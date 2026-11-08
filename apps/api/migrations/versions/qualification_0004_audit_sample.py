"""Immutable canonical audit populations and deterministic sample membership."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "qualification_0004"
down_revision = "m5_jobs_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qualification_audit_sample",
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), primary_key=True),
        sa.Column("sample_key", sa.String, primary_key=True),
        sa.Column(
            "release_id", UUID, sa.ForeignKey("canonical_dataset_release.id"), nullable=False
        ),
        sa.Column("actor_id", sa.String, nullable=False),
        sa.Column("population_sha256", sa.String(64), nullable=False),
        sa.Column("record", sa.JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("length(trim(actor_id)) > 0 AND length(trim(sample_key)) > 0"),
        sa.CheckConstraint("population_sha256 ~ '^[0-9a-f]{64}$'"),
    )
    op.execute(
        "CREATE TRIGGER qualification_audit_sample_no_update "
        "BEFORE UPDATE OR DELETE ON qualification_audit_sample "
        "FOR EACH ROW EXECUTE FUNCTION model_record_immutable()"
    )
    op.execute(
        "CREATE TRIGGER qualification_audit_sample_no_truncate "
        "BEFORE TRUNCATE ON qualification_audit_sample "
        "FOR EACH STATEMENT EXECUTE FUNCTION model_record_immutable()"
    )


def downgrade() -> None:
    op.drop_table("qualification_audit_sample")
