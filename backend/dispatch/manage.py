"""Administrative commands run only with direct access to the application's database."""

import argparse
import getpass

from sqlalchemy import select, text

from dispatch.auth import passwords
from dispatch.config import now, settings
from dispatch.db import Session
from dispatch.models import Hospital, Membership, Unit, User, Workspace, uid
from fixtures.alandi import seed_template


def do_seed(_args):
    if settings.mode != "demo":
        print("APP_MODE=connected. Skipping demo seed.")
        return
    with Session.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(241125802)"))
        workspace = seed_template(db, now())
    print(f"Fixture template ready: {workspace.slug}. Each group gets its own seeded records.")


def do_workspace(args):
    with Session.begin() as db:
        db.add(Workspace(slug=args.slug, name=args.name, mode="connected"))
    print(f"Connected workspace created: {args.slug}")


def workspace_by_slug(db, slug):
    workspace = db.scalar(select(Workspace).where(Workspace.slug == slug, Workspace.mode == "connected"))
    if not workspace:
        raise SystemExit(f"Connected workspace {slug} was not found.")
    return workspace


def by_code(db, cls, workspace, field, value):
    row = db.scalar(select(cls).where(cls.workspace_id == workspace.id, field == value))
    if not row:
        raise SystemExit(f"Could not find {value} in the connected workspace.")
    return row


def do_hospital(args):
    if not (-90 <= args.lat <= 90 and -180 <= args.lng <= 180):
        raise SystemExit("Hospital coordinates are outside the supported range.")
    with Session.begin() as db:
        ws = workspace_by_slug(db, args.workspace)
        hospital = Hospital(id=uid(), workspace_id=ws.id, code=args.code, name=args.name, lat=args.lat,
                            lng=args.lng, capabilities=args.capabilities.split(","), feed_enabled=args.enable_feed)
        db.add(hospital)
    print(f"Hospital created with ID {hospital.id}. Sign in as hospital staff to submit a named capacity report.")


def do_unit(args):
    with Session.begin() as db:
        ws = workspace_by_slug(db, args.workspace)
        hospital = by_code(db, Hospital, ws, Hospital.code, args.hospital)
        unit = Unit(id=uid(), workspace_id=ws.id, hospital_id=hospital.id, name=args.name,
                    checked_in=False, status="offline", capabilities=args.capabilities.split(","))
        db.add(unit)
    print(f"Unit {unit.name} provisioned offline; confirm availability from the dispatcher workspace.")


def do_member(args):
    password = getpass.getpass("Password (minimum 12 characters): ")
    if len(password) < 12 or len(password) > 128:
        raise SystemExit("Use 12 to 128 characters.")
    with Session.begin() as db:
        ws = workspace_by_slug(db, args.workspace)
        if db.scalar(select(User.id).where(User.email == args.email.lower())):
            raise SystemExit("This email already exists. Use a separate staff account for this membership.")
        hospital_id, unit_id = None, None
        if args.role in ("hospital", "feed"):
            if not args.hospital:
                raise SystemExit("Pass --hospital for a hospital-scoped account.")
            hospital = by_code(db, Hospital, ws, Hospital.code, args.hospital)
            if args.role == "feed" and not hospital.feed_enabled:
                raise SystemExit("Enable the feed when provisioning this hospital.")
            hospital_id = hospital.id
        if args.role in ("nurse", "driver"):
            if not args.unit:
                raise SystemExit("Pass --unit for a unit-scoped account.")
            unit_id = by_code(db, Unit, ws, Unit.name, args.unit).id
        user = User(id=uid(), email=args.email.lower(), name=args.name, password_hash=passwords.hash(password))
        db.add(user)
        db.flush()
        db.add(Membership(workspace_id=ws.id, user_id=user.id, role=args.role, hospital_id=hospital_id, unit_id=unit_id))
    print(f"Provisioned {args.role} membership for {args.email.lower()}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    commands.add_parser("seed-demo").set_defaults(run=do_seed)
    ws = commands.add_parser("workspace")
    ws.add_argument("slug")
    ws.add_argument("name")
    ws.set_defaults(run=do_workspace)
    h = commands.add_parser("hospital")
    for field in ("workspace", "code", "name"):
        h.add_argument(field)
    h.add_argument("lat", type=float)
    h.add_argument("lng", type=float)
    h.add_argument("--capabilities", default="general")
    h.add_argument("--enable-feed", action="store_true")
    h.set_defaults(run=do_hospital)
    unit = commands.add_parser("unit")
    for field in ("workspace", "hospital", "name"):
        unit.add_argument(field)
    unit.add_argument("--capabilities", default="general")
    unit.set_defaults(run=do_unit)
    member = commands.add_parser("member")
    for field in ("workspace", "email", "name"):
        member.add_argument(field)
    member.add_argument("role", choices=("dispatcher", "hospital", "driver", "nurse", "feed"))
    member.add_argument("--hospital")
    member.add_argument("--unit")
    member.set_defaults(run=do_member)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
