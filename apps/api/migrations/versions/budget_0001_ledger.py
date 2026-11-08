"""Project-serialized budget reservations, single-use dispatch and immutable audit."""

import sqlalchemy as sa
from alembic import op

revision = "budget_0001"
down_revision = "0002_local_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Frozen DDL: never import mutable application metadata into historical migrations.
    op.create_table(
        "budget_head",
        sa.Column("project_id", sa.String, sa.ForeignKey("project.project_key"), primary_key=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("policy", sa.JSON, nullable=False),
        sa.Column("pause_reason", sa.String),
        sa.CheckConstraint("revision > 0"),
        sa.CheckConstraint("pause_reason IN ('insufficient_credits', 'estimate_overrun')"),
    )
    op.create_table(
        "budget_dry_run",
        sa.Column(
            "project_id", sa.String, sa.ForeignKey("budget_head.project_id"), primary_key=True
        ),
        sa.Column("operation_id", sa.String, primary_key=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("policy_revision", sa.Integer, nullable=False),
        sa.Column("request", sa.JSON, nullable=False),
        sa.Column("intent", sa.JSON, nullable=False),
    )
    op.create_table(
        "budget_operation",
        sa.Column(
            "project_id", sa.String, sa.ForeignKey("budget_head.project_id"), primary_key=True
        ),
        sa.Column("operation_id", sa.String, primary_key=True),
        sa.Column("dry_run_digest", sa.String(64), nullable=False),
        sa.Column("policy_revision", sa.Integer, nullable=False),
        sa.Column("entry", sa.JSON, nullable=False),
        sa.Column("intent", sa.JSON, nullable=False),
        sa.Column("prices", sa.JSON),
        sa.Column("claim_id", sa.String(36), unique=True),
        sa.Column("claimed_by", sa.String),
        sa.Column("evidence", sa.JSON),
        sa.CheckConstraint("(claim_id IS NULL) = (claimed_by IS NULL)"),
    )
    op.create_table(
        "budget_event",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String, sa.ForeignKey("budget_head.project_id"), nullable=False),
        sa.Column("operation_id", sa.String),
        sa.Column("principal_id", sa.String(256), nullable=False),
        sa.Column("principal_kind", sa.String(8), nullable=False),
        sa.Column("principal_role", sa.String(32), nullable=False),
        sa.Column("principal_version", sa.String(64), nullable=False),
        sa.Column("policy_revision", sa.Integer, nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("record", sa.JSON, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("""
CREATE FUNCTION budget_immutable() RETURNS trigger
SET search_path FROM CURRENT AS $$
BEGIN
  RAISE EXCEPTION 'budget history and dispatch identity are immutable';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER budget_event_immutable BEFORE UPDATE OR DELETE ON budget_event
FOR EACH ROW EXECUTE FUNCTION budget_immutable();
CREATE TRIGGER budget_operation_no_delete BEFORE DELETE ON budget_operation
FOR EACH ROW EXECUTE FUNCTION budget_immutable();
CREATE TRIGGER budget_operation_identity BEFORE UPDATE ON budget_operation
FOR EACH ROW WHEN (
  OLD.project_id IS DISTINCT FROM NEW.project_id OR
  OLD.operation_id IS DISTINCT FROM NEW.operation_id OR
  OLD.dry_run_digest IS DISTINCT FROM NEW.dry_run_digest OR
  OLD.policy_revision IS DISTINCT FROM NEW.policy_revision OR
  OLD.intent::jsonb IS DISTINCT FROM NEW.intent::jsonb OR
  OLD.prices::jsonb IS DISTINCT FROM NEW.prices::jsonb OR
  OLD.entry::jsonb->'request' IS DISTINCT FROM NEW.entry::jsonb->'request' OR
  OLD.entry::jsonb->'approval' IS DISTINCT FROM NEW.entry::jsonb->'approval' OR
  OLD.entry::jsonb->'reserved_autonomously' IS DISTINCT FROM
    NEW.entry::jsonb->'reserved_autonomously' OR
  (OLD.claim_id IS NOT NULL AND (
    OLD.claim_id IS DISTINCT FROM NEW.claim_id OR
    OLD.claimed_by IS DISTINCT FROM NEW.claimed_by)) OR
  (OLD.evidence IS NOT NULL AND OLD.evidence::jsonb IS DISTINCT FROM NEW.evidence::jsonb)
) EXECUTE FUNCTION budget_immutable();
""")


def downgrade() -> None:
    for name in ("budget_event", "budget_operation", "budget_dry_run", "budget_head"):
        op.drop_table(name)
    op.execute("DROP FUNCTION budget_immutable()")
