from statistics import median

from sqlalchemy import select, text

from dispatch.allocator import bases, fleet, queue_order
from dispatch.config import now, settings
from dispatch.db import Session
from dispatch.journal import actor_view, envelope, participants, time_text
from dispatch.models import (
    ACTIVE,
    Capacity,
    Event,
    EventRecipient,
    Hospital,
    Incident,
    Intake,
    Mission,
    Notification,
    Route,
    Unit,
    Workspace,
)
from dispatch.routing import simulate_position
from fixtures.alandi import PICKUP_NAME


def capacity_view(hospital, capacity: Capacity | None, at):
    return {
        "id": hospital.id, "name": hospital.name, "code": hospital.code,
        "location": [hospital.lat, hospital.lng], "capabilities": hospital.capabilities,
        "capacity": {
            "total": capacity.total, "occupied": capacity.occupied, "reserved": capacity.reserved,
            "available": capacity.total - capacity.occupied - capacity.reserved,
            "accepting": capacity.accepting, "source": capacity.source,
            "reported_at": time_text(capacity.reported_at),
            "stale": (at - capacity.reported_at).total_seconds() > settings.capacity_ttl,
        } if capacity else None,
    }


def route_view(route):
    if not route:
        return None
    return {
        "version": route.version, "leg": route.leg, "points": route.points,
        "directions": route.directions, "eta_seconds": route.eta_seconds,
        "distance_m": route.distance_m, "delay_seconds": route.delay_seconds,
        "source": route.source, "calculated_at": time_text(route.calculated_at),
    }


def mission_view(db, actor, mission, at):
    sending, receiving = db.get(Hospital, mission.sending_id), db.get(Hospital, mission.receiving_id) if mission.receiving_id else None
    unit, intake = db.get(Unit, mission.unit_id), db.get(Intake, mission.id)
    versions = {}
    for leg, version in (("pickup", mission.pickup_version), ("receiving", mission.receiving_version)):
        versions[leg] = route_view(db.get(Route, (mission.id, version))) if version else None
    visible_intake = intake and (actor.role == "nurse" or (
        actor.role == "hospital" and actor.hospital_id == mission.receiving_id
        and mission.destination_status == "accepted"
    ))
    leg = "pickup" if mission.status in ("assigned", "en_route_to_patient", "at_patient") else "receiving"
    if mission.status == "at_patient" and intake and mission.destination_status == "accepted":
        leg = "receiving"
    route = versions[leg]
    simulated = None
    if actor.mode == "demo" and route:
        if mission.leg_started_at and mission.leg_seconds:
            elapsed = (at - mission.leg_started_at).total_seconds()
            simulated = simulate_position(route["points"], elapsed / mission.leg_seconds)
        elif mission.status != "cancelled":
            simulated = route["points"][-1] if mission.status in (
                "at_hospital", "completed",
            ) or (mission.status == "at_patient" and leg == "pickup") else route["points"][0]
    return {
        "id": mission.id, "unit_id": unit.id, "unit": unit.name, "status": mission.status,
        "dispatching_hospital_id": sending.id, "dispatching_hospital": sending.name,
        "dispatching_location": [sending.lat, sending.lng],
        "receiving_hospital_id": receiving.id if receiving else None,
        "receiving_hospital": receiving.name if receiving else None,
        "receiving_location": [receiving.lat, receiving.lng] if receiving else None,
        "destination_status": mission.destination_status,
        "released": mission.released, "acknowledged": mission.acknowledged,
        "route": route, "pickup_route": versions["pickup"], "receiving_route": versions["receiving"]
        if mission.destination_status == "accepted" or actor.role in ("dispatcher", "hospital") else None,
        "route_version": mission.route_version, "routing_issue": mission.routing_issue,
        "destination_candidates": mission.destination_candidates if actor.role == "dispatcher" else [],
        "leg_started_at": time_text(mission.leg_started_at), "leg_seconds": mission.leg_seconds,
        "simulated_position": simulated, "traffic_injected": mission.traffic_injected,
        "intake_saved": bool(intake),
        "intake": {"alias": intake.alias, "age": intake.age, "issue": intake.issue,
                   "observations": intake.observations} if visible_intake else None,
        "updated_at": time_text(mission.updated_at),
    }


def unit_view(unit, base, fix, at, mode):
    return {
        "id": unit.id, "name": unit.name, "status": unit.status, "base": base.name,
        "hospital_id": base.id, "checked_in": unit.checked_in,
        "location": [fix.lat, fix.lng] if fix else [base.lat, base.lng] if unit.checked_in else None,
        "source": "Simulation" if mode == "demo" else "Driver GPS" if fix else
        "Checked-in base" if unit.checked_in else "Position unavailable",
        "observed_at": time_text(fix.observed_at) if fix else None,
        "accuracy_m": fix.accuracy_m if fix else None,
        "stale": bool(fix and (at - fix.observed_at).total_seconds() > settings.gps_ttl),
    }


def _event_query(actor):
    return select(Event).join(EventRecipient, EventRecipient.event_id == Event.id).where(
        Event.workspace_id == actor.workspace_id, EventRecipient.member_id == actor.id,
    )


