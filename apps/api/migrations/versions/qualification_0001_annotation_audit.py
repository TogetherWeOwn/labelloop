"""Persist annotation audit vetoes independently of candidate qualification samples."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "qualification_0001"
down_revision = "budget_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qualification_annotation_audit",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), nullable=False),
        sa.Column(
            "release_id", UUID, sa.ForeignKey("canonical_dataset_release.id"), nullable=False
        ),
        sa.Column("audit_key", sa.String, nullable=False),
        sa.Column("actor_id", sa.String, nullable=False),
        sa.Column("record", sa.JSON, nullable=False),
        sa.Column("vetoed_revisions", sa.JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("project_id", "audit_key"),
        sa.CheckConstraint("length(trim(actor_id)) > 0 AND length(trim(audit_key)) > 0"),
    )
    op.execute(
        "CREATE TRIGGER qualification_audit_no_update BEFORE UPDATE OR DELETE "
        "ON qualification_annotation_audit "
        "FOR EACH ROW EXECUTE FUNCTION model_record_immutable()"
    )


def downgrade() -> None:
    op.drop_table("qualification_annotation_audit")
