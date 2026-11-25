import asyncio
import logging
from hmac import compare_digest
from typing import Annotated

import redis.asyncio as redis
import socketio
from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError, TimeoutError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware

from dispatch import demo, operations, views
from dispatch.allocator import allocate
from dispatch.auth import (
    COOKIE_PATH,
    COOKIE_PREFIX,
    DUMMY_PASSWORD,
    Actor,
    digest,
    issue,
    passwords,
    resolve,
)
from dispatch.config import now, settings
from dispatch.db import Session
from dispatch.errors import Problem, require
from dispatch.journal import actor_view, write
from dispatch.models import AuthSession, Membership, Notification, User, Workspace, uid
from dispatch.schemas import (
    GPS,
    SOS,
    AccessSession,
    ApiError,
    CapacityReport,
    DemoEntry,
    Destination,
    DestinationSelection,
    Dispatch,
    EventsPage,
    Health,
    IntakeSubmission,
    Login,
    Reason,
    Register,
    Review,
    Slot,
    Transition,
    UnitUpdate,
)
from fixtures.alandi import PICKUP, PICKUP_NAME, SCHEMATIC
from ml.forecast import dashboard_forecast

log = logging.getLogger("dispatch")
manager_options = {"socket_connect_timeout": 2, "health_check_interval": 20}
if settings.redis_url.startswith("rediss://"):
    manager_options["ssl_cert_reqs"] = "required"
manager = socketio.AsyncRedisManager(
    settings.redis_url, channel=f"{settings.namespace}:sio", redis_options=manager_options,
)
socket_expiries: dict[str, asyncio.TimerHandle] = {}
sio = socketio.AsyncServer(async_mode="asgi", client_manager=manager, transports=["websocket"],
                           allow_upgrades=False, cors_allowed_origins=settings.origins,
                           ping_interval=25, ping_timeout=15, max_http_buffer_size=16000)
rest = FastAPI(title="Smart Ambulance Dispatch API", version="1.0.0", docs_url="/api/docs",
               openapi_url="/api/openapi.json", redoc_url=None,
               swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect")
rest.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True,
                    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
                    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])
api = APIRouter(prefix="/api", responses={
    401: {"model": ApiError, "description": "Expired credentials or invalid session"},
    403: {"model": ApiError, "description": "Role, scope, or origin denied"},
    404: {"model": ApiError, "description": "Record unavailable in this workspace"},
    409: {"model": ApiError, "description": "State, capacity, queue order, or idempotency conflict"},
    422: {"model": ApiError, "description": "Invalid input or observation"},
})
bearer = HTTPBearer(auto_error=False)
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100,
                             description="Repeat this value when retrying the same command.")]


@rest.exception_handler(Problem)
async def domain_error(_request, exc):
    return JSONResponse({"code": exc.code, "detail": exc.message}, status_code=exc.status)


@rest.exception_handler(RequestValidationError)
async def validation_error(_request, exc):
    location = ".".join(str(item) for item in exc.errors()[0]["loc"])
    return JSONResponse({"code": "invalid_input", "detail": f"Check {location}."}, status_code=422)


@rest.exception_handler(IntegrityError)
async def integrity_error(_request, _exc):
    return JSONResponse({"code": "conflict", "detail": "A concurrent change conflicted. Refresh and retry."},
                        status_code=409)


@rest.exception_handler(OperationalError)
@rest.exception_handler(TimeoutError)
async def storage_error(_request, _exc):
    return JSONResponse({"code": "storage_unavailable", "detail": "Storage is temporarily unavailable."},
                        status_code=503)