def _snapshot(db, actor, at):
    workspace = db.get(Workspace, actor.workspace_id)
    mission_rows = db.scalars(select(Mission).where(Mission.workspace_id == workspace.id).order_by(
        Mission.created_at.desc(), Mission.id,
    )).all()
    assignments = {}
    former = {}
    for mission in mission_rows:
        if mission.status != "cancelled":
            assignments.setdefault(mission.incident_id, mission)
        else:
            former.setdefault(mission.incident_id, mission)
    pending = db.scalars(select(Incident).where(
        Incident.workspace_id == workspace.id, Incident.status == "queued",
    ).order_by(*queue_order())).all()
    positions = {item.id: i for i, item in enumerate(pending, 1)}
    incidents = db.scalars(select(Incident).where(Incident.workspace_id == workspace.id).order_by(
        Incident.created_at.desc(), Incident.code.desc(),
    )).all()
    scoped = []
    for incident in incidents:
        mission = assignments.get(incident.id)
        if incident.status == "cancelled" and not mission:
            mission = former.get(incident.id)
        if participants(actor, incident, mission):
            scoped.append((incident, mission))
    case_ids = [item.id for item, _ in scoped]
    timeline = {}
    if case_ids:
        for event in db.scalars(_event_query(actor).where(Event.incident_id.in_(case_ids)).order_by(Event.seq)):
            if event.kind == "unit.location":
                continue
            timeline.setdefault(event.incident_id, []).append(envelope(event, actor.role == "dispatcher"))
    case_views = [{
        "id": case.id, "code": case.code, "status": case.status,
        "priority": case.priority, "effective_priority": case.priority or "normal",
        "pickup_name": case.pickup_name, "pickup": [case.lat, case.lng], "zone": case.zone,
        "channel": case.channel, "reported_urgency": case.reported_urgency,
        "unit_requirement": case.unit_requirement, "receiving_requirement": case.receiving_requirement,
        "dispatch_note": case.dispatch_note if actor.role == "dispatcher" else "",
        "candidates": case.candidates if actor.role == "dispatcher" else [],
        "created_at": time_text(case.created_at), "arrived_at": time_text(case.arrived_at),
        "queue_position": positions.get(case.id),
        "timeline": timeline.get(case.id, []),
        "mission": mission_view(db, actor, mission, at) if mission else None,
    } for case, mission in scoped]
    involved_units = {mission.unit_id for _, mission in scoped if mission}
    units = [
        unit_view(unit, base, fix, at, actor.mode)
        for unit, base, fix in fleet(db, workspace.id)
        if actor.role == "dispatcher" or base.id == actor.hospital_id
        or unit.id == actor.unit_id or unit.id in involved_units
    ]
    if actor.role != "dispatcher":
        current_units = {mission.unit_id for _, mission in scoped if mission and mission.status in ACTIVE}
        for unit in units:
            if unit["id"] not in current_units and unit["id"] != actor.unit_id and unit["hospital_id"] != actor.hospital_id:
                unit.update(location=None, observed_at=None, accuracy_m=None, stale=False)
    hospitals = []
    if actor.role in ("dispatcher", "hospital", "feed"):
        hospitals = [capacity_view(h, c, at) for h, c in bases(db, workspace.id)
                     if actor.role == "dispatcher" or h.id == actor.hospital_id]
    arrival_times = [(case.arrived_at - case.created_at).total_seconds()
                     for case, _ in scoped if case.arrived_at]
    notices = db.execute(select(Event, Notification).join(
        Notification, Notification.event_id == Event.id,
    ).where(Notification.member_id == actor.id, Event.workspace_id == actor.workspace_id).order_by(
        Event.seq.desc(),
    ).limit(60)).all()
    return {
        "sequence": workspace.seq, "generation": workspace.generation, "server_time": at.isoformat(),
        "session": actor_view(actor), "incidents": case_views,
        "queue": [item.id for item in pending] if actor.role == "dispatcher" else [],
        "units": units, "hospitals": hospitals,
        "metrics": {
            "active_units": sum(unit["status"] == "busy" for unit in units),
            "available_units": sum(unit["status"] == "available" for unit in units),
            "queued": len(pending) if actor.role == "dispatcher" else sum(case.status == "queued" for case, _ in scoped),
            "median_arrival_seconds": round(median(arrival_times)) if arrival_times else None,
            "completed": sum(case.status == "completed" for case, _ in scoped),
        },
        "notifications": [{**envelope(event), "read": bool(item.read_at)} for event, item in notices],
        "demo_pickup_name": PICKUP_NAME if actor.mode == "demo" else None,
    }


def snapshot(actor, clock=now):
    with Session.begin() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        return _snapshot(db, actor, clock())


def event_replay(actor, after):
    with Session.begin() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        last = db.get(Workspace, actor.workspace_id).seq
        rows = db.scalars(_event_query(actor).where(Event.seq > after).order_by(Event.seq).limit(201)).all()
        more = len(rows) > 200
        return {"events": [envelope(e, actor.role == "dispatcher") for e in rows[:200]],
                "next": rows[199].seq if more else last, "more": more}
