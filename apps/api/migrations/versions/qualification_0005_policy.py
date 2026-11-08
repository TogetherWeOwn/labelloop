"""Append-only current qualification policy, pipeline identity and supported scope."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "qualification_0005"
down_revision = "qualification_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qualification_policy_event",
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), primary_key=True),
        sa.Column("recipe_id", UUID, sa.ForeignKey("preprocessing_recipe.id"), primary_key=True),
        sa.Column("revision", sa.Integer, primary_key=True),
        sa.Column("actor_id", sa.String, nullable=False),
        sa.Column("record", sa.JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("revision >= 1 AND length(trim(actor_id)) > 0"),
    )
    op.execute(
        "CREATE TRIGGER qualification_policy_no_update "
        "BEFORE UPDATE OR DELETE ON qualification_policy_event "
        "FOR EACH ROW EXECUTE FUNCTION model_record_immutable()"
    )
    op.execute(
        "CREATE TRIGGER qualification_policy_no_truncate "
        "BEFORE TRUNCATE ON qualification_policy_event "
        "FOR EACH STATEMENT EXECUTE FUNCTION model_record_immutable()"
    )


def downgrade() -> None:
    op.drop_table("qualification_policy_event")
