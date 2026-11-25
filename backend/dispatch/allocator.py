from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from math import ceil

from sqlalchemy import case, select

from dispatch.config import now, settings
from dispatch.db import Session
from dispatch.errors import Problem, require
from dispatch.journal import Journal, lock_workspace
from dispatch.models import (
    Capacity,
    Hospital,
    Incident,
    Mission,
    Position,
    Reservation,
    Route,
    Unit,
    Workspace,
)
from dispatch.routing import Estimate, Router, RouteUnavailable, distance

router = Router()
PRIORITY_RANK = case((Incident.priority == "critical", 0), (Incident.priority == "high", 1), else_=2)


def queue_order():
    return (PRIORITY_RANK, Incident.created_at, Incident.code)


def next_queued(db, workspace_id):
    return db.scalar(select(Incident).where(
        Incident.workspace_id == workspace_id, Incident.status == "queued",
    ).order_by(*queue_order()).limit(1))


def scoped_incident(db, workspace_id, incident_id):
    incident = db.get(Incident, incident_id)
    require(incident and incident.workspace_id == workspace_id,
            "Incident not found.", "not_found", 404)
    return incident


def scoped_mission(db, workspace_id, mission_id):
    mission = db.get(Mission, mission_id)
    require(mission and mission.workspace_id == workspace_id, "Mission not found.", "not_found", 404)
    return mission


def capacity_issue(hospital, capacity, requirement, at, carrying_reservation=False):
    if not capacity or (at - capacity.reported_at).total_seconds() > settings.capacity_ttl:
        return "Capacity report is stale"
    if not capacity.accepting:
        return "Hospital is not accepting"
    if capacity.total - capacity.occupied - capacity.reserved + int(carrying_reservation) <= 0:
        return "No available beds"
    if requirement and requirement not in hospital.capabilities:
        return "Required service unavailable"
    return ""


def candidate_routes(mode, rows, target, at, service, alternatives=False):
    def evaluate(row):
        summary, origin, error = row
        if error:
            return {**summary, "reason": error}, None
        try:
            options = service.routes(mode, origin, target, at, alternatives)
            route = options[0]
            return {
                **summary, "eta_seconds": route.eta_seconds, "distance_m": route.distance_m,
                "source": route.source, "calculated_at": route.calculated_at,
                "reason": "Eligible road route",
            }, route
        except RouteUnavailable as exc:
            return {**summary, "reason": str(exc)}, None

    if mode == "demo" or len(rows) < 2:
        return [evaluate(row) for row in rows]
    with ThreadPoolExecutor(max_workers=min(6, len(rows))) as workers:
        return list(workers.map(evaluate, rows))


def destination_routes(mode, incident, hospitals, sending_id, declined, at, service,
                       preferred=None, own_reservation=None):
    candidates = []
    start = (incident.lat, incident.lng)

    def evaluate(pair):
        hospital, capacity = pair
        data = {"id": hospital.id, "name": hospital.name, "code": hospital.code}
        reason = ("Hospital declined this mission" if hospital.id in declined
                  else capacity_issue(hospital, capacity, incident.receiving_requirement, at,
                                      hospital.id == own_reservation))
        if reason:
            return {**data, "reason": reason}, None
        try:
            route = service.routes(mode, start, (hospital.lat, hospital.lng), at)[0]
            return {**data, "reason": "Eligible destination", "eta_seconds": route.eta_seconds,
                    "source": route.source, "distance_m": route.distance_m,
                    "calculated_at": route.calculated_at}, route
        except RouteUnavailable as exc:
            return {**data, "reason": str(exc)}, None

    if mode == "demo" or len(hospitals) < 2:
        candidates = [evaluate(pair) for pair in hospitals]
    else:
        with ThreadPoolExecutor(max_workers=min(6, len(hospitals))) as workers:
            candidates = list(workers.map(evaluate, hospitals))
    eligible = [(data, route) for data, route in candidates
                if route and (preferred is None or data["id"] == preferred)]
    ranked = sorted(eligible, key=lambda pair: (
        0 if mode == "demo" and pair[0]["id"] == sending_id else 1,
        pair[1].eta_seconds, pair[0]["code"],
    ))
    return [data for data, _ in candidates], (ranked[0] if ranked else (None, None))


def bases(db, workspace_id):
    return db.execute(select(Hospital, Capacity).outerjoin(
        Capacity, Capacity.hospital_id == Hospital.id,
    ).where(Hospital.workspace_id == workspace_id).order_by(Hospital.code)).all()


def fleet(db, workspace_id):
    return db.execute(select(Unit, Hospital, Position).join(
        Hospital, Hospital.id == Unit.hospital_id,
    ).outerjoin(Position, Position.unit_id == Unit.id).where(
        Unit.workspace_id == workspace_id,
    ).order_by(Unit.name)).all()


