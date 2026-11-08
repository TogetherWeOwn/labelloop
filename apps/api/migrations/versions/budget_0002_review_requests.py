"""Bounded review-batch child requests; no live pricing or provider I/O."""

import sqlalchemy as sa
from alembic import op

revision = "budget_0002"
down_revision = "budget_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("budget_operation", sa.Column("review_pause", sa.String))
    op.create_check_constraint(
        "budget_review_pause", "budget_operation", "review_pause IN ('approval_exhausted')"
    )
    op.create_table(
        "budget_review_request",
        sa.Column("project_id", sa.String, primary_key=True),
        sa.Column("operation_id", sa.String, primary_key=True),
        sa.Column("request_id", sa.String, primary_key=True),
        sa.Column("image_id", sa.String, nullable=False),
        sa.Column("pass_index", sa.Integer, nullable=False),
        sa.Column("configuration_sha256", sa.String(64), nullable=False),
        sa.Column("forecast", sa.JSON, nullable=False),
        sa.Column("estimate", sa.BigInteger, nullable=False),
        sa.Column("claim_id", sa.String(36), nullable=False, unique=True),
        sa.Column("claimed_by", sa.String, nullable=False),
        sa.Column("state", sa.String, nullable=False),
        sa.Column("actual", sa.BigInteger),
        sa.Column("evidence", sa.JSON),
        sa.ForeignKeyConstraint(
            ["project_id", "operation_id"],
            ["budget_operation.project_id", "budget_operation.operation_id"],
        ),
        sa.UniqueConstraint("project_id", "operation_id", "image_id", "pass_index"),
        sa.CheckConstraint("estimate >= 0 AND pass_index >= 0"),
        sa.CheckConstraint("actual >= 0"),
        sa.CheckConstraint("(state = 'settled') = (actual IS NOT NULL)"),
        sa.CheckConstraint(
            "state IN ('dispatched', 'unconfirmed', 'settled', 'insufficient_credits')"
        ),
    )
    op.execute("""
CREATE TRIGGER budget_review_request_no_delete BEFORE DELETE ON budget_review_request
FOR EACH ROW EXECUTE FUNCTION budget_immutable();
CREATE TRIGGER budget_review_request_identity BEFORE UPDATE ON budget_review_request
FOR EACH ROW WHEN (
  OLD.project_id IS DISTINCT FROM NEW.project_id OR
  OLD.operation_id IS DISTINCT FROM NEW.operation_id OR
  OLD.request_id IS DISTINCT FROM NEW.request_id OR
  OLD.image_id IS DISTINCT FROM NEW.image_id OR
  OLD.pass_index IS DISTINCT FROM NEW.pass_index OR
  OLD.configuration_sha256 IS DISTINCT FROM NEW.configuration_sha256 OR
  OLD.forecast::jsonb IS DISTINCT FROM NEW.forecast::jsonb OR
  OLD.estimate IS DISTINCT FROM NEW.estimate OR
  OLD.claim_id IS DISTINCT FROM NEW.claim_id OR
  OLD.claimed_by IS DISTINCT FROM NEW.claimed_by OR
  (OLD.actual IS NOT NULL AND OLD.actual IS DISTINCT FROM NEW.actual) OR
  (OLD.evidence IS NOT NULL AND OLD.evidence::jsonb IS DISTINCT FROM NEW.evidence::jsonb)
) EXECUTE FUNCTION budget_immutable();
""")


def downgrade() -> None:
    op.drop_table("budget_review_request")
    op.drop_constraint("budget_review_pause", "budget_operation", type_="check")
    op.drop_column("budget_operation", "review_pause")
