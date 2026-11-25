import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import or_, select

from dispatch.auth import Actor, digest
from dispatch.config import now
from dispatch.db import Session
from dispatch.errors import require
from dispatch.models import (
    Command,
    Event,
    EventRecipient,
    Membership,
    Mission,
    Notification,
    Workspace,
    uid,
)


class Delivery(NamedTuple):
    member_id: str
    payload: dict


def envelope(event: Event, include_actor=False):
    payload = {
        "id": event.id, "seq": event.seq, "incident_id": event.incident_id,
        "mission_id": event.mission_id, "kind": event.kind, "reason": event.reason,
        "occurred_at": time_text(event.occurred_at), "payload": event.payload,
    }
    if include_actor:
        payload["actor_id"] = event.actor_id
    return payload


def lock_workspace(db, workspace_id):
    workspace = db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
    require(workspace, "Workspace unavailable.", "not_found", 404)
    return workspace


def participants(actor: Actor, incident, mission: Mission | None):
    if actor.role == "dispatcher" or actor.id == incident.patient_id:
        return True
    if mission is None:
        return False
    if actor.role in ("nurse", "driver"):
        return actor.unit_id == mission.unit_id
    return actor.role == "hospital" and actor.hospital_id in (mission.sending_id, mission.receiving_id)


class Journal:
    def __init__(self, db, workspace, clock=now, actor=None):
        self.db, self.workspace, self.clock, self.actor = db, workspace, clock, actor
        self.deliveries: list[Delivery] = []

    def add(self, kind, reason, *, incident=None, mission=None, hospitals=(),
            payload=None, inbox=True, when=None, workspace_wide=False,
            dispatcher_only=False, member_only=None, actor_id=None):
        self.workspace.seq += 1
        event = Event(
            id=uid(), workspace_id=self.workspace.id, seq=self.workspace.seq, kind=kind,
            reason=reason, incident_id=incident.id if incident else None,
            mission_id=mission.id if mission else None, payload=payload or {},
            actor_id=actor_id or (self.actor.id if self.actor else None), occurred_at=when or self.clock(),
        )
        self.db.add(event)
        self.db.flush([event])
        audience = [Membership.role == "dispatcher"]
        if incident and incident.patient_id:
            audience.append(Membership.id == incident.patient_id)
        if mission:
            audience.append(Membership.unit_id == mission.unit_id)
            hospitals = (*hospitals, mission.sending_id, mission.receiving_id)
        if any(hospitals):
            audience.append(Membership.hospital_id.in_([h for h in hospitals if h]))
        query = select(Membership.id).where(
            Membership.workspace_id == self.workspace.id, Membership.enabled.is_(True),
        )
        if member_only:
            query = query.where(Membership.id == member_only)
        elif dispatcher_only:
            query = query.where(Membership.role == "dispatcher")
        elif not workspace_wide:
            query = query.where(or_(*audience))
        for member_id in self.db.scalars(query).all():
            self.db.add(EventRecipient(event_id=event.id, member_id=member_id))
            if inbox:
                self.db.add(Notification(event_id=event.id, member_id=member_id))
            self.deliveries.append(Delivery(member_id, {"id": event.id, "seq": event.seq}))
        return event

    def note(self, reason, *, incident=None, mission=None):
        return self.add("dispatch.note", reason, incident=incident, mission=mission,
                        dispatcher_only=True, inbox=False)

    def change_result(self, event, **fields):
        return {"event_id": event.id, "sequence": event.seq, **fields}


def idempotent(actor, scope, key, body, action, clock=now):
    fingerprint = digest(json.dumps(body, sort_keys=True, default=str))
    with Session.begin() as db:
        workspace = lock_workspace(db, actor.workspace_id)
        stored = db.get(Command, (actor.id, scope, key))
        if stored:
            require(stored.fingerprint == fingerprint,
                    "This idempotency key is already bound to different input.", "key_reused")
            return stored.result, []
        journal = Journal(db, workspace, clock, actor)
        result = action(db, journal)
        db.add(Command(member_id=actor.id, scope=scope, key=key,
                       fingerprint=fingerprint, result=result))
        return result, journal.deliveries


def write(actor, action, clock=now):
    with Session.begin() as db:
        workspace = lock_workspace(db, actor.workspace_id)
        journal = Journal(db, workspace, clock, actor)
        result = action(db, journal)
        return result, journal.deliveries


def time_text(instant: datetime | None):
    return instant.astimezone(UTC).isoformat() if instant else None


def actor_view(actor):
    return {k: v for k, v in asdict(actor).items() if k not in {"expiry", "slot"}}