def unit_inputs(mode, incident, units, at):
    result = []
    for unit, base, fix in units:
        origin, error = (base.lat, base.lng), ""
        if unit.status != "available":
            error = {"busy": "Already assigned to another mission", "offline": "Unit offline",
                     "out_of_service": "Unit out of service"}[unit.status]
        elif incident.unit_requirement and incident.unit_requirement not in unit.capabilities:
            error = "Required equipment unavailable"
        elif mode == "demo" and not unit.checked_in:
            error = "Unit is not checked in at its demo base"
        elif mode == "connected":
            if fix and (at - fix.observed_at).total_seconds() <= settings.gps_ttl:
                origin = (fix.lat, fix.lng)
            elif fix or not unit.checked_in:
                error = "Latest GPS fix is stale or missing"
        result.append(({"id": unit.id, "name": unit.name, "base": base.name,
                        "version": unit.version, "base_id": base.id}, origin, error))
    return result


def save_route(db, mission, leg, estimate):
    mission.route_version += 1
    if leg == "pickup":
        mission.pickup_version = mission.route_version
    else:
        mission.receiving_version = mission.route_version
    db.add(Route(
        mission_id=mission.id, version=mission.route_version, leg=leg, points=estimate.points,
        directions=estimate.directions, eta_seconds=estimate.eta_seconds,
        distance_m=estimate.distance_m, delay_seconds=estimate.delay_seconds, source=estimate.source,
        calculated_at=datetime.fromisoformat(estimate.calculated_at),
    ))


def change_reservation(db, mission, operation, at):
    row = db.get(Reservation, mission.id)
    if operation == "reserve":
        if row and row.status == "reserved":
            require(row.hospital_id == mission.receiving_id, "Another bed is reserved.")
            return
        capacity = db.get(Capacity, mission.receiving_id)
        require(capacity and capacity.total > capacity.occupied + capacity.reserved,
                "The receiving hospital has no available beds.", "capacity_full")
        capacity.reserved += 1
        if row is None:
            row = Reservation(mission_id=mission.id, hospital_id=mission.receiving_id,
                              status="reserved", updated_at=at)
            db.add(row)
        else:
            row.hospital_id, row.status, row.updated_at = mission.receiving_id, "reserved", at
    elif row and row.status == "reserved":
        capacity = db.get(Capacity, row.hospital_id)
        capacity.reserved -= 1
        if operation == "occupy":
            capacity.occupied += 1
        row.status, row.updated_at = ("occupied" if operation == "occupy" else "released"), at
    else:
        require(operation != "occupy", "An accepted bed is required for handover.")
        return
    capacity.version += 1


def plan_duration(workspace, eta_seconds):
    return max(9, ceil(eta_seconds / settings.demo_speed)) if workspace.mode == "demo" else None


def select_target(db, log, incident, mission, candidates, chosen, service_at):
    destination, route = chosen
    previous = mission.receiving_id
    mission.destination_candidates = candidates
    mission.receiving_id = destination["id"] if destination else None
    mission.destination_status = "pending" if destination else "coordination"
    mission.receiving_version = None
    if route:
        save_route(db, mission, "receiving", route)
    if not destination:
        log.add("destination.coordination", "Dispatcher coordination needed. No eligible receiving hospital.",
                incident=incident, mission=mission, hospitals=(previous,))
    elif log.workspace.mode == "demo":
        change_reservation(db, mission, "reserve", service_at)
        mission.destination_status = "accepted"
        log.add("destination.accepted", "Simulated hospital accepted and reserved one example bed.",
                incident=incident, mission=mission, hospitals=(previous,))
    else:
        log.add("destination.requested", "Receiving hospital decision requested.",
                incident=incident, mission=mission, hospitals=(previous,))


def release_unit(db, mission):
    unit = db.get(Unit, mission.unit_id)
    unit.status, unit.version = "available", unit.version + 1
    if mission.status != "assigned":
        unit.checked_in = mission.status == "at_hospital" and mission.receiving_id == mission.sending_id
    return unit


def requested_candidate(db, workspace_id, requested_id, preferred_id):
    first = next_queued(db, workspace_id)
    if not requested_id:
        return first, None
    request = scoped_incident(db, workspace_id, requested_id)
    if request.status == "assigned" and preferred_id:
        mission = db.scalar(select(Mission).where(
            Mission.incident_id == request.id, Mission.status == "assigned",
        ))
        require(mission, "Reassignment is only available before departure.")
        return request, mission
    require(first and first.id == request.id, "Dispatch the highest-priority queued request first.",
            "queue_order")
    return first, None


