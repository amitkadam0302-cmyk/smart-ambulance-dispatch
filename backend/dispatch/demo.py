from secrets import token_urlsafe

from sqlalchemy import delete, select

from dispatch.auth import digest, issue
from dispatch.config import now, settings
from dispatch.db import Session
from dispatch.errors import require
from dispatch.journal import write
from dispatch.models import (
    Capacity,
    Event,
    EventRecipient,
    Hospital,
    Incident,
    Intake,
    Membership,
    Mission,
    Notification,
    Position,
    Reservation,
    Route,
    Unit,
    Workspace,
    uid,
)
from fixtures.alandi import HOSPITALS, populate


def create_group():
    require(settings.mode == "demo", "Training workspaces are disabled here.", "forbidden", 403)
    with Session.begin() as db:
        code = token_urlsafe(24)
        workspace = Workspace(id=uid(), slug=f"training-{uid()}", mode="demo",
                              name="Alandi training workspace", share_hash=digest(code))
        db.add(workspace)
        db.flush()
        populate(db, workspace, now())
        return {"code": code, "workspace": workspace.name}


def enter(body):
    require(settings.mode == "demo", "Training workspaces are disabled here.", "forbidden", 403)
    with Session.begin() as db:
        workspace = db.scalar(select(Workspace).where(Workspace.mode == "demo", Workspace.share_hash == digest(body.code)))
        require(workspace, "Training group not found. Check the group link.", "not_found", 404)
        query = select(Membership).where(Membership.workspace_id == workspace.id,
                                        Membership.role == body.role, Membership.enabled.is_(True))
        if body.role in ("driver", "nurse"):
            query = query.join(Unit, Unit.id == Membership.unit_id).where(Unit.name == (body.unit or "AMB3"))
        if body.role == "hospital":
            query = query.join(Hospital, Hospital.id == Membership.hospital_id).where(
                Hospital.code == (body.hospital or "ALANDI"),
            )
        member = db.scalar(query)
        require(member, "Training role not found.", "not_found", 404)
        return issue(db, member)


def reset(actor):
    require(actor.role == "dispatcher" and actor.mode == "demo", "Only the demo dispatcher can reset its group.",
            "forbidden", 403)

    def run(db, log):
        ids = select(Mission.id).where(Mission.workspace_id == actor.workspace_id)
        events = select(Event.id).where(Event.workspace_id == actor.workspace_id)
        for table in (Notification, EventRecipient):
            db.execute(delete(table).where(table.event_id.in_(events)))
        db.execute(delete(Event).where(Event.workspace_id == actor.workspace_id))
        for table in (Intake, Reservation, Route):
            db.execute(delete(table).where(table.mission_id.in_(ids)))
        db.execute(delete(Mission).where(Mission.workspace_id == actor.workspace_id))
        db.execute(delete(Incident).where(Incident.workspace_id == actor.workspace_id))
        db.execute(delete(Position).where(Position.unit_id.in_(
            select(Unit.id).where(Unit.workspace_id == actor.workspace_id),
        )))
        for unit in db.scalars(select(Unit).where(Unit.workspace_id == actor.workspace_id)):
            unit.status, unit.checked_in, unit.version = "available", True, unit.version + 1
        fixture = {code: (total, occupied) for code, _, _, _, total, occupied, _ in HOSPITALS}
        for hospital in db.scalars(select(Hospital).where(Hospital.workspace_id == actor.workspace_id)):
            capacity = db.get(Capacity, hospital.id)
            capacity.total, capacity.occupied = fixture[hospital.code]
            capacity.reserved, capacity.accepting = 0, True
            capacity.source, capacity.reported_at = "Example hospital report", now()
            capacity.version += 1
        log.workspace.generation += 1
        event = log.add("workspace.reset", "This training group was reset.", workspace_wide=True)
        return log.change_result(event, generation=log.workspace.generation)

    return write(actor, run)
