from datetime import timedelta

from sqlalchemy import select

from dispatch.allocator import (
    bases,
    capacity_issue,
    change_reservation,
    destination_routes,
    plan_duration,
    release_unit,
    router,
    save_route,
    scoped_incident,
    scoped_mission,
    select_target,
)
from dispatch.auth import check_role
from dispatch.config import now, settings
from dispatch.db import Session
from dispatch.errors import require
from dispatch.journal import Journal, idempotent, lock_workspace, participants, write
from dispatch.models import (
    ACTIVE,
    Capacity,
    Hospital,
    Incident,
    Intake,
    Mission,
    Position,
    Route,
    Unit,
    Workspace,
    uid,
)
from dispatch.routing import RouteUnavailable, progress_on_route, simulate_position
from fixtures.alandi import PICKUP, PICKUP_NAME, zone_for

AUTO_DEPART = 6
CANCELLABLE = {"assigned", "en_route_to_patient"}


def submit_sos(actor, body, key, clock=now):
    check_role(actor, "patient", "dispatcher")
    if actor.role == "patient":
        require(body.priority is None and body.unit_requirement is None and body.receiving_requirement is None
                and body.source != "phone", "Only staff set operational priority or requirements.", "forbidden", 403)
    if body.source == "alandi":
        require(actor.mode == "demo", "The Alandi pickup is available in training.", "forbidden", 403)

    def run(db, log):
        if actor.role == "patient":
            existing = db.scalar(select(Incident.id).where(
                Incident.patient_id == actor.id, Incident.status.in_((*ACTIVE, "queued")),
            ))
            require(not existing, "There is already an active SOS for this patient.", "active_sos")
        log.workspace.case_counter += 1
        lat, lng = PICKUP if body.source == "alandi" else (body.lat, body.lng)
        case = Incident(
            id=uid(), workspace_id=actor.workspace_id, code=f"ALD-{log.workspace.case_counter:04}",
            patient_id=actor.id if actor.role == "patient" else None,
            pickup_name=PICKUP_NAME if body.source == "alandi" else body.pickup_name,
            lat=lat, lng=lng, zone=zone_for(lat, lng), channel="sos" if actor.role == "patient" else "phone",
            reported_urgency=body.reported_urgency, priority=body.priority,
            unit_requirement=body.unit_requirement, receiving_requirement=body.receiving_requirement,
            status="queued", created_at=clock(),
        )
        db.add(case)
        db.flush()
        event = log.add("incident.queued", "Request received and added to the dispatch queue.",
                        incident=case, payload={"status": "queued", "code": case.code})
        return log.change_result(event, id=case.id, code=case.code, status="queued")

    return idempotent(actor, "sos", key, body.model_dump(), run, clock)


def review(actor, incident_id, body, clock=now):
    check_role(actor, "dispatcher")

    def run(db, log):
        incident = scoped_incident(db, actor.workspace_id, incident_id)
        require(incident.status == "queued", "Priority can be changed while a request is queued.")
        incident.priority = body.priority
        incident.unit_requirement = body.unit_requirement
        incident.receiving_requirement = body.receiving_requirement
        incident.version += 1
        event = log.add("incident.reviewed", "Dispatcher confirmed operational priority.",
                        incident=incident, payload={"priority": body.priority})
        log.note(body.reason, incident=incident)
        return log.change_result(event, status=incident.status)

    return write(actor, run, clock)


def _advance(db, log, incident, mission, status, message, at, reason=None):
    incident.status, mission.status = status, status
    incident.version += 1
    mission.updated_at = at
    if status == "at_patient":
        incident.arrived_at = at
    if status in ("en_route_to_patient", "transporting"):
        version = mission.pickup_version if status == "en_route_to_patient" else mission.receiving_version
        route = db.get(Route, (mission.id, version)) if version else None
        require(route, "A route must be recorded before departure.", "route_unavailable")
        mission.leg_started_at, mission.leg_seconds = at, plan_duration(log.workspace, route.eta_seconds)
    else:
        mission.leg_started_at, mission.leg_seconds = None, None
    if reason:
        log.note(reason, incident=incident, mission=mission)
    return log.add(f"mission.{status}", message, incident=incident, mission=mission,
                   payload={"status": status}, when=at)


def _begin_transport(db, log, incident, mission, at):
    if (mission.status == "at_patient" and mission.destination_status == "accepted"
            and mission.receiving_version and db.get(Intake, mission.id)):
        if log.workspace.mode == "demo":
            _advance(db, log, incident, mission, "transporting",
                     "Simulated ambulance started the receiving-hospital leg.", at)
        return True
    return False


