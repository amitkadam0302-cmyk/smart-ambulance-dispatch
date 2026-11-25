"""Original schematic geometry. Names, coordinates, times and capacities are demo fixtures."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from dispatch.models import Capacity, Hospital, Membership, Unit, User, Workspace, uid

PICKUP = (18.67160, 73.89140)
PICKUP_NAME = "MIT ACSC College, Alandi"
ZONES = ("Alandi", "Central Pune", "Pimpri-Chinchwad")
HOSPITALS = (
    ("RUBY", "Ruby Hall Clinic", 18.53389, 73.87725, 24, 16, ("general", "trauma", "cardiac")),
    ("SASSOON", "Sassoon General Hospital", 18.52667, 73.87333, 30, 24,
     ("general", "trauma", "maternity", "stroke")),
    ("ALANDI", "Sant Dnyaneshwar Hospital Alandi", 18.6731, 73.8984, 12, 7,
     ("general", "trauma", "maternity")),
)
UNIT_FIXTURES = (
    ("AMB1", "RUBY", 2640, ("general", "advanced")),
    ("AMB2", "SASSOON", 2940, ("general", "advanced")),
    ("AMB3", "ALANDI", 300, ("general", "advanced")),
)
POSITIONS = {code: (lat, lng) for code, _, lat, lng, *_ in HOSPITALS}
PICKUP_TIMES = {POSITIONS[base]: seconds for _, base, seconds, _ in UNIT_FIXTURES}
ALANDI_MAIN = (
    POSITIONS["ALANDI"], (18.67287, 73.89742), (18.67245, 73.89651),
    (18.67268, 73.89553), (18.67232, 73.89479), (18.67244, 73.89368),
    (18.67160, 73.89240), PICKUP,
)
ROAD_LABELS = (
    (18.67355, 73.89500, "Hospital Road"),
    (18.67055, 73.89330, "College Road"),
    (18.67430, 73.89070, "Alandi"),
    (18.66940, 73.89715, "Indrayani River"),
)
SCHEMATIC = {
    "river": [(18.6682, 73.885), (18.6688, 73.8892), (18.6686, 73.8918),
              (18.6701, 73.8951), (18.67015, 73.8981), (18.6699, 73.9036)],
    "roads": [
        list(ALANDI_MAIN),
        [(18.6756, 73.8877), (18.6748, 73.8918), (18.6744, 73.8945),
         (18.6741, 73.8962), (18.6742, 73.9005), (18.6751, 73.903)],
        [(18.6767, 73.8968), (18.6741, 73.8962), (18.67245, 73.89651),
         (18.67155, 73.8961), (18.6701, 73.8951), (18.6679, 73.8948)],
        [(18.6716, 73.8879), PICKUP, (18.67125, 73.89436), (18.67155, 73.8961),
         (18.6713, 73.9006), (18.6715, 73.9033)],
        [(18.6765, 73.8923), (18.6748, 73.8918), (18.6730, 73.89221),
         (18.6716, 73.8924), (18.6699, 73.8922), (18.6672, 73.8925)],
        [(18.6772, 73.9007), (18.6742, 73.9005), POSITIONS["ALANDI"],
         (18.6713, 73.9006), (18.6681, 73.9012)],
    ],
    "labels": ROAD_LABELS,
}


def zone_for(lat: float, lng: float):
    if lat >= 18.625 and lng >= 73.86:
        return ZONES[0]
    return ZONES[2] if lat >= 18.60 else ZONES[1]


def add_member(db: Session, workspace: Workspace, role: str, name: str, **scopes):
    user = User(id=uid(), email=f"{uid()}@training.invalid", name=name)
    db.add(user)
    db.flush()
    member = Membership(id=uid(), workspace_id=workspace.id, user_id=user.id, role=role, **scopes)
    db.add(member)
    return member


def populate(db: Session, workspace: Workspace, at: datetime):
    add_member(db, workspace, "patient", "Demo patient")
    add_member(db, workspace, "dispatcher", "Demo dispatcher")
    hospitals = {}
    for code, name, lat, lng, total, occupied, capabilities in HOSPITALS:
        hospital = Hospital(id=uid(), workspace_id=workspace.id, code=code, name=name,
                            lat=lat, lng=lng, capabilities=list(capabilities))
        hospitals[code] = hospital
        db.add(hospital)
        db.flush()
        db.add(Capacity(hospital_id=hospital.id, total=total, occupied=occupied, reserved=0,
                        accepting=True, source="Example hospital report", reported_at=at))
        add_member(db, workspace, "hospital", f"Demo hospital · {code}", hospital_id=hospital.id)
    for name, base, _, capabilities in UNIT_FIXTURES:
        unit = Unit(id=uid(), workspace_id=workspace.id, hospital_id=hospitals[base].id,
                    name=name, capabilities=list(capabilities), checked_in=True)
        db.add(unit)
        db.flush()
        add_member(db, workspace, "driver", f"Demo driver · {name}", unit_id=unit.id)
        add_member(db, workspace, "nurse", f"Demo nurse · {name}", unit_id=unit.id)


def seed_template(db: Session, at: datetime):
    template = db.scalar(select(Workspace).where(Workspace.slug == "alandi-template"))
    if template is None:
        template = Workspace(id=uid(), slug="alandi-template", name="Alandi fixture template",
                             mode="template", created_at=at)
        db.add(template)
        db.flush()
        populate(db, template, at)
    return template
