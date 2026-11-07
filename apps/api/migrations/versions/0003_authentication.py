"""Server-side sessions and restricted service principals, with no default accounts."""

import sqlalchemy as sa
from alembic import op

revision = "0003_authentication"
down_revision = "0002_local_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_principal",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("password_hash", sa.String),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("failed_attempts", sa.Integer, nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.CheckConstraint("failed_attempts >= 0"),
        sa.CheckConstraint(
            "(kind = 'human' AND role IN ('admin','reviewer') AND password_hash IS NOT NULL) OR "
            "(kind = 'service' AND role IN "
            "('worker-core','worker-ml','worker-llm','worker-platform') AND password_hash IS NULL)"
        ),
    )
    op.create_table(
        "auth_session",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "principal_id", sa.String(36), sa.ForeignKey("auth_principal.id"), nullable=False
        ),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "auth_service_token",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "principal_id", sa.String(36), sa.ForeignKey("auth_principal.id"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "auth_event",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("principal_id", sa.String(36), sa.ForeignKey("auth_principal.id")),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('login_failed','login_succeeded','logout',"
            "'permission_denied','csrf_denied')"
        ),
    )
    op.execute("""
        CREATE FUNCTION auth_event_immutable() RETURNS trigger
        SET search_path FROM CURRENT AS $$
        BEGIN
          RAISE EXCEPTION 'authentication events are immutable';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER auth_event_no_update BEFORE UPDATE OR DELETE ON auth_event
        FOR EACH ROW EXECUTE FUNCTION auth_event_immutable();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER auth_event_no_update ON auth_event")
    op.execute("DROP FUNCTION auth_event_immutable()")
    for name in ("auth_event", "auth_service_token", "auth_session", "auth_principal"):
        op.drop_table(name)
