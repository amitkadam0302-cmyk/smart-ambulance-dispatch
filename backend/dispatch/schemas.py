from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

Role = Literal["patient", "dispatcher", "hospital", "driver", "nurse"]
Priority = Literal["critical", "high", "normal"]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=180)]
ShortName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Capability = Literal["general", "advanced", "trauma", "cardiac", "maternity", "stroke"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Point(Input):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class SOS(Point):
    pickup_name: str = Field(min_length=2, max_length=140)
    reported_urgency: Priority = "normal"
    priority: Priority | None = None
    unit_requirement: Capability | None = None
    receiving_requirement: Capability | None = None
    source: Literal["pin", "gps", "alandi", "phone"] = "pin"


class Review(Input):
    priority: Priority
    unit_requirement: Capability | None = None
    receiving_requirement: Capability | None = None
    reason: Reason


class Dispatch(Input):
    unit_id: str | None = Field(None, max_length=36)
    reason: Reason | None = None
    manual_eta_seconds: int | None = Field(None, ge=30, le=86400)


class Transition(Input):
    action: Literal["acknowledge", "release", "depart_pickup", "arrive_pickup",
                    "depart_hospital", "arrive_hospital", "handover", "cancel"]
    reason: Reason | None = None


class Destination(Input):
    decision: Literal["accept", "decline"]
    reason: Reason | None = None


class DestinationSelection(Input):
    hospital_id: str | None = Field(None, max_length=36)
    reason: Reason


class IntakeSubmission(Input):
    alias: ShortName
    age: int | None = Field(None, ge=0, le=125)
    issue: Literal["accident", "reported chest pain", "suspected stroke", "pregnancy", "other"]
    observations: str | None = Field(None, max_length=1500)


class CapacityReport(Input):
    total: int = Field(ge=0, le=100000)
    occupied: int = Field(ge=0, le=100000)
    accepting: bool
    source: ShortName
    reported_at: AwareDatetime | None = None


class GPS(Point):
    accuracy_m: float = Field(ge=0, le=300)
    observed_at: AwareDatetime


class UnitUpdate(Input):
    status: Literal["available", "offline", "out_of_service"]
    checked_in: bool
    reason: Reason


class DemoEntry(Input):
    code: str = Field(min_length=24, max_length=64, pattern="^[a-zA-Z0-9_-]+$")
    role: Role
    unit: str | None = Field(None, pattern="^AMB[123]$")
    hospital: str | None = Field(None, pattern="^(ALANDI|SASSOON|RUBY)$")


class Slot(Input):
    slot: str = Field(min_length=24, max_length=24, pattern="^[a-f0-9]+$")


class Login(Input):
    workspace: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=3, max_length=254, pattern="^[^ @]+@[^ @]+$")
    password: str = Field(min_length=1, max_length=128)
    role: Literal["patient", "dispatcher", "hospital", "driver", "nurse", "feed"] = "patient"


class Register(Input):
    workspace: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=3, max_length=254, pattern="^[^ @]+@[^ @]+$")
    password: str = Field(min_length=12, max_length=128)
    name: ShortName


class ApiError(BaseModel):
    code: str
    detail: str


class EventEnvelope(BaseModel):
    id: str
    seq: int
    incident_id: str | None
    mission_id: str | None
    kind: str
    reason: str
    occurred_at: datetime
    payload: dict
    actor_id: str | None = None


class EventsPage(BaseModel):
    events: list[EventEnvelope]
    next: int
    more: bool


class AccessSession(BaseModel):
    access_token: str
    slot: str
    expires_at: int


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    redis: bool
    mode: Literal["demo", "connected"]
    socket_path: str
