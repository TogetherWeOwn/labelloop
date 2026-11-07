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
    op.execute("DROP TRIGGER registry_scope ON registry_head")
    op.execute("DROP FUNCTION registry_recipe_scope()")
    for name in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER model_no_update ON {name}")
    op.execute("DROP FUNCTION model_record_immutable()")
    metadata.drop_all(op.get_bind())