@rest.middleware("http")
async def request_policy(request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        cookie_action = request.url.path.startswith(("/api/auth/", "/api/demo"))
        if (origin is not None and origin not in settings.origins) or (
            cookie_action and origin not in settings.origins
        ):
            return JSONResponse({"code": "origin_denied", "detail": "This origin is not allowed."}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


def get_actor(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> Actor:
    require(credentials, "Sign in to continue.", "missing_access", 401)
    with Session() as db:
        return resolve(db, credentials.credentials)


Authed = Annotated[Actor, Depends(get_actor)]


async def deliver(items):
    async def send(item):
        await sio.emit("domain_event", item.payload, room=f"member:{item.member_id}")

    if not items:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*(send(item) for item in items)), timeout=2.5)
    except (asyncio.TimeoutError, OSError):
        log.warning("Live delivery delayed. Durable event replay is available.")


async def change(operation, *args, **kwargs):
    result, items = await run_in_threadpool(operation, *args, **kwargs)
    await deliver(items)
    return result


async def retry_queue(actor, limit=1):
    for _ in range(limit):
        result = await change(allocate, actor.workspace_id)
        if result["status"] != "assigned":
            break
    return result


def cookie(response: Response, session_result):
    tokens, raw = session_result
    response.set_cookie(
        COOKIE_PREFIX + tokens["slot"], raw, max_age=settings.refresh_seconds,
        httponly=True, secure=settings.hosted, samesite="strict", path=COOKIE_PATH,
    )
    return tokens


@api.get("", tags=["System"], include_in_schema=False)
@api.get("/health", tags=["System"], response_model=Health, summary="Check durable database and native Redis connectivity")
async def health(response: Response):
    def db_ping():
        with Session() as db:
            db.execute(text("SELECT 1 FROM workspaces LIMIT 1"))

    async def redis_ping():
        async with redis.from_url(settings.redis_url, **manager_options, socket_timeout=2) as client:
            return await client.ping()

    results = await asyncio.gather(run_in_threadpool(db_ping), redis_ping(), return_exceptions=True)
    good = [not isinstance(item, BaseException) for item in results]
    if not all(good):
        response.status_code = 503
    return {"status": "ok" if all(good) else "degraded", "database": good[0], "redis": good[1],
            "mode": settings.mode, "socket_path": "/api/socket.io"}


@api.get("/meta", tags=["System"], summary="Public workspace mode and schematic fixture")
def meta():
    return {"mode": settings.mode, "timezone": "Asia/Kolkata",
            "pickup": {"name": PICKUP_NAME, "position": PICKUP} if settings.mode == "demo" else None,
            "schematic": SCHEMATIC if settings.mode == "demo" else None}


@api.post("/demo", tags=["Training"], status_code=201, summary="Create an isolated training group")
def create_demo():
    return demo.create_group()


@api.post("/auth/demo", tags=["Authentication"], response_model=AccessSession, summary="Enter a training role using the private group link")
def demo_auth(body: DemoEntry, response: Response):
    return cookie(response, demo.enter(body))


@api.post("/demo/reset", tags=["Training"], summary="Reset this dispatcher’s training group")
async def reset_demo(actor: Authed):
    return await change(demo.reset, actor)


@api.post("/auth/login", tags=["Authentication"], response_model=AccessSession, summary="Sign in with a provisioned membership")
def login(body: Login, response: Response):
    require(settings.mode == "connected", "Use a training role in this environment.", "forbidden", 403)
    with Session.begin() as db:
        row = db.execute(select(Membership, User).join(User, User.id == Membership.user_id).join(
            Workspace, Workspace.id == Membership.workspace_id,
        ).where(Workspace.slug == body.workspace, Workspace.mode == "connected",
                User.email == body.email.lower(), Membership.role == body.role, Membership.enabled.is_(True))).first()
        valid = passwords.verify(body.password, row[1].password_hash if row and row[1].password_hash else DUMMY_PASSWORD)
        require(row and row[1].password_hash and valid,
                "The workspace, role, or credentials did not match.", "invalid_login", 401)
        return cookie(response, issue(db, row[0]))


@api.post("/auth/register", tags=["Authentication"], response_model=AccessSession, status_code=201, summary="Self-register a patient")
def register(body: Register, response: Response):
    require(settings.mode == "connected", "Use a training role in this environment.", "forbidden", 403)
    hashed = passwords.hash(body.password)
    with Session.begin() as db:
        workspace = db.scalar(select(Workspace).where(Workspace.slug == body.workspace, Workspace.mode == "connected"))
        require(workspace, "The connected workspace was not found.", "not_found", 404)
        require(not db.scalar(select(User.id).where(User.email == body.email.lower())),
                "This email is already registered.", "email_exists")
        user = User(id=uid(), email=body.email.lower(), name=body.name, password_hash=hashed)
        db.add(user)
        db.flush()
        member = Membership(id=uid(), user_id=user.id, workspace_id=workspace.id, role="patient")
        db.add(member)
        db.flush()
        return cookie(response, issue(db, member))


@api.post("/auth/refresh", tags=["Authentication"], response_model=AccessSession, summary="Rotate a tab’s HttpOnly refresh cookie")
def refresh(body: Slot, request: Request, response: Response):
    raw = request.cookies.get(COOKIE_PREFIX + body.slot)
    require(raw, "The refresh session is missing.", "invalid_refresh", 401)
    with Session.begin() as db:
        current = db.scalar(select(AuthSession).where(AuthSession.id == body.slot).with_for_update())
        require(current and not current.revoked and current.expires_at > now()
                and compare_digest(digest(raw), current.token_hash), "The refresh session has expired.", "invalid_refresh", 401)
        member = db.get(Membership, current.member_id)
        require(member.enabled and db.get(Workspace, member.workspace_id).mode == settings.mode,
                "This membership is disabled.", "invalid_refresh", 401)
        return cookie(response, issue(db, member, current))


@api.post("/auth/logout", tags=["Authentication"], summary="Revoke this tab’s refresh session")
def logout(body: Slot, request: Request, response: Response):
    raw = request.cookies.get(COOKIE_PREFIX + body.slot)
    if raw:
        with Session.begin() as db:
            row = db.scalar(select(AuthSession).where(AuthSession.id == body.slot).with_for_update())
            if row and compare_digest(digest(raw), row.token_hash):
                row.revoked = True
    response.delete_cookie(COOKIE_PREFIX + body.slot, path=COOKIE_PATH, secure=settings.hosted,
                           httponly=True, samesite="strict")
    return {"signed_out": True}


@api.get("/session", tags=["Authentication"], summary="Current server-authorized membership")
def session(actor: Authed):
    return actor_view(actor)


@api.get("/dashboard", tags=["Workspace"], summary="Fetch a role-scoped snapshot and durable sequence")
def dashboard(actor: Authed):
    return views.snapshot(actor)


@api.get("/metrics", tags=["Workspace"], summary="Stored fleet, queue and arrival metrics")
def metrics(actor: Authed):
    return views.snapshot(actor)["metrics"]


@api.get("/forecast", tags=["Workspace"], summary="Next-hour demand forecast and last observed hour")
def forecast(actor: Authed):
    return dashboard_forecast(actor)


@api.get("/queue", tags=["Dispatch"], summary="Dispatcher’s priority-ordered queue")
def queue(actor: Authed):
    require(actor.role == "dispatcher", "Dispatcher access required.", "forbidden", 403)
    snapshot = views.snapshot(actor)
    incidents = {case["id"]: case for case in snapshot["incidents"]}
    return [incidents[key] for key in snapshot["queue"]]


@api.post("/incidents", tags=["Dispatch"], status_code=201,
          summary="Store a patient SOS or dispatcher phone request before attempting assignment")
async def post_incident(body: SOS, actor: Authed, key: Key):
    """Patients can create an SOS for themselves. Dispatchers can record phone requests and confirm priority. Replay the same Idempotency-Key with the same body."""
    result = await change(operations.submit_sos, actor, body, key)
    await retry_queue(actor)
    return result


@api.put("/incidents/{incident_id}/priority", tags=["Dispatch"], summary="Confirm a queued incident’s operational priority")
async def priority(incident_id: str, body: Review, actor: Authed):
    result = await change(operations.review, actor, incident_id, body)
    await retry_queue(actor)
    return result


@api.post("/incidents/{incident_id}/dispatch", tags=["Dispatch"],
          summary="Dispatch the first queued incident or reassign a unit before departure")
async def dispatch(incident_id: str, body: Dispatch, actor: Authed):
    require(actor.role == "dispatcher", "Dispatcher access required.", "forbidden", 403)
    result = await change(allocate, actor.workspace_id, requested_id=incident_id,
                          preferred_id=body.unit_id, reason=body.reason,
                          manual_eta=body.manual_eta_seconds, actor=actor)
    if result["status"] == "assigned":
        await retry_queue(actor)
    return result


@api.post("/incidents/{incident_id}/cancel", tags=["Dispatch"], summary="Cancel a queued request")
async def cancel(incident_id: str, actor: Authed, reason: Reason = Query()):
    result = await change(operations.cancel_queued, actor, incident_id, reason)
    await retry_queue(actor)
    return result


@api.post("/missions/{mission_id}/transition", tags=["Missions"],
          summary="Release, depart, arrive, hand over, or cancel a mission")
async def transition(mission_id: str, body: Transition, actor: Authed,
                     key: Annotated[str | None, Header(alias="Idempotency-Key", min_length=8, max_length=100)] = None):
    require(key or body.action != "handover", "Handover requires an Idempotency-Key header.",
            "idempotency_required", 422)
    result = await change(operations.transition, actor, mission_id, body, key)
    if body.action in ("handover", "cancel"):
        await retry_queue(actor)
    return result


@api.post("/missions/{mission_id}/intake", tags=["Missions"], summary="Save assigned nurse intake exactly once")
async def intake(mission_id: str, body: IntakeSubmission, actor: Authed, key: Key):
    """Assigned nurse only. The mission must be at the pickup. A repeated key returns the initial successful save."""
    return await change(operations.save_intake, actor, mission_id, body, key)


@api.post("/missions/{mission_id}/destination", tags=["Hospitals"], summary="Accept or decline your hospital’s inbound request")
async def destination(mission_id: str, body: Destination, actor: Authed):
    """Receiving hospital only. Acceptance requires fresh capacity and a free bed. Include a reason when declining."""
    return await change(operations.decide_destination, actor, mission_id, body)


@api.put("/missions/{mission_id}/destination", tags=["Hospitals"], summary="Select or retry an eligible receiving hospital")
async def select_destination(mission_id: str, body: DestinationSelection, actor: Authed):
    return await change(operations.choose_destination, actor, mission_id, body)


@api.put("/hospitals/{hospital_id}/capacity", tags=["Hospitals"], summary="Update your hospital’s named capacity report")
async def capacity(hospital_id: str, body: CapacityReport, actor: Authed, key: Key):
    """Hospital staff can report their own total and occupied beds. Active reservations remain server-owned."""
    return await change(operations.report_capacity, actor, hospital_id, body, key)


@api.put("/integrations/hospitals/{hospital_id}/capacity", tags=["Hospitals"],
         summary="Submit a report using an enabled hospital-scoped feed membership")
async def integration(hospital_id: str, body: CapacityReport, actor: Authed, key: Key):
    """Use a feed-role access token scoped to this hospital. Hospital enrollment must explicitly enable its feed."""
    return await change(operations.report_capacity, actor, hospital_id, body, key, partner=True)


@api.put("/units/{unit_id}/availability", tags=["Dispatch"], summary="Check in or change availability of an idle unit")
async def availability(unit_id: str, body: UnitUpdate, actor: Authed):
    result = await change(operations.update_unit, actor, unit_id, body)
    await retry_queue(actor)
    return result


@api.post("/missions/{mission_id}/location", tags=["Missions"], summary="Update the assigned unit’s foreground GPS")
async def location(mission_id: str, body: GPS, actor: Authed):
    return await change(operations.record_gps, actor, mission_id, body)


@api.post("/missions/{mission_id}/route", tags=["Missions"], summary="Recalculate the current leg or inject fixture traffic")
async def route(mission_id: str, actor: Authed, inject: bool = False):
    return await change(operations.reroute, actor, mission_id, inject)


@api.post("/simulation/tick", tags=["Training"], summary="Catch up scheduled simulation steps using the persisted clock")
async def simulation(actor: Authed):
    return await change(operations.tick, actor)


@api.get("/events", tags=["Workspace"], response_model=EventsPage, response_model_exclude_none=True, summary="Replay authorized events following the last confirmed sequence")
def events(actor: Authed, after: Annotated[int, Query(ge=0)] = 0):
    return views.event_replay(actor, after)


@api.put("/notifications/{event_id}/read", tags=["Workspace"], summary="Mark your notification as read")
async def notify_read(event_id: str, actor: Authed):
    def mark(db, journal):
        item = db.get(Notification, (actor.id, event_id))
        require(item, "Notification not found.", "not_found", 404)
        if item.read_at:
            return {"read": True}
        item.read_at = now()
        event = journal.add("notification.read", "Notification marked read.", inbox=False, member_only=actor.id)
        return journal.change_result(event, read=True)
    return await change(write, actor, mark)


def resolve_token(token):
    require(isinstance(token, str), "Access token required.", "missing_access", 401)
    with Session() as db:
        return resolve(db, token)


@sio.event
async def connect(sid, environ, auth):
    origin = environ.get("HTTP_ORIGIN")
    if origin and origin not in settings.origins:
        return False
    token = auth.get("token") if isinstance(auth, dict) else None
    try:
        actor = await run_in_threadpool(resolve_token, token)
    except Problem:
        return False
    await sio.save_session(sid, {"token": token, "member_id": actor.id})
    await sio.enter_room(sid, f"member:{actor.id}")
    delay = max(0, actor.expiry - now().timestamp())
    socket_expiries[sid] = asyncio.get_running_loop().call_later(
        delay, lambda: asyncio.create_task(sio.disconnect(sid)),
    )


@sio.event
async def disconnect(sid, _reason):
    expiry = socket_expiries.pop(sid, None)
    if expiry:
        expiry.cancel()


@sio.event
async def subscribe(sid, request):
    session_data = await sio.get_session(sid)
    try:
        actor = await run_in_threadpool(resolve_token, session_data["token"])
        require(isinstance(request, dict) and request.get("room") == "self",
                "Only the authenticated membership room is available.", "forbidden", 403)
        await sio.enter_room(sid, f"member:{actor.id}")
        return {"ok": True}
    except Problem as exc:
        return {"ok": False, "code": exc.code, "detail": exc.message}


@sio.event
async def location_update(sid, request):
    session_data = await sio.get_session(sid)
    try:
        actor = await run_in_threadpool(resolve_token, session_data["token"])
        require(isinstance(request, dict) and isinstance(request.get("mission_id"), str),
                "Provide mission_id and fix.", "invalid_input", 422)
        result = await change(operations.record_gps, actor, request["mission_id"],
                              GPS.model_validate(request.get("fix")))
        return {"ok": True, **result}
    except ValidationError:
        return {"ok": False, "code": "invalid_input", "detail": "GPS fix is invalid."}
    except Problem as exc:
        return {"ok": False, "code": exc.code, "detail": exc.message}


rest.include_router(api)
app = socketio.ASGIApp(sio, other_asgi_app=rest, socketio_path="/api/socket.io")
