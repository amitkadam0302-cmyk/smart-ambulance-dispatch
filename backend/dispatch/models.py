from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from dispatch.config import now

ACTIVE = ("assigned", "en_route_to_patient", "at_patient", "transporting", "at_hospital")
STATES = (*ACTIVE, "queued", "completed", "cancelled")
ROLES = ("patient", "dispatcher", "hospital", "driver", "nurse", "feed")
PRIORITIES = ("critical", "high", "normal")
UNIT_STATES = ("available", "busy", "offline", "out_of_service")
STATE_SQL = ", ".join(f"'{status}'" for status in STATES)


def uid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    mode: Mapped[str] = mapped_column(String(12))
    share_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    case_counter: Mapped[int] = mapped_column(Integer, default=0)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (CheckConstraint("mode IN ('demo', 'template', 'connected')"),)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str | None] = mapped_column(String(512))


class Hospital(Base):
    __tablename__ = "hospitals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float]
    lng: Mapped[float]
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    feed_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("workspace_id", "code"),)


class Capacity(Base):
    __tablename__ = "capacities"
    hospital_id: Mapped[str] = mapped_column(ForeignKey("hospitals.id"), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    occupied: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    accepting: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(120))
    reported_at = mapped_column(DateTime(timezone=True), nullable=False, default=now)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        CheckConstraint("total >= 0 AND occupied >= 0 AND reserved >= 0"),
        CheckConstraint("occupied + reserved <= total"),
    )


class Unit(Base):
    __tablename__ = "units"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    hospital_id: Mapped[str] = mapped_column(ForeignKey("hospitals.id"), index=True)
    name: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(20), default="available")
    checked_in: Mapped[bool] = mapped_column(Boolean, default=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        UniqueConstraint("workspace_id", "name"),
        CheckConstraint("status IN ('available','busy','offline','out_of_service')"),
    )


class Position(Base):
    __tablename__ = "positions"
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), primary_key=True)
    lat: Mapped[float]
    lng: Mapped[float]
    accuracy_m: Mapped[float]
    observed_at = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("lat BETWEEN -90 AND 90 AND lng BETWEEN -180 AND 180"),
        CheckConstraint("accuracy_m >= 0"),
    )


class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    role: Mapped[str] = mapped_column(String(12))
    hospital_id: Mapped[str | None] = mapped_column(ForeignKey("hospitals.id"))
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("units.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "role"),
        CheckConstraint("role IN ('patient','dispatcher','hospital','driver','nurse','feed')"),
        CheckConstraint("role NOT IN ('hospital','feed') OR hospital_id IS NOT NULL"),
        CheckConstraint("role NOT IN ('driver','nurse') OR unit_id IS NOT NULL"),
        Index("ix_membership_audience", "workspace_id", "role", "unit_id", "hospital_id"),
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("memberships.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("memberships.id"), index=True)
    code: Mapped[str] = mapped_column(String(24))
    pickup_name: Mapped[str] = mapped_column(String(140))
    lat: Mapped[float]
    lng: Mapped[float]
    zone: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(12))
    reported_urgency: Mapped[str] = mapped_column(String(12))
    priority: Mapped[str | None] = mapped_column(String(12))
    unit_requirement: Mapped[str | None] = mapped_column(String(32))
    receiving_requirement: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    dispatch_note: Mapped[str] = mapped_column(String(240), default="")
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    created_at = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    arrived_at = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        UniqueConstraint("workspace_id", "code"),
        CheckConstraint("lat BETWEEN -90 AND 90 AND lng BETWEEN -180 AND 180"),
        CheckConstraint(f"status IN ({STATE_SQL})"),
        CheckConstraint("priority IN ('critical', 'high', 'normal')"),
        Index("ix_dispatch_queue", "workspace_id", "status", "created_at"),
    )


class Mission(Base):
    __tablename__ = "missions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), index=True)
    sending_id: Mapped[str] = mapped_column(ForeignKey("hospitals.id"), index=True)
    receiving_id: Mapped[str | None] = mapped_column(ForeignKey("hospitals.id"), index=True)
    destination_status: Mapped[str] = mapped_column(String(16), default="coordination")
    declined: Mapped[list] = mapped_column(JSON, default=list)
    destination_candidates: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="assigned")
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    released: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    leg_started_at = mapped_column(DateTime(timezone=True))
    leg_seconds: Mapped[int | None]
    route_version: Mapped[int] = mapped_column(Integer, default=0)
    pickup_version: Mapped[int | None]
    receiving_version: Mapped[int | None]
    rerouted_at = mapped_column(DateTime(timezone=True))
    traffic_injected: Mapped[bool] = mapped_column(Boolean, default=False)
    routing_issue: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (
        CheckConstraint(f"status IN ({STATE_SQL}) AND status <> 'queued'"),
        CheckConstraint("destination_status IN ('coordination','pending','accepted')"),
        Index("uq_active_mission_unit", "unit_id", unique=True,
              postgresql_where=text("status NOT IN ('completed','cancelled')")),
        Index("uq_active_mission_incident", "incident_id", unique=True,
              postgresql_where=text("status NOT IN ('completed','cancelled')")),
    )


class Reservation(Base):
    __tablename__ = "reservations"
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), primary_key=True)
    hospital_id: Mapped[str] = mapped_column(ForeignKey("hospitals.id"), index=True)
    status: Mapped[str] = mapped_column(String(12))
    updated_at = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (CheckConstraint("status IN ('reserved','released','occupied')"),)


class Intake(Base):
    __tablename__ = "intakes"
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), primary_key=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("memberships.id"))
    alias: Mapped[str] = mapped_column(String(100))
    age: Mapped[int | None]
    issue: Mapped[str] = mapped_column(String(40))
    observations: Mapped[str | None] = mapped_column(String(1500))
    created_at = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (CheckConstraint("age BETWEEN 0 AND 125"),)


class Route(Base):
    __tablename__ = "route_versions"
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg: Mapped[str] = mapped_column(String(12))
    points: Mapped[list] = mapped_column(JSON)
    directions: Mapped[list] = mapped_column(JSON)
    eta_seconds: Mapped[int]
    distance_m: Mapped[int]
    delay_seconds: Mapped[int | None]
    source: Mapped[str] = mapped_column(String(40))
    calculated_at = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (CheckConstraint("eta_seconds > 0 AND distance_m >= 0"),)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    seq: Mapped[int]
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), index=True)
    mission_id: Mapped[str | None] = mapped_column(ForeignKey("missions.id"))
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("memberships.id"))
    kind: Mapped[str] = mapped_column(String(48))
    reason: Mapped[str] = mapped_column(String(360))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("workspace_id", "seq"),)


class EventRecipient(Base):
    __tablename__ = "event_recipients"
    member_id: Mapped[str] = mapped_column(ForeignKey("memberships.id"), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True, index=True)


class Notification(Base):
    __tablename__ = "notifications"
    member_id: Mapped[str] = mapped_column(ForeignKey("memberships.id"), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True, index=True)
    read_at = mapped_column(DateTime(timezone=True))


class Command(Base):
    __tablename__ = "commands"
    member_id: Mapped[str] = mapped_column(ForeignKey("memberships.id"), primary_key=True)
    scope: Mapped[str] = mapped_column(String(100), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
