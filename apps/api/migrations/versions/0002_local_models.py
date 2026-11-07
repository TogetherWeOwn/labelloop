"""Immutable local training evidence and transactionally updated recipe heads."""

from alembic import op

from labelloop_api.model_schema import metadata

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


def upgrade() -> None:
    metadata.create_all(op.get_bind())
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
    metadata.drop_all(op.get_bind())
