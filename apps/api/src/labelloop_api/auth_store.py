"""Transactional authentication; callers supply a trusted clock, never request actor claims."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import Engine, delete, insert, select, update

from labelloop_api.auth_schema import auth_event, principal, service_token, session

PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, type=Type.ID)
SESSION_LIFETIME = timedelta(hours=8)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def utc(value: datetime) -> datetime:
    # SQLite fixtures have naive UTC values; PostgreSQL returns aware timestamps.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class Principal:
    id: str
    name: str
    kind: str
    role: str

    @property
    def actor_kind(self) -> str:
        # Service authentication never confers the privileged system-policy actor.
        return "human" if self.kind == "human" else "model"

    def permits(self, permission: str) -> bool:
        if permission == "admin":
            return self.kind == "human" and self.role == "admin"
        if permission == "review":
            return self.kind == "human" and self.role in ("admin", "reviewer")
        return False


@dataclass(frozen=True)
class Authentication:
    principal: Principal
    csrf_hash: str | None = None


class AuthStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        # Unknown and known usernames both perform an Argon2 verification.
        self._dummy_hash = PASSWORDS.hash(secrets.token_urlsafe(32))

    def login(self, name: str, password: str, now: datetime) -> tuple[str, str] | None:
        with self.engine.begin() as connection:
            record = (
                connection.execute(
                    select(principal).where(principal.c.name == name).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            encoded = record["password_hash"] if record and record["kind"] == "human" else None
            try:
                valid = PASSWORDS.verify(encoded or self._dummy_hash, password)
            except (VerificationError, InvalidHashError):
                valid = False
            locked = bool(record and record["locked_until"] and utc(record["locked_until"]) > now)
            allowed = bool(record and encoded and valid and record["enabled"] and not locked)
            identity = record["id"] if record else None
            connection.execute(
                insert(auth_event).values(
                    id=str(uuid4()),
                    principal_id=identity,
                    occurred_at=now,
                    action="login_succeeded" if allowed else "login_failed",
                )
            )
            if not allowed:
                if record and record["kind"] == "human" and not locked:
                    previous = (
                        0 if record["locked_until"] is not None else record["failed_attempts"]
                    )
                    attempts = min(previous + 1, 5)
                    connection.execute(
                        update(principal)
                        .where(principal.c.id == identity)
                        .values(
                            failed_attempts=attempts,
                            locked_until=now + timedelta(minutes=15) if attempts >= 5 else None,
                        )
                    )
                return None
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            values = {"failed_attempts": 0, "locked_until": None}
            if PASSWORDS.check_needs_rehash(encoded):
                values["password_hash"] = PASSWORDS.hash(password)
            connection.execute(update(principal).where(principal.c.id == identity).values(**values))
            connection.execute(
                insert(session).values(
                    token_hash=digest(token),
                    principal_id=identity,
                    csrf_hash=digest(csrf),
                    expires_at=now + SESSION_LIFETIME,
                )
            )
            return token, csrf

    def authenticate(self, token: str, *, service: bool, now: datetime) -> Authentication | None:
        if len(token) != 43 or not all(c.isalnum() or c in "-_" for c in token):
            return None
        credentials = service_token if service else session
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(principal, credentials)
                    .join(credentials, credentials.c.principal_id == principal.c.id)
                    .where(
                        credentials.c.token_hash == digest(token),
                        credentials.c.expires_at > now,
                        principal.c.enabled.is_(True),
                        principal.c.kind == ("service" if service else "human"),
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return Authentication(
            Principal(row["id"], row["name"], row["kind"], row["role"]),
            None if service else row["csrf_hash"],
        )

    def event(self, actor: Principal, action: str, now: datetime) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(auth_event).values(
                    id=str(uuid4()),
                    principal_id=actor.id,
                    action=action,
                    occurred_at=now,
                )
            )

    def logout(self, token: str, actor: Principal, now: datetime) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                delete(session).where(
                    session.c.token_hash == digest(token),
                    session.c.principal_id == actor.id,
                )
            )
            connection.execute(
                insert(auth_event).values(
                    id=str(uuid4()),
                    principal_id=actor.id,
                    action="logout",
                    occurred_at=now,
                )
            )
