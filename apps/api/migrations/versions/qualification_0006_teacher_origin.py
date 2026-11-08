"""Retain immutable exact-revision model-generated label provenance."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "qualification_0006"
down_revision = "qualification_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qualification_generated_label_origin",
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), nullable=False),
        sa.Column("asset_id", UUID, primary_key=True),
        sa.Column("revision", sa.Integer, primary_key=True),
        sa.Column("teacher_model_id", UUID, sa.ForeignKey("model_release.id"), primary_key=True),
        sa.Column("actor_id", sa.String, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["asset_id", "revision"],
            ["annotation_revision.asset_id", "annotation_revision.revision"],
        ),
        sa.CheckConstraint("revision >= 1 AND length(trim(actor_id)) > 0"),
    )
    op.create_index(
        "qualification_generated_label_project",
        "qualification_generated_label_origin",
        ["project_id"],
    )
    op.execute(
        "CREATE TRIGGER qualification_generated_label_no_update "
        "BEFORE UPDATE OR DELETE ON qualification_generated_label_origin "
        "FOR EACH ROW EXECUTE FUNCTION model_record_immutable()"
    )
    op.execute(
        "CREATE TRIGGER qualification_generated_label_no_truncate "
        "BEFORE TRUNCATE ON qualification_generated_label_origin "
        "FOR EACH STATEMENT EXECUTE FUNCTION model_record_immutable()"
    )


def downgrade() -> None:
    op.drop_table("qualification_generated_label_origin")
