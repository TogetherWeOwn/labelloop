"""Drift journal boundaries; real PostgreSQL transitions run in Actions ML smoke."""

import io
import json
import runpy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from labelloop_api import acceptance_store, qualification_schema
from labelloop_api.acceptance_store import load_acceptance, pause_acceptance, resume_acceptance
from labelloop_core.drift import AcceptanceState
from labelloop_core.evaluation_lifecycle import EvaluationSet, FinalTestState
from labelloop_core.qualification_wire import dumps


@pytest.mark.parametrize("absent", [True, False])
def test_acceptance_loader_decodes_expected_root_or_starts_empty(absent):
    state = AcceptanceState("project", "recipe")
    connection = Mock()
    connection.execute.return_value.mappings.return_value.one_or_none.return_value = (
        None if absent else {"record": json.loads(dumps(state)), "revision": 0}
    )
    with patch.object(acceptance_store, "_scope", return_value=("p-id", "r-id")):
        assert load_acceptance(connection, project_key="project", recipe_key="recipe") == state


@pytest.mark.parametrize(
    "record,revision",
    [
        (AcceptanceState("other", "recipe"), 0),
        (AcceptanceState("project", "other"), 0),
        (replace(AcceptanceState("project", "recipe"), revision=1), 0),
        (FinalTestState(EvaluationSet("test", "project", ("group",), "locked_final_test")), 0),
    ],
)
def test_acceptance_loader_rejects_wrong_identity_revision_and_root(record, revision):
    connection = Mock()
    connection.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "record": json.loads(dumps(record)),
        "revision": revision,
    }
    with (
        patch.object(acceptance_store, "_scope", return_value=("p-id", "r-id")),
        pytest.raises(ValueError),
    ):
        load_acceptance(connection, project_key="project", recipe_key="recipe")


@pytest.mark.parametrize(
    "operation,kwargs",
    [
        (
            pause_acceptance,
            {"pause_id": "drift", "qualification_id": "q", "scope": (), "reason": "layout"},
        ),
        (resume_acceptance, {"pause_ids": ("drift",), "evaluation_key": "report"}),
    ],
)
def test_drift_writer_rejects_empty_actor_before_database_access(operation, kwargs):
    with pytest.raises(ValueError, match="authenticated actor"):
        operation(
            None,
            project_key="project",
            recipe_key="recipe",
            actor_id=" ",
            expected_revision=0,
            **kwargs,
        )


def test_acceptance_cannot_enable_itself_without_a_trusted_qualification():
    with pytest.raises(ValueError, match="trusted qualification"):
        acceptance_store._qualified_champion(None, "project", "recipe", "report", None)


def test_acceptance_migration_is_frozen_and_append_only():
    migration = runpy.run_path(
        str(Path(__file__).parents[1] / "migrations/versions/qualification_0003_acceptance.py")
    )
    table = qualification_schema.acceptance_event
    future = sa.Column("future_column", sa.String)
    table.append_column(future)
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    try:
        with Operations.context(context):
            migration["upgrade"]()
            migration["downgrade"]()
    finally:
        table._columns.remove(future)
    sql = output.getvalue()
    assert "future_column" not in sql
    assert "PRIMARY KEY (project_id, recipe_id, revision)" in sql
    assert "BEFORE UPDATE OR DELETE ON qualification_acceptance_event" in sql
    assert "DROP TABLE qualification_acceptance_event" in sql
    assert migration["down_revision"] == "qualification_0002"
