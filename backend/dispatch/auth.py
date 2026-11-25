import hashlib
from dataclasses import dataclass
from datetime import timedelta
from secrets import token_hex, token_urlsafe

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from dispatch.config import now, settings
from dispatch.errors import Problem, require
from dispatch.models import AuthSession, Membership, User, Workspace

ISSUER = "smart-ambulance-dispatch"
AUDIENCE = "dispatch-browser-api"
COOKIE_PREFIX = "sads_refresh_"
COOKIE_PATH = "/api/auth"
passwords = PasswordHash.recommended()
DUMMY_PASSWORD = passwords.hash("credential timing probe")


@dataclass(frozen=True)
class Actor:
    id: str
    workspace_id: str
    role: str
    name: str
    workspace_name: str
    mode: str
    hospital_id: str | None
    unit_id: str | None
    slot: str
    expiry: int


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def issue(db: Session, member: Membership, session: AuthSession | None = None):
    at = now()
    raw = token_urlsafe(36)
    if session is None:
        session = AuthSession(id=token_hex(12), member_id=member.id, revoked=False)
        db.add(session)
    session.token_hash = digest(raw)
    session.expires_at = at + timedelta(seconds=settings.refresh_seconds)
    expires = at + timedelta(seconds=settings.access_seconds)
    access = jwt.encode({
        "sub": member.id, "ws": member.workspace_id, "sid": session.id,
        "iss": ISSUER, "aud": AUDIENCE, "iat": at, "exp": expires,
    }, settings.secret, algorithm="HS256")
    return {"access_token": access, "slot": session.id, "expires_at": int(expires.timestamp())}, raw


def resolve(db: Session, token: str) -> Actor:
    try:
        claims = jwt.decode(token, settings.secret, algorithms=["HS256"], issuer=ISSUER,
                            audience=AUDIENCE, options={
                                "require": ["sub", "ws", "sid", "iss", "aud", "exp", "iat"],
                            })
    except jwt.PyJWTError as exc:
        raise Problem(401, "invalid_access", "Sign in again to continue.") from exc

    row = db.execute(select(Membership, User, Workspace).join(
        User, User.id == Membership.user_id,
    ).join(Workspace, Workspace.id == Membership.workspace_id).join(
        AuthSession, AuthSession.member_id == Membership.id,
    ).where(
        Membership.id == claims["sub"], Membership.enabled.is_(True),
        Workspace.id == claims["ws"], Workspace.mode == settings.mode,
        AuthSession.id == claims["sid"], AuthSession.revoked.is_(False),
        AuthSession.expires_at > now(),
    )).first()
    require(row, "The session has ended.", "invalid_access", 401)
    member, user, workspace = row
    return Actor(member.id, workspace.id, member.role, user.name, workspace.name, workspace.mode,
                 member.hospital_id, member.unit_id, claims["sid"], claims["exp"])


def check_role(actor: Actor, *roles: str):
    require(actor.role in roles, "This role cannot perform that action.", "forbidden", 403)
