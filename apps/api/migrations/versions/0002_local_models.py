"""Immutable local training evidence and transactionally updated recipe heads."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_local_models"
down_revision = "0001_canonical_foundation"
branch_labels = None
depends_on = None

GUARDS = """
CREATE FUNCTION model_record_immutable() RETURNS trigger
SET search_path FROM CURRENT AS $$
BEGIN
  RAISE EXCEPTION 'model lineage and promotion events are immutable';
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION registry_recipe_scope() RETURNS trigger
SET search_path FROM CURRENT AS $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM model_release m
    WHERE m.id IN (NEW.champion_id, NEW.rollback_id)
      AND (m.project_id <> NEW.project_id OR m.recipe_id <> NEW.recipe_id)
  ) THEN
    RAISE EXCEPTION 'registry cannot cross project or recipe boundaries';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER registry_scope BEFORE INSERT OR UPDATE ON registry_head
FOR EACH ROW EXECUTE FUNCTION registry_recipe_scope();

CREATE FUNCTION model_input_immutable() RETURNS trigger
SET search_path FROM CURRENT AS $$
BEGIN
  RAISE EXCEPTION 'frozen model inputs are immutable';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_recipe BEFORE UPDATE OR DELETE ON preprocessing_recipe
FOR EACH ROW WHEN (OLD.immutable) EXECUTE FUNCTION model_input_immutable();
CREATE TRIGGER immutable_variant_delete BEFORE DELETE ON yolo_dataset_variant
FOR EACH ROW WHEN (OLD.validated) EXECUTE FUNCTION model_input_immutable();
CREATE TRIGGER immutable_variant_update BEFORE UPDATE ON yolo_dataset_variant
FOR EACH ROW WHEN (OLD.validated AND (
  OLD.release_id IS DISTINCT FROM NEW.release_id OR
  OLD.recipe_id IS DISTINCT FROM NEW.recipe_id OR
  OLD.class_map::jsonb IS DISTINCT FROM NEW.class_map::jsonb OR
  OLD.file_manifest::jsonb IS DISTINCT FROM NEW.file_manifest::jsonb OR
  OLD.validated IS DISTINCT FROM NEW.validated OR
  OLD.non_empty_val IS DISTINCT FROM NEW.non_empty_val
)) EXECUTE FUNCTION model_input_immutable();
"""
IMMUTABLE_TABLES = (
    "training_run",
    "evaluation_run",
    "model_release",
    "model_evaluation",
    "promotion_event",
)


def record_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def upgrade() -> None:
    # Historical DDL must not depend on application metadata from a later release.
    op.create_table(
        "training_run",
        *record_columns(),
        sa.Column("run_key", sa.String, unique=True, nullable=False),
        sa.Column("variant_id", UUID, sa.ForeignKey("yolo_dataset_variant.id"), nullable=False),
        sa.Column("input_manifest", sa.JSON, nullable=False),
        sa.Column("record", sa.JSON, nullable=False),
    )
    op.create_table(
        "evaluation_run",
        *record_columns(),
        sa.Column("run_key", sa.String, unique=True, nullable=False),
        sa.Column("training_run_id", UUID, sa.ForeignKey("training_run.id"), nullable=False),
        sa.Column("variant_id", UUID, sa.ForeignKey("yolo_dataset_variant.id"), nullable=False),
        sa.Column("record", sa.JSON, nullable=False),
    )
    op.create_table(
        "model_release",
        *record_columns(),
        sa.Column("release_key", sa.String, unique=True, nullable=False),
        sa.Column("training_run_id", UUID, sa.ForeignKey("training_run.id"), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), nullable=False),
        sa.Column("recipe_id", UUID, sa.ForeignKey("preprocessing_recipe.id"), nullable=False),
        sa.Column("record", sa.JSON, nullable=False),
        sa.Column("export_evidence", sa.JSON, nullable=False),
    )
    op.create_table(
        "model_evaluation",
        sa.Column("model_id", UUID, sa.ForeignKey("model_release.id"), primary_key=True),
        sa.Column("evaluation_id", UUID, sa.ForeignKey("evaluation_run.id"), primary_key=True),
    )
    op.create_table(
        "registry_head",
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), primary_key=True),
        sa.Column("recipe_id", UUID, sa.ForeignKey("preprocessing_recipe.id"), primary_key=True),
        sa.Column("revision", sa.Integer, nullable=False, server_default="0"),
        sa.Column("champion_id", UUID, sa.ForeignKey("model_release.id")),
        sa.Column("rollback_id", UUID, sa.ForeignKey("model_release.id")),
        sa.CheckConstraint("revision >= 0"),
    )
    op.create_table(
        "promotion_event",
        *record_columns(),
        sa.Column("project_id", UUID, sa.ForeignKey("project.id"), nullable=False),
        sa.Column("recipe_id", UUID, sa.ForeignKey("preprocessing_recipe.id"), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("actor_id", sa.String, nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("record", sa.JSON, nullable=False),
        sa.UniqueConstraint("project_id", "recipe_id", "revision"),
        sa.CheckConstraint("revision > 0 AND length(trim(actor_id)) > 0"),
        sa.CheckConstraint("action IN ('promote', 'rollback')"),
    )
    op.execute(GUARDS)
    for name in IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER model_no_update BEFORE UPDATE OR DELETE ON {name} "
            "FOR EACH ROW EXECUTE FUNCTION model_record_immutable()"
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER immutable_recipe ON preprocessing_recipe")
    op.execute("DROP TRIGGER immutable_variant_delete ON yolo_dataset_variant")
    op.execute("DROP TRIGGER immutable_variant_update ON yolo_dataset_variant")
    op.execute("DROP FUNCTION model_input_immutable()")
    op.execute("DROP TRIGGER registry_scope ON registry_head")
    op.execute("DROP FUNCTION registry_recipe_scope()")
    for name in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER model_no_update ON {name}")
    op.execute("DROP FUNCTION model_record_immutable()")
    for name in (
        "promotion_event",
        "registry_head",
        "model_evaluation",
        "model_release",
        "evaluation_run",
        "training_run",
    ):
        op.drop_table(name)
