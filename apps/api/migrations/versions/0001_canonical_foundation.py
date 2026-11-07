"""Alembic's single migration revision; connection use is delegated to the runner."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from labelloop_api.schema import metadata

revision: str = "0001_canonical_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUARDS = """
CREATE FUNCTION annotation_revision_append_only() RETURNS trigger
SET search_path FROM CURRENT AS $$
BEGIN
  RAISE EXCEPTION 'annotation revisions are immutable; append with an expected parent';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER revision_no_update BEFORE UPDATE OR DELETE ON annotation_revision
FOR EACH ROW EXECUTE FUNCTION annotation_revision_append_only();

CREATE FUNCTION frozen_guard() RETURNS trigger SET search_path FROM CURRENT AS $$
BEGIN
  RAISE EXCEPTION 'frozen releases and locked split assignments are immutable';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER frozen_release_update BEFORE UPDATE OR DELETE ON canonical_dataset_release
FOR EACH ROW WHEN (OLD.frozen) EXECUTE FUNCTION frozen_guard();
CREATE TRIGGER locked_assignment_update BEFORE UPDATE OR DELETE ON source_split_assignment
FOR EACH ROW WHEN (OLD.locked) EXECUTE FUNCTION frozen_guard();

CREATE FUNCTION no_late_model_human_overwrite() RETURNS trigger
SET search_path FROM CURRENT AS $$
BEGIN
  IF NEW.actor_kind <> 'human' AND EXISTS (
    SELECT 1 FROM annotation_revision previous
    WHERE previous.asset_id = NEW.asset_id AND previous.revision = NEW.parent_revision
    AND (previous.review_tier = 'adjudicated' OR previous.approval_state = 'human_accepted')
  ) THEN
    RAISE EXCEPTION 'late model work cannot supersede human/adjudicated labels';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER protect_human_revision BEFORE INSERT ON annotation_revision
FOR EACH ROW EXECUTE FUNCTION no_late_model_human_overwrite();

CREATE FUNCTION expected_review_revision() RETURNS trigger
SET search_path FROM CURRENT AS $$
DECLARE current integer;
BEGIN
  PERFORM 1 FROM source_asset WHERE id = NEW.asset_id FOR SHARE;
  SELECT revision INTO current FROM annotation_revision
  WHERE asset_id = NEW.asset_id ORDER BY revision DESC LIMIT 1;
  IF current IS DISTINCT FROM NEW.expected_revision THEN
    RAISE EXCEPTION 'stale expected annotation revision';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER expected_revision BEFORE INSERT ON review_event
FOR EACH ROW EXECUTE FUNCTION expected_review_revision();

CREATE FUNCTION derived_variant_lineage() RETURNS trigger
SET search_path FROM CURRENT AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM yolo_dataset_variant variant
    JOIN canonical_dataset_release release ON release.id = variant.release_id
    WHERE variant.id = NEW.variant_id AND release.frozen AND variant.validated
    AND variant.non_empty_val
  ) THEN
    RAISE EXCEPTION 'derived samples must belong to a validated frozen release';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER derived_lineage BEFORE INSERT ON derived_sample
FOR EACH ROW EXECUTE FUNCTION derived_variant_lineage();
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    table_names = [item.name for item in metadata.sorted_tables]
    ordered = [
        *(name for name in table_names if name not in {"derived_sample", "yolo_dataset_variant"}),
        "yolo_dataset_variant",
        "derived_sample",
    ]
    for name in ordered:
        table = metadata.tables[name]
        op.create_table(
            table.name,
            *table.columns,
            *table.constraints,
            **{key: value for key, value in table.kwargs.items()},
        )
    op.execute(sa.text(GUARDS))


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS derived_lineage ON derived_sample;"
            "DROP FUNCTION IF EXISTS derived_variant_lineage;"
            "DROP TRIGGER IF EXISTS expected_revision ON review_event;"
            "DROP FUNCTION IF EXISTS expected_review_revision;"
            "DROP TRIGGER IF EXISTS protect_human_revision ON annotation_revision;"
            "DROP FUNCTION IF EXISTS no_late_model_human_overwrite;"
            "DROP TRIGGER IF EXISTS locked_assignment_update ON source_split_assignment;"
            "DROP TRIGGER IF EXISTS frozen_release_update ON canonical_dataset_release;"
            "DROP FUNCTION IF EXISTS frozen_guard;"
            "DROP TRIGGER IF EXISTS revision_no_update ON annotation_revision;"
            "DROP FUNCTION IF EXISTS annotation_revision_append_only;"
        )
    )
    for table in reversed(metadata.sorted_tables):
        op.drop_table(table.name)
