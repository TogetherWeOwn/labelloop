"""Persist immutable measured annotation audit and candidate-bound canary outcomes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "qualification_0007"
down_revision = "qualification_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qualification_annotation_evidence",
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), primary_key=True),
        sa.Column("evidence_key", sa.String, primary_key=True),
        sa.Column(
            "audit_id", UUID, sa.ForeignKey("qualification_annotation_audit.id"), nullable=False
        ),
        sa.Column("actor_id", sa.String, nullable=False),
        sa.Column("record", sa.JSON, nullable=False),
        sa.Column("canary_model_id", UUID, sa.ForeignKey("model_release.id"), nullable=True),
        sa.Column("canary_release_sha256", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("length(trim(actor_id)) > 0 AND length(trim(evidence_key)) > 0"),
        sa.CheckConstraint("(canary_model_id IS NULL) = (canary_release_sha256 IS NULL)"),
        sa.CheckConstraint(
            "canary_release_sha256 IS NULL OR canary_release_sha256 ~ '^[0-9a-f]{64}$'"
        ),
    )
    op.execute(
        "CREATE TRIGGER qualification_annotation_evidence_no_update "
        "BEFORE UPDATE OR DELETE ON qualification_annotation_evidence "
        "FOR EACH ROW EXECUTE FUNCTION model_record_immutable()"
    )
    op.execute(
        "CREATE TRIGGER qualification_annotation_evidence_no_truncate "
        "BEFORE TRUNCATE ON qualification_annotation_evidence "
        "FOR EACH STATEMENT EXECUTE FUNCTION model_record_immutable()"
    )


def downgrade() -> None:
    op.drop_table("qualification_annotation_evidence")
