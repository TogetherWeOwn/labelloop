"""Append-only selections and final-test lifecycle; no reset transition."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "qualification_0002"
down_revision = "qualification_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qualification_candidate_selection",
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), primary_key=True),
        sa.Column("selection_key", sa.String, primary_key=True),
        sa.Column("actor_id", sa.String, nullable=False),
        sa.Column("record", sa.JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("length(trim(actor_id)) > 0 AND length(trim(selection_key)) > 0"),
    )
    op.create_table(
        "qualification_final_test_event",
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), primary_key=True),
        sa.Column(
            "dataset_key",
            sa.String,
            sa.ForeignKey("canonical_dataset_release.release_key"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer, primary_key=True),
        sa.Column("actor_id", sa.String, nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("record", sa.JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("revision >= 0 AND length(trim(actor_id)) > 0"),
        sa.CheckConstraint("action IN ('register','expose','adapt','quarantine')"),
    )
    for table in ("qualification_candidate_selection", "qualification_final_test_event"):
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION model_record_immutable()"
        )


def downgrade() -> None:
    op.drop_table("qualification_final_test_event")
    op.drop_table("qualification_candidate_selection")
