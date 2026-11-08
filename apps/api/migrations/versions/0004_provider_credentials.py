"""Shared provider envelopes, validation intents and immutable secret audit lineage."""

import sqlalchemy as sa
from alembic import op

revision = "0004_provider_credentials"
# The authentication integration will attach this reserved revision after 0003.
# Until that integration, qualify independent storage on the delivered main schema.
down_revision = "0002_local_models"
branch_labels = None
depends_on = None

GUARDS = """
CREATE FUNCTION provider_event_immutable() RETURNS trigger
SET search_path FROM CURRENT AS $$
BEGIN
  RAISE EXCEPTION 'provider audit events are immutable';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER provider_event_no_update BEFORE UPDATE OR DELETE ON provider_secret_event
FOR EACH ROW EXECUTE FUNCTION provider_event_immutable();

CREATE FUNCTION provider_version_guard() RETURNS trigger
SET search_path FROM CURRENT AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'provider envelope lineage is immutable';
  END IF;
  IF (to_jsonb(NEW) - 'intent_state') IS DISTINCT FROM
     (to_jsonb(OLD) - 'intent_state') OR OLD.intent_state <> 'pending' OR
     NEW.intent_state NOT IN ('activated', 'rejected', 'stale', 'identity_unverifiable') THEN
    RAISE EXCEPTION 'only pending credential intents can be resolved';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER provider_version_no_rewrite BEFORE UPDATE OR DELETE ON provider_credential_version
FOR EACH ROW EXECUTE FUNCTION provider_version_guard();

CREATE FUNCTION provider_scope_guard() RETURNS trigger
SET search_path FROM CURRENT AS $$
BEGIN
  IF (NEW.id, NEW.project_id, NEW.workspace_id, NEW.secret_type) IS DISTINCT FROM
     (OLD.id, OLD.project_id, OLD.workspace_id, OLD.secret_type) OR
     NEW.latest_version < OLD.latest_version OR NEW.active_version < OLD.active_version THEN
    RAISE EXCEPTION 'provider connection scope and version lineage are immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER provider_scope_no_rewrite BEFORE UPDATE ON provider_connection
FOR EACH ROW EXECUTE FUNCTION provider_scope_guard();
"""


def upgrade() -> None:
    # Snapshot the DDL here rather than importing mutable application metadata.
    op.create_table(
        "provider_connection",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, nullable=False),
        sa.Column("workspace_id", sa.Uuid, nullable=False),
        sa.Column("secret_type", sa.String, nullable=False),
        sa.Column("latest_version", sa.Integer, nullable=False),
        sa.Column("active_version", sa.Integer, nullable=False),
        sa.CheckConstraint("secret_type IN ('llm', 'platform')"),
        sa.CheckConstraint("active_version >= 0 AND latest_version >= active_version"),
    )
    op.create_table(
        "provider_credential_version",
        sa.Column(
            "connection_id", sa.Uuid, sa.ForeignKey("provider_connection.id"), primary_key=True
        ),
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
    op.create_table(
        "provider_secret_event",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "connection_id", sa.Uuid, sa.ForeignKey("provider_connection.id"), nullable=False
        ),
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
    op.execute(GUARDS)


def downgrade() -> None:
    op.drop_table("provider_secret_event")
    op.drop_table("provider_credential_version")
    op.drop_table("provider_connection")
    op.execute("DROP FUNCTION provider_event_immutable()")
    op.execute("DROP FUNCTION provider_version_guard()")
    op.execute("DROP FUNCTION provider_scope_guard()")