def allocate(workspace_id, *, requested_id=None, preferred_id=None, reason=None, manual_eta=None,
             actor=None, clock=now, service=None):
    """Read and release the database before routing, then commit only against the same revision."""
    service = service or router
    for _ in range(5):
        at = clock()
        with Session() as db:
            workspace = db.get(Workspace, workspace_id)
            mode, revision = workspace.mode, workspace.seq
            first, previous = requested_candidate(db, workspace_id, requested_id, preferred_id)
            if not first:
                return {"status": "empty"}, []
            incident, hospital_rows = first, bases(db, workspace_id)
            inputs = unit_inputs(mode, incident, fleet(db, workspace_id), at)
        target = (incident.lat, incident.lng)
        results = candidate_routes(mode, inputs, target, at, service)
        selected = None
        eligible = sorted(((data, route) for data, route in results if route),
                          key=lambda item: (item[1].eta_seconds, item[0]["name"]))
        if preferred_id:
            require(reason, "Add a reason for a manual assignment.", "reason_required")
            match = next(((data, origin, error) for data, origin, error in inputs
                          if data["id"] == preferred_id), None)
            require(match and not match[2], "The selected unit is ineligible.", "unit_unavailable")
            selected = next((pair for pair in eligible if pair[0]["id"] == preferred_id), None)
            if selected is None and manual_eta:
                data, origin, _ = match
                manual = Estimate([origin, target], [], manual_eta, round(distance(origin, target)),
                                  None, "Dispatcher estimate / straight-line", at.isoformat())
                selected = (data, manual)
        elif eligible:
            selected = eligible[0]
        notes = [data for data, _ in results]
        target_plan = None
        if selected:
            unit_info, pickup = selected
            target_plan = destination_routes(
                mode, incident, hospital_rows, unit_info["base_id"], [], at, service,
                own_reservation=(previous.receiving_id if previous and previous.destination_status == "accepted" else None),
            )
        with Session.begin() as db:
            current = lock_workspace(db, workspace_id)
            if current.seq != revision:
                continue
            queued, current_assignment = requested_candidate(db, workspace_id, requested_id, preferred_id)
            if queued is None or queued.id != incident.id:
                continue
            require(queued.version == incident.version, "Queue changed. Retry dispatch.", "queue_changed")
            log = Journal(db, current, clock, actor)
            if not selected:
                if preferred_id:
                    raise Problem(409, "route_unavailable",
                                  "Enter an estimated travel time to dispatch manually.")
                note = ("No eligible unit has a usable road route. Review routing and availability."
                        if any(not error for _, _, error in inputs)
                        else "No eligible ambulance available. Request is queued.")
                if queued.dispatch_note != note or queued.candidates != notes:
                    queued.dispatch_note, queued.candidates = note, notes
                    queued.version += 1
                    log.add("dispatch.waiting", note, incident=queued)
                return {"status": "queued", "id": queued.id}, log.deliveries
            unit = db.get(Unit, unit_info["id"])
            if unit.status != "available" or unit.version != unit_info["version"]:
                continue
            qualified_count = len(eligible)
            reason_text = (
                f"{unit.name} selected by the dispatcher. {reason}" if preferred_id else
                f"{unit.name} had the shortest road ETA among {qualified_count} routed "
                + ("unit. " if qualified_count == 1 else "units. ")
                + "Equal ETAs are ordered by unit name."
            )
            if current_assignment:
                change_reservation(db, current_assignment, "release", clock())
                release_unit(db, current_assignment)
                current_assignment.status, current_assignment.updated_at = "cancelled", clock()
                log.add("mission.reassigned", "Dispatcher replaced the assigned unit before departure.",
                        incident=queued, mission=current_assignment)
            if preferred_id and reason:
                log.note(reason, incident=queued)
            unit.status, unit.version = "busy", unit.version + 1
            queued.status, queued.dispatch_note, queued.candidates = "assigned", reason_text, notes
            queued.version += 1
            mission = Mission(workspace_id=workspace_id, incident_id=queued.id, unit_id=unit.id,
                              sending_id=unit.hospital_id, status="assigned", created_at=clock(),
                              updated_at=clock(), acknowledged=mode == "demo", released=mode == "demo")
            db.add(mission)
            db.flush()
            save_route(db, mission, "pickup", pickup)
            event = log.add("mission.assigned", "Ambulance assigned to the pickup.",
                            incident=queued, mission=mission,
                            payload={"status": "assigned", "unit": unit.name})
            if mode == "demo":
                log.add("unit.released", "Simulated sending hospital acknowledged and released the unit.",
                        incident=queued, mission=mission)
            select_target(db, log, queued, mission, *target_plan, clock())
            return log.change_result(event, status="assigned", id=queued.id,
                                     mission_id=mission.id), log.deliveries
    return {"status": "retry", "detail": "Queue changed during routing. Retry dispatch."}, []
