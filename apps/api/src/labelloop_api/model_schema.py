"""Training and registry tables; separate metadata keeps the canonical migration frozen."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from labelloop_api.schema import preprocessing_recipe, project, yolo_dataset_variant

metadata = sa.MetaData()


def record_table(name: str, *columns: sa.Column, constraints: tuple = ()) -> sa.Table:
    return sa.Table(
        name,
        metadata,
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        *columns,
        *constraints,
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


training_run = record_table(
    "training_run",
    sa.Column("run_key", sa.String, unique=True, nullable=False),
    sa.Column("variant_id", UUID, sa.ForeignKey(yolo_dataset_variant.c.id), nullable=False),
    sa.Column("input_manifest", sa.JSON, nullable=False),
    sa.Column("record", sa.JSON, nullable=False),
)
evaluation_run = record_table(
    "evaluation_run",
    sa.Column("run_key", sa.String, unique=True, nullable=False),
    sa.Column("training_run_id", UUID, sa.ForeignKey(training_run.c.id), nullable=False),
    sa.Column("variant_id", UUID, sa.ForeignKey(yolo_dataset_variant.c.id), nullable=False),
    sa.Column("record", sa.JSON, nullable=False),
)
model_release = record_table(
    "model_release",
    sa.Column("release_key", sa.String, unique=True, nullable=False),
    sa.Column("training_run_id", UUID, sa.ForeignKey(training_run.c.id), nullable=False),
    sa.Column("project_id", UUID, sa.ForeignKey(project.c.id), nullable=False),
    sa.Column("recipe_id", UUID, sa.ForeignKey(preprocessing_recipe.c.id), nullable=False),
    sa.Column("record", sa.JSON, nullable=False),
    sa.Column("export_evidence", sa.JSON, nullable=False),
)
model_evaluation = sa.Table(
    "model_evaluation",
    metadata,
    sa.Column("model_id", UUID, sa.ForeignKey(model_release.c.id), primary_key=True),
    sa.Column("evaluation_id", UUID, sa.ForeignKey(evaluation_run.c.id), primary_key=True),
)
registry_head = sa.Table(
    "registry_head",
    metadata,
    sa.Column("project_id", UUID, sa.ForeignKey(project.c.id), primary_key=True),
    sa.Column("recipe_id", UUID, sa.ForeignKey(preprocessing_recipe.c.id), primary_key=True),
    sa.Column("revision", sa.Integer, nullable=False, server_default="0"),
    sa.Column("champion_id", UUID, sa.ForeignKey(model_release.c.id)),
    sa.Column("rollback_id", UUID, sa.ForeignKey(model_release.c.id)),
    sa.CheckConstraint("revision >= 0"),
)
promotion_event = record_table(
    "promotion_event",
    sa.Column("project_id", UUID, sa.ForeignKey(project.c.id), nullable=False),
    sa.Column("recipe_id", UUID, sa.ForeignKey(preprocessing_recipe.c.id), nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("actor_id", sa.String, nullable=False),
    sa.Column("action", sa.String, nullable=False),
    sa.Column("record", sa.JSON, nullable=False),
    constraints=(
        sa.UniqueConstraint("project_id", "recipe_id", "revision"),
        sa.CheckConstraint("revision > 0 AND length(trim(actor_id)) > 0"),
        sa.CheckConstraint("action IN ('promote', 'rollback')"),
    ),
)
