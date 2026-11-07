"""Single-organization authentication records; never store bearer/session plaintext."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
)

metadata = MetaData()
principal = Table(
    "auth_principal",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(128), unique=True, nullable=False),
    Column("kind", String(8), nullable=False),
    Column("role", String(32), nullable=False),
    Column("password_hash", String),
    Column("enabled", Boolean, nullable=False),
    Column("failed_attempts", Integer, nullable=False),
    Column("locked_until", DateTime(timezone=True)),
    CheckConstraint("failed_attempts >= 0"),
    CheckConstraint(
        "(kind = 'human' AND role IN ('admin','reviewer') AND password_hash IS NOT NULL) OR "
        "(kind = 'service' AND role IN "
        "('worker-core','worker-ml','worker-llm','worker-platform') AND password_hash IS NULL)"
    ),
)
session = Table(
    "auth_session",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("principal_id", String(36), ForeignKey("auth_principal.id"), nullable=False),
    Column("csrf_hash", String(64), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)
service_token = Table(
    "auth_service_token",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("principal_id", String(36), ForeignKey("auth_principal.id"), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)
auth_event = Table(
    "auth_event",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("principal_id", String(36), ForeignKey("auth_principal.id")),
    Column("action", String(32), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "action IN ('login_failed','login_succeeded','logout','permission_denied','csrf_denied')"
    ),
)
