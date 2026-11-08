"""Append-only scoped drift pauses and incident-linked resumptions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "qualification_0003"
down_revision = "qualification_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qualification_acceptance_event",
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), primary_key=True),
        sa.Column("recipe_id", UUID, sa.ForeignKey("preprocessing_recipe.id"), primary_key=True),
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
        sa.CheckConstraint("revision >= 1 AND length(trim(actor_id)) > 0"),
        sa.CheckConstraint("action IN ('pause','resume')"),
    )
    op.execute(
        "CREATE TRIGGER qualification_acceptance_event_no_update "
        "BEFORE UPDATE OR DELETE ON qualification_acceptance_event "
        "FOR EACH ROW EXECUTE FUNCTION model_record_immutable()"
    )


def downgrade() -> None:
    op.drop_table("qualification_acceptance_event")
