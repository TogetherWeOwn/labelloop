"""Teacher lineage contracts; durable registry assertions run in Actions ML smoke."""

import io
import runpy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from labelloop_api import generated_label_store as store
from labelloop_api import qualification_schema
from labelloop_api.annotation_audit_store import training_ancestor_assets


def run(assets, initialization="external", checkpoint="checkpoint"):
    return (
        {
            "request": {"initialization_sha256": initialization},
            "checkpoints": [{"sha256": checkpoint}],
        },
        assets,
    )


def test_teacher_labels_trace_independent_initialization_multiple_teachers_and_cycles():
    runs = {
        "bad-teacher": run({"bad": 1}),
        "second-teacher": run({"second": 1}),
        "student": run({"generated-negative": 2}),
        "grandchild": run({"clean": 1}, "student-weights"),
        "unrelated": run({"unrelated": 1}),
    }
    runs["student"] = run({"generated-negative": 2}, checkpoint="student-weights")
    teachers = {
        "generated-negative": [(1, "bad-teacher"), (2, "second-teacher")],
        "bad": [(1, "student")],
    }
    assert training_ancestor_assets(runs, "grandchild", teachers) == {
        "bad",
        "second",
        "generated-negative",
        "clean",
    }
    assert training_ancestor_assets(runs, "unrelated", teachers) == {"unrelated"}


@pytest.mark.parametrize(
    "revision,expected", [(1, {"generated"}), (2, {"generated", "bad"}), (3, {"generated", "bad"})]
)
def test_teacher_influence_starts_at_exact_revision_and_survives_later_corrections(
    revision, expected
):
    runs = {"teacher": run({"bad": 1}), "student": run({"generated": revision})}
    assert training_ancestor_assets(runs, "student", {"generated": [(2, "teacher")]}) == expected


def test_recorded_unknown_teacher_fails_closed_instead_of_becoming_an_external_base():
    with pytest.raises(ValueError, match="outside its project"):
        training_ancestor_assets(
            {"student": run({"generated": 1})}, "student", {"generated": [(1, "foreign")]}
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_id", " "),
        ("asset_key", ""),
        ("teacher_model_key", ""),
        ("revision", True),
        ("revision", 0),
    ],
)
def test_origin_validates_before_database_access(field, value):
    args = dict(
        project_key="project",
        asset_key="asset",
        revision=1,
        teacher_model_key="teacher",
        actor_id="worker",
    )
    args[field] = value
    with pytest.raises(ValueError):
        store.record_generated_label_origin(None, **args)


def test_origin_binds_exact_source_revision_and_same_project_teacher():
    connection = MagicMock()
    connection.execute.return_value.scalar_one.side_effect = ["asset-id", "teacher-id"]
    with patch.object(store, "lock_project", return_value="project-id"):
        store.record_generated_label_origin(
            connection,
            project_key="project",
            asset_key="asset",
            revision=2,
            teacher_model_key="teacher",
            actor_id="worker",
        )
    statements = [call.args[0] for call in connection.execute.call_args_list]
    assert statements[0].compile().params == {
        "project_id_1": "project-id",
        "asset_key_1": "asset",
        "revision_1": 2,
    }
    assert statements[1].compile().params == {
        "project_id_1": "project-id",
        "release_key_1": "teacher",
    }
    assert statements[2].compile().params == dict(
        project_id="project-id",
        asset_id="asset-id",
        revision=2,
        teacher_model_id="teacher-id",
        actor_id="worker",
    )


@pytest.mark.parametrize("field", [None, "project_id", "source_project", "teacher_project"])
def test_teacher_loader_rejects_cross_project_substitution(field):
    row = dict(
        project_id="project",
        source_project="project",
        teacher_project="project",
        asset_key="asset",
        revision=2,
        training_run_id="teacher-run",
    )
    if field:
        row[field] = "foreign"
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value = [row]
    if field:
        with pytest.raises(ValueError, match="cross project"):
            store.label_teacher_runs(connection, "project")
    else:
        assert store.label_teacher_runs(connection, "project") == {"asset": [(2, "teacher-run")]}


def test_teacher_origin_migration_is_frozen_and_append_only():
    migration = runpy.run_path(
        str(Path(__file__).parents[1] / "migrations/versions/qualification_0006_teacher_origin.py")
    )
    table = qualification_schema.generated_label_origin
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
    assert "PRIMARY KEY (asset_id, revision, teacher_model_id)" in sql
    assert (
        "FOREIGN KEY(asset_id, revision) REFERENCES annotation_revision (asset_id, revision)" in sql
    )
    assert "BEFORE UPDATE OR DELETE ON qualification_generated_label_origin" in sql
    assert "BEFORE TRUNCATE ON qualification_generated_label_origin" in sql
    assert "DROP TABLE qualification_generated_label_origin" in sql
    assert migration["down_revision"] == "qualification_0005"
