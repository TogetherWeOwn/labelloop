"""Transaction-bound encrypted storage. Internal ports, never HTTP authorization.

The caller owns a transaction and supplies trusted project/workspace grants. Public
reads use an explicit projection. No plaintext enters SQL or stored job payloads.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa

from labelloop_credentials.envelope import (
    CredentialError,
    Envelope,
    Fingerprint,
    Identity,
    PublicCredential,
    SecretType,
)

metadata = sa.MetaData()
connection_head = sa.Table(
    "provider_connection",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("project_id", sa.Uuid, nullable=False),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("secret_type", sa.String, nullable=False),
    sa.Column("latest_version", sa.Integer, nullable=False),
    sa.Column("active_version", sa.Integer, nullable=False),
    sa.CheckConstraint("secret_type IN ('llm', 'platform')"),
    sa.CheckConstraint("active_version >= 0 AND latest_version >= active_version"),
)
credential_version = sa.Table(
    "provider_credential_version",
    metadata,
    sa.Column("connection_id", sa.Uuid, sa.ForeignKey("provider_connection.id"), primary_key=True),
    sa.Column("version", sa.Integer, primary_key=True),
    sa.Column("expected_active", sa.Integer, nullable=False),
    sa.Column("wrapping_key_id", sa.String(64), nullable=False),
    sa.Column("last_four", sa.String(4), nullable=False),
    sa.Column("sha256_prefix", sa.String(12), nullable=False),
    sa.Column("nonce", sa.LargeBinary, nullable=False),
    sa.Column("wrapped_key", sa.LargeBinary, nullable=False),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
    sa.Column("format_version", sa.Integer, nullable=False),
    sa.Column("intent_state", sa.String, nullable=False),
    sa.CheckConstraint("version > 0 AND expected_active >= 0 AND format_version = 1"),
    sa.CheckConstraint(
        "intent_state IN ('pending', 'activated', 'rejected', 'stale', 'identity_unverifiable')"
    ),
)
audit_event = sa.Table(
    "provider_secret_event",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("connection_id", sa.Uuid, sa.ForeignKey("provider_connection.id"), nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("actor_id", sa.String, nullable=False),
    sa.Column("action", sa.String, nullable=False),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    ),
    sa.CheckConstraint(
        "action IN ('validation_requested', 'activated', 'rejected', 'stale', "
        "'identity_unverifiable')"
    ),
)


@dataclass(frozen=True)
class AuthorizedScope:
    """Construct only from authenticated policy/configuration, never model/job text."""

    project_id: UUID
    workspace_id: UUID
    secret_type: SecretType

    def identity(self, connection_id: UUID, version: int) -> Identity:
        return Identity(
            self.project_id, self.workspace_id, connection_id, version, self.secret_type
        )


class CredentialStore:
    def __init__(self, connection: sa.Connection) -> None:
        self._db = connection

    def _head(self, scope: AuthorizedScope, connection_id: UUID) -> sa.RowMapping:
        row = (
            self._db.execute(
                sa.select(connection_head)
                .where(
                    connection_head.c.id == connection_id,
                    connection_head.c.project_id == scope.project_id,
                    connection_head.c.workspace_id == scope.workspace_id,
                    connection_head.c.secret_type == scope.secret_type.value,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise CredentialError()
        return row

    def _event(self, identity: Identity, actor_id: str, action: str) -> None:
        if not actor_id or len(actor_id) > 128 or not actor_id.strip():
            raise CredentialError()
        self._db.execute(
            audit_event.insert().values(
                id=uuid4(),
                connection_id=identity.connection_id,
                version=identity.version,
                actor_id=actor_id,
                action=action,
            )
        )

    def stage(self, envelope: Envelope, *, expected_active: int, actor_id: str) -> PublicCredential:
        """Persist validation/rotation intent; activation is a separate worker operation.

        The authenticated admin integration must authorize the identity before calling.
        A new connection starts at version 1; retries cannot replace an existing envelope.
        """
        i = envelope.identity
        scope = AuthorizedScope(i.project_id, i.workspace_id, i.secret_type)
        with self._db.begin_nested():
            if i.version == 1:
                if expected_active != 0:
                    raise CredentialError()
                self._db.execute(
                    connection_head.insert().values(
                        id=i.connection_id,
                        project_id=i.project_id,
                        workspace_id=i.workspace_id,
                        secret_type=i.secret_type.value,
                        latest_version=0,
                        active_version=0,
                    )
                )
            head = self._head(scope, i.connection_id)
            if head["active_version"] != expected_active:
                raise CredentialError()
            updated = self._db.execute(
                connection_head.update()
                .where(
                    connection_head.c.id == i.connection_id,
                    connection_head.c.latest_version == i.version - 1,
                    connection_head.c.active_version == expected_active,
                )
                .values(latest_version=i.version)
            )
            if updated.rowcount != 1:
                raise CredentialError()
            self._db.execute(
                credential_version.insert().values(
                    connection_id=i.connection_id,
                    version=i.version,
                    expected_active=expected_active,
                    wrapping_key_id=envelope.wrapping_key_id,
                    last_four=envelope.fingerprint.last_four,
                    sha256_prefix=envelope.fingerprint.sha256_prefix,
                    nonce=envelope.nonce,
                    wrapped_key=envelope.wrapped_key,
                    ciphertext=envelope.ciphertext,
                    format_version=envelope.format_version,
                    intent_state="pending",
                )
            )
            self._event(i, actor_id, "validation_requested")
        return PublicCredential(i, envelope.fingerprint)

    def public(self, scope: AuthorizedScope, connection_id: UUID) -> tuple[PublicCredential, ...]:
        self._head(scope, connection_id)
        rows = self._db.execute(
            sa.select(
                credential_version.c.version,
                credential_version.c.last_four,
                credential_version.c.sha256_prefix,
            )
            .where(credential_version.c.connection_id == connection_id)
            .order_by(credential_version.c.version)
        ).mappings()
        return tuple(
            PublicCredential(
                scope.identity(connection_id, r["version"]),
                Fingerprint(r["last_four"], r["sha256_prefix"]),
            )
            for r in rows
        )

    def lookup(
        self, scope: AuthorizedScope, connection_id: UUID, *, pending_version: int | None = None
    ) -> Envelope:
        head = self._head(scope, connection_id)
        version = pending_version if pending_version is not None else head["active_version"]
        row = (
            self._db.execute(
                sa.select(credential_version).where(
                    credential_version.c.connection_id == connection_id,
                    credential_version.c.version == version,
                    credential_version.c.intent_state
                    == ("pending" if pending_version is not None else "activated"),
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise CredentialError()
        return Envelope(
            scope.identity(connection_id, version),
            row["wrapping_key_id"],
            Fingerprint(row["last_four"], row["sha256_prefix"]),
            row["nonce"],
            row["wrapped_key"],
            row["ciphertext"],
            row["format_version"],
        )

    def finish_validation(
        self,
        scope: AuthorizedScope,
        connection_id: UUID,
        version: int,
        *,
        valid: bool,
        actor_id: str,
        denial: str | None = None,
    ) -> str:
        """Worker-only persistence port, not proof of provider identity/permission.

        A stale validation cannot roll back a newer key. Denials are bounded codes,
        never provider response text or exception messages.
        """
        if type(valid) is not bool:
            raise CredentialError()
        if denial is not None and (
            denial != "identity_unverifiable" or valid or scope.secret_type != SecretType.PLATFORM
        ):
            raise CredentialError()
        with self._db.begin_nested():
            head = self._head(scope, connection_id)
            row = (
                self._db.execute(
                    sa.select(credential_version)
                    .where(
                        credential_version.c.connection_id == connection_id,
                        credential_version.c.version == version,
                        credential_version.c.intent_state == "pending",
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise CredentialError()
            state = denial if denial is not None else "rejected"
            if valid:
                state = "stale"
                if head["active_version"] == row["expected_active"]:
                    result = self._db.execute(
                        connection_head.update()
                        .where(
                            connection_head.c.id == connection_id,
                            connection_head.c.active_version == row["expected_active"],
                        )
                        .values(active_version=version)
                    )
                    if result.rowcount == 1:
                        state = "activated"
            result = self._db.execute(
                credential_version.update()
                .where(
                    credential_version.c.connection_id == connection_id,
                    credential_version.c.version == version,
                    credential_version.c.intent_state == "pending",
                )
                .values(intent_state=state)
            )
            if result.rowcount != 1:
                raise CredentialError()
            self._event(scope.identity(connection_id, version), actor_id, state)
        return state
