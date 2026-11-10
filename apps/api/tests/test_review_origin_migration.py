"""Review provenance survives updates/deletes; DB controls run in Actions smoke."""

import io
import runpy
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_review_origin_guard_is_narrow_reversible_and_preserves_history():
    migration = runpy.run_path(
        str(Path(__file__).parents[1] / "migrations/versions/qualification_0008_review_origins.py")
    )
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration["upgrade"]()
        migration["downgrade"]()
    sql = output.getvalue()
    assert migration["down_revision"] == "m5_auth_0001"
    assert "BEFORE UPDATE OR DELETE ON review_event" in sql
    assert "BEFORE TRUNCATE ON review_event" in sql
    assert "DROP TRIGGER qualification_review_no_update ON review_event" in sql
    assert "DROP TRIGGER qualification_review_no_truncate ON review_event" in sql
    assert "DROP FUNCTION qualification_review_immutable()" in sql
    assert "CREATE TABLE" not in sql
    assert "ALTER TABLE" not in sql
    assert "UPDATE review_event" not in sql
    assert "DELETE FROM review_event" not in sql
