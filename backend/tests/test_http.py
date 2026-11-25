from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import rest
from dispatch import allocator, demo, operations
from dispatch.auth import COOKIE_PREFIX, issue
from dispatch.config import now, settings
from dispatch.db import Session
from dispatch.models import Membership, Workspace
from dispatch.routing import RouteUnavailable, fixture_route
from fixtures.alandi import PICKUP, POSITIONS

ORIGIN = "http://localhost:3000"


@pytest.fixture(scope="module")
def client():
    with TestClient(rest, headers={"Origin": ORIGIN}) as c:
        yield c


def enter(client, code, role):
    response = client.post("/api/auth/demo", json={"code": code, "role": role})
    assert response.status_code == 200, response.text
    return response.json()


def headers(tokens, key=None):
    return {"Authorization": "Bearer " + tokens["access_token"],
            **({"Idempotency-Key": key} if key else {})}


def test_workspace_and_role_isolation_reset_and_jwt(client):
    codes = [client.post("/api/demo").json()["code"] for _ in range(2)]
    patient, dispatch, alt_patient, nurse = [
        enter(client, code, role)
        for code, role in [(codes[0], "patient"), (codes[0], "dispatcher"), (codes[1], "patient"), (codes[0], "nurse")]
    ]
    created = client.post("/api/incidents", headers=headers(patient, "valid-sos-key"),
                          json={"lat": PICKUP[0], "lng": PICKUP[1], "pickup_name": "Alandi", "source": "alandi"})
    assert created.status_code == 201
    assert client.get("/api/queue", headers=headers(patient)).status_code == 403
    assert client.get("/api/forecast", headers=headers(patient)).status_code == 403
    assert client.get("/api/dashboard", headers=headers(alt_patient)).json()["incidents"] == []
    assert client.get("/api/events?after=0", headers=headers(alt_patient)).json()["events"] == []
    case = client.get("/api/dashboard", headers=headers(dispatch)).json()["incidents"][0]
    assert case["mission"]["unit"] == "AMB3"
    assert client.post(f"/api/missions/{case['mission']['id']}/intake",
                       headers=headers(patient, "bad-intake-key"),
                       json={"alias": "Fictional", "issue": "accident"}).status_code == 403
    assert client.post("/api/demo/reset", headers=headers(nurse)).status_code == 403
    assert client.post("/api/demo/reset", headers=headers(dispatch)).status_code == 200
    assert client.get("/api/dashboard", headers=headers(dispatch)).json()["incidents"] == []
    assert client.get("/api/dashboard", headers=headers(alt_patient)).json()["generation"] == 0
    assert client.get("/api/session", headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.get("/api/health").json()["redis"]
    assert "/api/missions/{mission_id}/intake" in client.get("/api/openapi.json").json()["paths"]


def test_cookie_origin_rotation_logout_and_secure_flags(client, monkeypatch):
    code = client.post("/api/demo").json()["code"]
    assert client.post("/api/auth/demo", headers={"Origin": "https://other.example"},
                       json={"code": code, "role": "patient"}).status_code == 403
    account = enter(client, code, "patient")
    cookie = COOKIE_PREFIX + account["slot"]
    old_cookie = client.cookies[cookie]
    refreshed = client.post("/api/auth/refresh", json={"slot": account["slot"]})
    assert refreshed.status_code == 200
    assert client.cookies[cookie] != old_cookie
    assert "HttpOnly" in refreshed.headers["Set-Cookie"] and "SameSite=strict" in refreshed.headers["Set-Cookie"]
    denied = client.post("/api/auth/refresh", json={"slot": account["slot"]},
                         headers={"Cookie": f"{cookie}={old_cookie}"})
    assert denied.status_code == 401
    assert client.post("/api/auth/logout", json={"slot": account["slot"]}).status_code == 200
    assert client.get("/api/session", headers=headers(refreshed.json())).status_code == 401
    monkeypatch.setattr(settings, "hosted", True)
    secure = client.post("/api/auth/demo", json={"code": code, "role": "dispatcher"})
    assert secure.status_code == 200 and "Secure" in secure.headers["Set-Cookie"]


def test_connected_intake_waits_for_acceptance_and_validates_gps(client, monkeypatch):
    group = demo.create_group()["code"]
    ids = {role: enter(client, group, role) for role in ("dispatcher", "driver", "nurse", "hospital")}
    workspace_id = client.get("/api/session", headers=headers(ids["dispatcher"])).json()["workspace_id"]
    with Session.begin() as db:
        workspace = db.get(Workspace, workspace_id)
        workspace.mode = "connected"
        ws_slug = workspace.slug
    monkeypatch.setattr(settings, "mode", "connected")
    registered = client.post("/api/auth/register", json={
        "workspace": ws_slug, "email": workspace_id + "@example.invalid",
        "password": "test passphrase only 573", "name": "Fictional person",
    })
    assert registered.status_code == 201
    patient = registered.json()
    assert client.get("/api/session", headers=headers(patient)).json()["role"] == "patient"
    assert client.post("/api/auth/register", json={
        "workspace": ws_slug, "email": "test-staff@example.invalid", "password": "fake password",
        "name": "Staff", "role": "dispatcher",
    }).status_code == 422

    created = client.post("/api/incidents", headers=headers(patient, "connected-sos"),
                          json={"lat": PICKUP[0], "lng": PICKUP[1], "pickup_name": "Example connected pickup"})
    assert created.status_code == 201
    c_id = created.json()["id"]
    dispatch = headers(ids["dispatcher"])
    case = client.get("/api/dashboard", headers=dispatch).json()["incidents"][0]
    second_patient = client.post("/api/auth/register", json={
        "workspace": ws_slug, "email": "other-" + workspace_id + "@example.invalid",
        "password": "second test passphrase 548", "name": "Another fictional person",
    }).json()
    assert client.get("/api/dashboard", headers=headers(second_patient)).json()["incidents"] == []
    assert case["status"] == "queued"
    assert all("no routing key" in row["reason"] for row in case["candidates"])
    unit = next(row for row in case["candidates"] if row["name"] == "AMB3")
    assigned = client.post(f"/api/incidents/{c_id}/dispatch", headers=dispatch,
                           json={"unit_id": unit["id"], "reason": "Confirmed by phone",
                                 "manual_eta_seconds": 300})
    assert assigned.status_code == 200, assigned.text
    mid = assigned.json()["mission_id"]
    mpath = f"/api/missions/{mid}"
    assert "Dispatcher estimate" in client.get("/api/dashboard", headers=dispatch).json()["incidents"][0]["mission"]["route"]["source"]

    class ProviderFixture:
        def routes(self, _mode, origin, target, at, alternate=False):
            return [fixture_route(origin, target, at, alternate)]

    monkeypatch.setattr(allocator, "router", ProviderFixture())
    monkeypatch.setattr(operations, "router", ProviderFixture())

    def action(role, action, expected=200):
        response = client.post(mpath + "/transition", headers=headers(ids[role]), json={"action": action})
        assert response.status_code == expected, response.text

    selected = client.put(mpath + "/destination", headers=dispatch, json={"reason": "Call the receiving ward"})
    assert selected.json()["destination_status"] == "pending"
    action("driver", "depart_pickup", 409)
    action("hospital", "acknowledge")
    action("hospital", "release")
    action("driver", "depart_pickup")
    t = now()
    base = {"lat": POSITIONS["ALANDI"][0], "lng": POSITIONS["ALANDI"][1], "accuracy_m": 15}
    location = client.post(mpath + "/location", headers=headers(ids["driver"]),
                           json={**base, "observed_at": t.isoformat()})
    assert location.json()["accepted"]
    old = client.post(mpath + "/location", headers=headers(ids["driver"]),
                      json={**base, "observed_at": (t - timedelta(seconds=2)).isoformat()})
    assert old.status_code == 409 and old.json()["code"] == "stale_observation"
    bad = client.post(mpath + "/location", headers=headers(ids["driver"]),
                      json={**base, "accuracy_m": 9000, "observed_at": t.isoformat()})
    assert bad.status_code == 422

    class ProviderUnavailable:
        def routes(self, *_args, **_kwargs):
            raise RouteUnavailable("TomTom timed out.")

    monkeypatch.setattr(operations, "router", ProviderUnavailable())
    check = client.post(mpath + "/route", headers=headers(ids["driver"]))
    assert check.status_code == 200 and check.json()["changed"] is False
    retained = client.get("/api/dashboard", headers=dispatch).json()["incidents"][0]["mission"]
    assert "last-known route" in retained["routing_issue"]
    assert retained["route"]["version"] == 1 and retained["route"]["calculated_at"]
    action("driver", "arrive_pickup")
    intake = client.post(mpath + "/intake", headers=headers(ids["nurse"], "test-intake-key"),
                         json={"alias": "Only for the assigned ward", "issue": "reported chest pain"})
    assert intake.json()["status"] == "at_patient" and intake.json()["destination_status"] == "pending"
    action("driver", "depart_hospital", 409)
    accepted = client.post(mpath + "/destination", headers=headers(ids["hospital"]), json={"decision": "accept"})
    assert accepted.status_code == 200, accepted.text
    after = client.get("/api/dashboard", headers=headers(ids["driver"])).json()["incidents"][0]
    assert after["status"] == "at_patient" and after["mission"]["route"]["leg"] == "receiving"
    action("driver", "depart_hospital")
    action("driver", "arrive_hospital")


def test_hospital_feed_opt_in_and_scope(client, monkeypatch):
    code = demo.create_group()["code"]
    h = enter(client, code, "hospital")
    ident = client.get("/api/session", headers=headers(h)).json()
    with Session.begin() as db:
        ws = db.get(Workspace, ident["workspace_id"])
        ws.mode = "connected"
        base = db.get(Membership, ident["id"])
        feed = Membership(user_id=base.user_id, workspace_id=ws.id,
                          role="feed", hospital_id=base.hospital_id)
        db.add(feed)
        db.flush()
        token = issue(db, feed)[0]
    monkeypatch.setattr(settings, "mode", "connected")
    path = f"/api/integrations/hospitals/{ident['hospital_id']}/capacity"
    body = {"total": 12, "occupied": 5, "accepting": True, "source": "Example test feed"}
    assert client.put(path, headers=headers(token, "test-feed-off"), json=body).status_code == 403
    from dispatch.models import Hospital
    with Session.begin() as db:
        hospital = db.get(Hospital, ident["hospital_id"])
        hospital.feed_enabled = True
        other = db.scalar(select(Hospital).where(Hospital.workspace_id == ident["workspace_id"],
                                                Hospital.code == "RUBY"))
        other_id = other.id
    accepted = client.put(path, headers=headers(token, "test-feed-on"), json=body)
    assert accepted.status_code == 200 and accepted.json()["available"] == 7
    assert client.put(path, headers=headers(token, "test-feed-on"), json=body).json() == accepted.json()
    assert client.put(f"/api/integrations/hospitals/{other_id}/capacity", headers=headers(token, "test-feed-denied"),
                      json=body).status_code == 403