def save_intake(actor, mission_id, body, key, clock=now):
    check_role(actor, "nurse")

    def run(db, log):
        mission = scoped_mission(db, actor.workspace_id, mission_id)
        require(mission.unit_id == actor.unit_id, "Intake is restricted to the assigned nurse.", "forbidden", 403)
        require(mission.status == "at_patient" and not db.get(Intake, mission.id),
                "Intake can be saved once while the ambulance is at the pickup.")
        incident = db.get(Incident, mission.incident_id)
        db.add(Intake(mission_id=mission.id, author_id=actor.id, created_at=clock(), **body.model_dump()))
        db.flush()
        event = log.add("intake.saved", "Nurse intake saved.", incident=incident, mission=mission)
        _begin_transport(db, log, incident, mission, clock())
        return log.change_result(event, status=mission.status, destination_status=mission.destination_status)

    return idempotent(actor, f"intake:{mission_id}", key, body.model_dump(), run, clock)


def transition(actor, mission_id, body, key=None, clock=now):
    action = body.action
    roles = {
        "acknowledge": ("hospital",), "release": ("hospital",), "handover": ("hospital",),
        "depart_pickup": ("driver",), "arrive_pickup": ("driver",),
        "depart_hospital": ("driver",), "arrive_hospital": ("driver",), "cancel": ("dispatcher", "patient"),
    }
    check_role(actor, *roles[action])

    def run(db, log):
        mission = scoped_mission(db, actor.workspace_id, mission_id)
        incident = db.get(Incident, mission.incident_id)
        require(participants(actor, incident, mission), "This mission is outside your assignment.", "forbidden", 403)
        at = clock()
        if action in ("acknowledge", "release"):
            require(actor.hospital_id == mission.sending_id, "Only the sending hospital can release this unit.",
                    "forbidden", 403)
            require(mission.status == "assigned", "The unit has already left its base.")
            if (action == "acknowledge" and mission.acknowledged) or (action == "release" and mission.released):
                return {"status": mission.status, "id": mission.id}
            if action == "acknowledge":
                mission.acknowledged = True
                msg = "Sending hospital acknowledged the assignment."
            else:
                require(mission.acknowledged, "Acknowledge the assignment before releasing the unit.")
                mission.released = True
                msg = "Sending hospital released the assigned ambulance."
            event = log.add(f"unit.{action}", msg, incident=incident, mission=mission)
        elif action == "handover":
            require(actor.hospital_id == mission.receiving_id,
                    "Only the receiving hospital can complete handover.", "forbidden", 403)
            require(mission.status == "at_hospital", "Confirm hospital arrival before handover.")
            change_reservation(db, mission, "occupy", at)
            release_unit(db, mission)
            event = _advance(db, log, incident, mission, "completed",
                             "Receiving hospital confirmed handover. Ambulance available.", at)
        elif action == "cancel":
            require(body.reason, "Provide a cancellation reason.", "reason_required")
            require(mission.status in CANCELLABLE and (actor.role == "dispatcher" or mission.status == "assigned"),
                    "This mission cannot be cancelled at the current stage.")
            change_reservation(db, mission, "release", at)
            release_unit(db, mission)
            event = _advance(db, log, incident, mission, "cancelled", "Mission cancelled.", at, body.reason)
        else:
            expected, status, message = {
                "depart_pickup": ("assigned", "en_route_to_patient", "Ambulance departed for the pickup."),
                "arrive_pickup": ("en_route_to_patient", "at_patient", "Ambulance arrived at the pickup."),
                "depart_hospital": ("at_patient", "transporting", "Ambulance departed for the receiving hospital."),
                "arrive_hospital": ("transporting", "at_hospital", "Ambulance arrived at the receiving hospital."),
            }[action]
            if mission.status == status:
                return {"status": status, "id": mission.id}
            require(mission.status == expected, "This transition is not available at the current stage.")
            if action == "depart_pickup":
                require(mission.released, "The sending hospital must release this unit.")
            if action == "depart_hospital":
                require(db.get(Intake, mission.id) and mission.destination_status == "accepted",
                        "Both intake and the hospital's acceptance are required.")
            event = _advance(db, log, incident, mission, status, message, at)
        return log.change_result(event, id=mission.id, status=mission.status)

    if key:
        return idempotent(actor, f"transition:{mission_id}:{action}", key, body.model_dump(), run, clock)
    return write(actor, run, clock)


