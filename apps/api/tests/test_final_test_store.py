"""Cheap journal boundary/DDL checks; PostgreSQL transitions run in Actions ML smoke."""

import io
import json
import runpy
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from labelloop_api import final_test_store, qualification_schema
from labelloop_api.final_test_store import (
    expose_final_test,
    invalidate_final_test,
    record_candidate_selection,
    register_final_test,
)
from labelloop_core.evaluation_lifecycle import CandidateSelection, EvaluationSet, FinalTestState
from labelloop_core.qualification_wire import dumps


@pytest.mark.parametrize(
    "project_key,dataset_key", [("project", "test"), ("other", "test"), ("project", "other")]
)
def test_final_state_loader_decodes_the_expected_root_and_checks_identity(project_key, dataset_key):
    state = FinalTestState(EvaluationSet("test", "project", ("g",), "locked_final_test"))
    connection = Mock()
    connection.execute.return_value.scalar_one.return_value = json.loads(dumps(state))
    with patch.object(final_test_store, "lock_project", return_value="project-id"):
        if (project_key, dataset_key) == ("project", "test"):
            assert (
                final_test_store.load_final_test(
                    connection, project_key=project_key, dataset_key=dataset_key
                )
                == state
            )
        else:
            with pytest.raises(ValueError, match="journal identity mismatch"):
                final_test_store.load_final_test(
                    connection, project_key=project_key, dataset_key=dataset_key
                )


@pytest.mark.parametrize("wrong_root", [False, True])
def test_exposure_decodes_only_a_candidate_selection_before_transition(wrong_root):
    selection = CandidateSelection(
        "selection", "project", "model", "a" * 64, "dev-run", "dev", None, None
    )
    state = Mock()
    connection = MagicMock()
    record = (
        FinalTestState(EvaluationSet("test", "project", ("g",), "locked_final_test"))
        if wrong_root
        else selection
    )
    connection.execute.return_value.scalar_one.return_value = json.loads(dumps(record))
    connection.execute.return_value.scalar_one_or_none.return_value = None
    release, report = object(), object()
    with (
        patch.object(final_test_store, "lock_project", return_value="project-id"),
        patch.object(final_test_store, "load_final_test", return_value=state),
        patch.object(final_test_store, "_model", return_value=release),
        patch.object(final_test_store, "stored_report", return_value=report),
        patch.object(final_test_store, "_append") as append,
    ):
        kwargs = dict(
            project_key="project",
            dataset_key="test",
            model_key="model",
            selection_key="selection",
            evaluation_key="final",
            expected_revision=0,
            actor_id="admin",
        )
        if wrong_root:
            with pytest.raises(ValueError, match="schema or record type"):
                expose_final_test(connection, **kwargs)
            state.expose.assert_not_called()
            append.assert_not_called()
        else:
            assert expose_final_test(connection, **kwargs) is state.expose.return_value
            state.expose.assert_called_once_with(release, selection, report, expected_revision=0)
            append.assert_called_once_with(
                connection, "project-id", "admin", "expose", state.expose.return_value
            )


@pytest.mark.parametrize(
    "operation,kwargs",
    [
        (
            register_final_test,
            {"dataset": EvaluationSet("test", "project", ("g",), "locked_final_test")},
        ),
        (
            record_candidate_selection,
            {
                "project_key": "project",
                "model_key": "model",
                "selection_key": "selection",
                "development_key": "report",
                "development_set": EvaluationSet("dev", "project", ("g",), "development"),
            },
        ),
        (
            expose_final_test,
            {
                "project_key": "project",
                "dataset_key": "test",
                "model_key": "model",
                "selection_key": "selection",
                "evaluation_key": "report",
                "expected_revision": 0,
            },
        ),
        (
            invalidate_final_test,
            {
                "project_key": "project",
                "dataset_key": "test",
                "action": "adapt",
                "reason": "changed threshold",
                "expected_revision": 1,
            },
        ),
    ],
)
def test_journal_rejects_empty_actor_before_any_database_access(operation, kwargs):
    with pytest.raises(ValueError, match="authenticated actor"):
        operation(None, actor_id="  ", **kwargs)


def test_journal_cannot_reset_or_unquarantine_a_final_test():
    with pytest.raises(ValueError, match="unknown final-test invalidation"):
        invalidate_final_test(
            None,
            project_key="project",
            dataset_key="test",
            action="reset",
            reason="reuse",
            expected_revision=1,
            actor_id="admin",
        )


def test_initial_state_cannot_register_a_development_set_as_locked_final():
    with pytest.raises(ValueError, match="original locked-test identity"):
        register_final_test(
            None,
            dataset=EvaluationSet("dev", "project", ("g",), "development"),
            actor_id="admin",
        )


def test_final_journal_migration_has_frozen_append_only_ddl():
    migration = runpy.run_path(
        str(Path(__file__).parents[1] / "migrations/versions/qualification_0002_final_test.py")
    )
    future = sa.Column("future_column", sa.String)
    qualification_schema.final_test_event.append_column(future)
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    try:
        with Operations.context(context):
            migration["upgrade"]()
            migration["downgrade"]()
    finally:
        qualification_schema.final_test_event._columns.remove(future)
    sql = output.getvalue()
    assert "future_column" not in sql
    for table in ("qualification_candidate_selection", "qualification_final_test_event"):
        assert f"CREATE TABLE {table}" in sql
        assert f"DROP TABLE {table}" in sql
        assert f"BEFORE UPDATE OR DELETE ON {table}" in sql
    assert "PRIMARY KEY (project_id, dataset_key, revision)" in sql
    assert migration["down_revision"] == "qualification_0001"
