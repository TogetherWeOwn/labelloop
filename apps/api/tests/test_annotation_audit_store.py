"""Contract checks; PostgreSQL registry veto assertions run in Actions CPU smoke."""

import io
import runpy
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from labelloop_api import qualification_schema
from labelloop_api.annotation_audit_store import (
    record_annotation_audit,
    release_membership,
    training_ancestor_assets,
)


def lineage_run(initialization, checkpoints, assets):
    return (
        {
            "request": {"initialization_sha256": initialization},
            "checkpoints": [{"sha256": digest} for digest in checkpoints],
        },
        dict.fromkeys(assets, 1),
    )


def test_checkpoint_lineage_traces_multiple_generations_and_unselected_parents():
    runs = {
        "original": lineage_run("external-base", ("selected", "unselected"), ("bad",)),
        "intermediate": lineage_run("unselected", ("middle",), ("clean-a",)),
        "child": lineage_run("middle", ("child",), ("clean-b",)),
        "unrelated": lineage_run("external-base", ("other",), ("unrelated",)),
    }
    assert training_ancestor_assets(runs, "child") == {"bad", "clean-a", "clean-b"}
    assert training_ancestor_assets(runs, "unrelated") == {"unrelated"}


def test_checkpoint_lineage_includes_all_duplicate_hash_parents_and_bounds_cycles():
    runs = {
        "a": lineage_run("b", ("shared",), ("a",)),
        "b": lineage_run("shared", ("b",), ("b",)),
        "duplicate": lineage_run("external", ("shared",), ("adverse",)),
    }
    assert training_ancestor_assets(runs, "b") == {"a", "b", "adverse"}


def test_missing_project_training_lineage_fails_closed():
    with pytest.raises(ValueError, match="outside its project"):
        training_ancestor_assets({}, "foreign")


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