def cancel_queued(actor, incident_id, reason, clock=now):
    check_role(actor, "dispatcher", "patient")

    def run(db, log):
        incident = scoped_incident(db, actor.workspace_id, incident_id)
        require(participants(actor, incident, None), "Incident not found.", "not_found", 404)
        require(incident.status == "queued", "Only a queued request can be cancelled here.")
        incident.status, incident.version = "cancelled", incident.version + 1
        log.note(reason, incident=incident)
        event = log.add("incident.cancelled", "Queued request cancelled.", incident=incident)
        return log.change_result(event, status=incident.status)

    return write(actor, run, clock)


def report_capacity(actor, hospital_id, body, key, partner=False, clock=now):
    check_role(actor, "feed" if partner else "hospital")

    def run(db, log):
        require(actor.hospital_id == hospital_id, "This capacity feed belongs to another hospital.", "forbidden", 403)
        hospital = db.get(Hospital, hospital_id)
        require(hospital and hospital.workspace_id == actor.workspace_id
                and (not partner or hospital.feed_enabled), "The partner capacity feed is disabled.", "forbidden", 403)
        capacity = db.get(Capacity, hospital_id)
        if capacity is None:
            capacity = Capacity(hospital_id=hospital_id, reserved=0, version=0)
            db.add(capacity)
        at = body.reported_at or clock()
        require(at <= clock() + timedelta(seconds=30), "Capacity report time is in the future.",
                "invalid_observation", 422)
        require(capacity.reported_at is None or at >= capacity.reported_at,
                "A more recent capacity report is already stored.", "stale_observation")
        require(body.occupied + capacity.reserved <= body.total,
                "Occupied beds and active reservations exceed total capacity.", "capacity_conflict")
        capacity.total, capacity.occupied, capacity.accepting = body.total, body.occupied, body.accepting
        capacity.source, capacity.reported_at, capacity.version = body.source, at, capacity.version + 1
        event = log.add("capacity.updated", f"Capacity updated at {hospital.name}.", hospitals=(hospital.id,))
        return log.change_result(event, reserved=capacity.reserved, available=body.total - body.occupied - capacity.reserved)

    return idempotent(actor, f"capacity:{hospital_id}", key, body.model_dump(), run, clock)


def decide_destination(actor, mission_id, body, clock=now):
    check_role(actor, "hospital")

    def run(db, log):
        mission = scoped_mission(db, actor.workspace_id, mission_id)
        require(mission.receiving_id == actor.hospital_id, "This destination was assigned to another hospital.",
                "forbidden", 403)
        require(mission.status in {"assigned", "en_route_to_patient", "at_patient"},
                "Coordinate destination changes with the dispatcher after departure.")
        incident = db.get(Incident, mission.incident_id)
        if body.decision == "accept":
            if mission.destination_status == "accepted":
                return {"status": mission.status, "destination_status": "accepted"}
            issue = capacity_issue(db.get(Hospital, actor.hospital_id), db.get(Capacity, actor.hospital_id),
                                   incident.receiving_requirement, clock())
            require(not issue, issue, "destination_unavailable")
            change_reservation(db, mission, "reserve", clock())
            mission.destination_status = "accepted"
            event = log.add("destination.accepted", "Receiving hospital accepted and reserved a bed.",
                            incident=incident, mission=mission)
            _begin_transport(db, log, incident, mission, clock())
        else:
            require(body.reason, "Provide a reason for declining the destination.", "reason_required")
            change_reservation(db, mission, "release", clock())
            mission.declined = list({*mission.declined, mission.receiving_id})
            mission.receiving_id, mission.destination_status, mission.receiving_version = None, "coordination", None
            event = log.add("destination.declined", "Receiving hospital declined. Dispatcher coordination needed.",
                            incident=incident, mission=mission, hospitals=(actor.hospital_id,))
            log.note(body.reason, incident=incident, mission=mission)
        return log.change_result(event, status=mission.status, destination_status=mission.destination_status)

    return write(actor, run, clock)


