"""Compile the actual worker lookup without a database or training backend."""

from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy import Select
from sqlalchemy.dialects import postgresql

from labelloop_api.management_train_worker import CanonicalLocalTrainingWorker
from labelloop_core.training import InferencePolicy, TrainingRequest


def test_training_variant_lookup_compiles_with_owned_lineage():
    workspace_id, project_id, variant_id = uuid4(), uuid4(), uuid4()
    request = TrainingRequest(
        "fixture-run",
        str(project_id),
        "full-letterbox-320-v1",
        "fixture-release",
        "1" * 64,
        "2" * 64,
        "assignment-1",
        "3" * 64,
        InferencePolicy(320),
    )
    lease = SimpleNamespace(
        kind="train_local",
        job_id="fixture-job",
        project_key="fixture-project",
        input={
            "workspace_id": str(workspace_id),
            "variant_id": str(variant_id),
            "requested_by": "fixture-admin",
            "request": asdict(request),
        },
    )
    engine, connection, objects, backend = (MagicMock() for _ in range(4))
    engine.connect.return_value.execution_options.return_value = connection
    connection.__enter__.return_value = connection
    compiled = []

    class QueryCompiled(Exception):
        pass

    def execute(statement, *args):
        if isinstance(statement, Select) and "recipe_id" in statement.selected_columns:
            sql = str(statement.compile(dialect=postgresql.dialect()))
            assert len(statement.get_final_froms()) == 1
            assert (
                "yolo_dataset_variant JOIN canonical_dataset_release ON "
                "yolo_dataset_variant.release_id = canonical_dataset_release.id"
            ) in sql
            assert "JOIN project ON project.id = canonical_dataset_release.project_id" in sql
            assert (
                "JOIN preprocessing_recipe ON "
                "preprocessing_recipe.id = yolo_dataset_variant.recipe_id"
            ) in sql
            assert "project.workspace_id =" in sql
            assert "project.project_key =" in sql
            assert "project.id =" in sql
            assert "yolo_dataset_variant.id =" in sql
            compiled.append(sql)
            raise QueryCompiled
        return MagicMock()

    connection.execute.side_effect = execute
    worker = CanonicalLocalTrainingWorker(engine, objects, workspace_id, backend)
    try:
        worker.train(SimpleNamespace(lease=lease))
    except QueryCompiled:
        pass
    else:
        raise AssertionError("worker did not compile the variant lookup")
    assert len(compiled) == 1
    objects.read.assert_not_called()
    objects.put.assert_not_called()
    backend.assert_not_called()
