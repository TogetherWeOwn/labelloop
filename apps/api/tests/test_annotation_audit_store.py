"""Contract checks; PostgreSQL registry veto assertions run in Actions CPU smoke."""

import io
import runpy
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from labelloop_api import qualification_schema
from labelloop_api.annotation_audit_store import record_annotation_audit, release_membership


@pytest.mark.parametrize(
    "value", [[], None, {"": 1}, {"asset": True}, {"asset": 0}, {"asset": "1"}]
)
def test_untrusted_annotation_membership_fails_closed(value):
    with pytest.raises(ValueError, match="asset keys to revisions"):
        release_membership(value)


def test_annotation_membership_preserves_exact_revision_numbers():
    assert release_membership({"asset-a": 1, "asset-b": 3}) == {"asset-a": 1, "asset-b": 3}


def test_audit_needs_actor_and_observations_before_opening_transaction():
    with pytest.raises(ValueError, match="authenticated actor"):
        record_annotation_audit(
            None,
            project_key="project",
            release_key="release",
            audit_key="audit",
            actor_id="",
            observations=(),
        )


def test_audit_migration_has_frozen_ddl_and_immutable_evidence():
    migration = runpy.run_path(
        str(
            Path(__file__).parents[1] / "migrations/versions/qualification_0001_annotation_audit.py"
        )
    )
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    future = sa.Column("future_column", sa.String)
    qualification_schema.annotation_audit.append_column(future)
    try:
        with Operations.context(context):
            migration["upgrade"]()
            migration["downgrade"]()
    finally:
        qualification_schema.annotation_audit._columns.remove(future)
    sql = output.getvalue()
    assert "future_column" not in sql
    assert "CREATE TABLE qualification_annotation_audit" in sql
    assert "DROP TABLE qualification_annotation_audit" in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "model_record_immutable()" in sql
    assert migration["down_revision"] == "budget_0002"