def choose_destination(actor, mission_id, body, clock=now, service=None):
    check_role(actor, "dispatcher")
    service = service or router
    with Session() as db:
        revision = db.get(Workspace, actor.workspace_id).seq
        mission = scoped_mission(db, actor.workspace_id, mission_id)
        allowed = ("assigned", "en_route_to_patient", "at_patient")
        require(mission.status in allowed, "Destination changes are available before transport starts.")
        incident = db.get(Incident, mission.incident_id)
        hospital_rows = bases(db, actor.workspace_id)
        if body.hospital_id:
            require(any(h.id == body.hospital_id for h, _ in hospital_rows),
                    "Hospital not found.", "not_found", 404)
    at = clock()
    declined = mission.declined
    if body.hospital_id:
        require(body.hospital_id not in declined, "This hospital already declined the mission.")
    plans = destination_routes(actor.mode, incident, hospital_rows, mission.sending_id, declined,
                               at, service, body.hospital_id,
                               mission.receiving_id if mission.destination_status == "accepted" else None)
    require(not body.hospital_id or plans[1][0], "This destination is unavailable.", "destination_unavailable")

    def run(db, log):
        require(log.workspace.seq == revision, "Availability changed. Retry destination selection.", "retry")
        current = scoped_mission(db, actor.workspace_id, mission_id)
        chosen = plans[1][0]
        if chosen and current.receiving_id == chosen["id"] and current.destination_status == "accepted":
            log.note(body.reason, incident=incident, mission=current)
            return {"status": current.status, "destination_status": current.destination_status}
        change_reservation(db, current, "release", clock())
        select_target(db, log, db.get(Incident, current.incident_id), current, *plans, clock())
        _begin_transport(db, log, incident, current, clock())
        log.note(body.reason, incident=incident, mission=current)
        return {"status": current.status, "destination_status": current.destination_status}

    return write(actor, run, clock)


def update_unit(actor, unit_id, body, clock=now):
    check_role(actor, "dispatcher", "hospital")

    def run(db, log):
        unit = db.get(Unit, unit_id)
        require(unit and unit.workspace_id == actor.workspace_id, "Unit not found.", "not_found", 404)
        require(actor.role == "dispatcher" or actor.hospital_id == unit.hospital_id,
                "This unit belongs to another hospital.", "forbidden", 403)
        require(unit.status != "busy", "Complete or cancel the mission before changing unit availability.")
        unit.status, unit.checked_in, unit.version = body.status, body.checked_in, unit.version + 1
        if body.checked_in:
            fix = db.get(Position, unit.id)
            if fix:
                db.delete(fix)
        event = log.add("unit.availability", f"{unit.name} availability updated.",
                        hospitals=(unit.hospital_id,), payload={"status": unit.status})
        log.note(body.reason)
        return log.change_result(event, status=unit.status)

    return write(actor, run, clock)


def record_gps(actor, mission_id, fix, clock=now):
    check_role(actor, "driver")
    require(actor.mode == "connected", "Training positions come from the simulation.", "forbidden", 403)

    def run(db, log):
        mission = scoped_mission(db, actor.workspace_id, mission_id)
        require(actor.unit_id == mission.unit_id, "This driver is assigned to another unit.", "forbidden", 403)
        require(mission.status in ACTIVE, "The mission is closed.")
        require(clock() - timedelta(seconds=settings.gps_ttl) <= fix.observed_at <= clock() + timedelta(seconds=20),
                "GPS fix is too old or in the future.", "invalid_observation", 422)
        position = db.get(Position, mission.unit_id)
        if position:
            require(fix.observed_at > position.observed_at, "A newer GPS fix is already stored.", "stale_observation")
            if (fix.observed_at - position.observed_at).total_seconds() < 4:
                return {"accepted": False}
        else:
            position = Position(unit_id=mission.unit_id)
            db.add(position)
        position.lat, position.lng = fix.lat, fix.lng
        position.accuracy_m, position.observed_at = fix.accuracy_m, fix.observed_at
        unit = db.get(Unit, mission.unit_id)
        unit.checked_in, unit.version = False, unit.version + 1
        event = log.add("unit.location", "Unit location refreshed.", inbox=False, mission=mission)
        return log.change_result(event, accepted=True)

    return write(actor, run, clock)


def simulation_due(mission, at):
    if mission.status == "assigned":
        return mission.created_at + timedelta(seconds=AUTO_DEPART) if mission.released else None
    if mission.status in ("en_route_to_patient", "transporting") and mission.leg_started_at and mission.leg_seconds:
        return mission.leg_started_at + timedelta(seconds=mission.leg_seconds)
    return None


def tick(actor, clock=now):
    if actor.mode != "demo":
        return {"changed": False}, []
    at = clock()
    with Session() as db:
        active = db.scalars(select(Mission).where(
            Mission.workspace_id == actor.workspace_id,
            Mission.status.in_(("assigned", "en_route_to_patient", "transporting")),
        )).all()
        due = [m.id for m in active if simulation_due(m, at) and simulation_due(m, at) <= at]
    if not due:
        return {"changed": False}, []
    with Session.begin() as db:
        workspace = lock_workspace(db, actor.workspace_id)
        log = Journal(db, workspace, clock)
        for mission_id in due:
            mission = db.get(Mission, mission_id)
            incident = db.get(Incident, mission.incident_id)
            deadline = simulation_due(mission, at)
            while deadline and deadline <= at:
                status, message = {
                    "assigned": ("en_route_to_patient", "Simulated ambulance departed for pickup."),
                    "en_route_to_patient": ("at_patient", "Simulated ambulance arrived. Nurse intake is ready."),
                    "transporting": ("at_hospital", "Simulated ambulance arrived at the receiving hospital."),
                }[mission.status]
                _advance(db, log, incident, mission, status, message, deadline)
                deadline = simulation_due(mission, at)
        return {"changed": bool(log.deliveries)}, log.deliveries


def reroute(actor, mission_id, inject, clock=now, service=None):
    check_role(actor, "dispatcher", "driver")
    require(not inject or actor.mode == "demo", "Traffic injection is available in training.", "forbidden", 403)
    service = service or router
    at = clock()
    with Session() as db:
        mission = scoped_mission(db, actor.workspace_id, mission_id)
        incident = db.get(Incident, mission.incident_id)
        require(participants(actor, incident, mission), "Mission not assigned to this user.", "forbidden", 403)
        if inject and mission.traffic_injected:
            return {"changed": False, "reason": "Traffic was already injected."}, []
        require(mission.status in {"assigned", "en_route_to_patient", "transporting"},
                "There is no active route leg to recalculate.")
        if not inject and mission.rerouted_at and (at - mission.rerouted_at).total_seconds() < settings.reroute_cooldown:
            return {"changed": False, "reason": "Route recalculation is on cooldown."}, []
        outbound = mission.status != "transporting"
        version = mission.pickup_version if outbound else mission.receiving_version
        route = db.get(Route, (mission.id, version))
        require(route, "There is no saved route to recalculate.", "route_unavailable")
        leg = "pickup" if outbound else "receiving"
        start, end = route.points[0], route.points[-1]
        fraction = 0.0
        fix = db.get(Position, mission.unit_id)
        if actor.mode == "connected" and fix and (at - fix.observed_at).total_seconds() <= settings.gps_ttl:
            start = [fix.lat, fix.lng]
            fraction = progress_on_route(start, route.points)
        elif actor.mode == "connected" and mission.status != "assigned":
            require(False, "Share a fresh GPS fix before recalculating this leg.", "gps_stale")
        elif actor.mode == "demo" and mission.leg_started_at and mission.leg_seconds:
            fraction = min(1, (at - mission.leg_started_at).total_seconds() / mission.leg_seconds)
            start = simulate_position(route.points, fraction)
    problem = None
    try:
        options = service.routes(actor.mode, tuple(start), tuple(end), at, alternate=True)
    except RouteUnavailable as exc:
        problem, options = str(exc), []
    remaining = route.eta_seconds * (1 - fraction)
    simulated_delay = 240 if inject else 0
    chosen = next((r for r in options if [list(p) for p in r.points] != route.points
                   and r.eta_seconds + settings.reroute_threshold <= remaining + simulated_delay), None)

    def run(db, log):
        current = scoped_mission(db, actor.workspace_id, mission_id)
        if inject and current.traffic_injected:
            return {"changed": False, "reason": "Traffic was already injected."}
        require(current.route_version == mission.route_version and current.status == mission.status,
                "The active route changed. Retry recalculation.", "retry")
        current.rerouted_at = at
        if problem:
            current.routing_issue = problem + " Showing the last-known route."
            event = log.add("route.unavailable", problem + " Last-known route retained.",
                            incident=incident, mission=current)
            return log.change_result(event, changed=False, reason=problem)
        current.routing_issue = None
        if not chosen:
            event = log.add("route.checked", "No faster alternative met the improvement threshold.",
                            incident=incident, mission=current, inbox=False)
            return log.change_result(event, changed=False, reason=event.reason)
        save_route(db, current, leg, chosen)
        current.traffic_injected |= inject
        if current.leg_started_at:
            current.leg_started_at, current.leg_seconds = at, plan_duration(log.workspace, chosen.eta_seconds)
            if inject:
                current.leg_seconds = max(5, min(current.leg_seconds, 8))
        message = ("Traffic detected. Ambulance switching to a faster route." if inject
                   else "Ambulance switched to a faster provider route.")
        event = log.add("route.changed", message, incident=incident, mission=current,
                        payload={"version": current.route_version, "source": chosen.source})
        return log.change_result(event, changed=True, version=current.route_version, reason=message)

    return write(actor, run, clock)
