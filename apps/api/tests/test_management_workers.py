"""Registration contracts only; real transactions are covered by Actions DB cases."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from labelloop_api.management_compile_worker import CanonicalCompilationWorker
from labelloop_api.management_evaluate_worker import (
    CanonicalLocalEvaluationPublisher,
    CanonicalLocalEvaluationWorker,
)
from labelloop_api.management_model_worker import (
    CanonicalLocalExportWorker,
    CanonicalLocalModelPublisher,
)
from labelloop_api.management_workers import (
    compilation_runner,
    local_evaluation_runner,
    local_training_runner,
)
from labelloop_worker_core.jobs import JobRunner, JobStore


def unexpected(*args, **kwargs):
    pytest.fail("registration must perform no DB, filesystem, object or ML I/O")


def registration(kind, **kwargs):
    store = JobStore(unexpected)
    engine = object()
    objects = SimpleNamespace(read=unexpected, put=unexpected)
    workspace_id = uuid4()
    if kind == "compile":
        runner = compilation_runner(store, objects, workspace_id, **kwargs)
    elif kind == "train":
        runner = local_training_runner(
            store, engine, objects, workspace_id, unexpected, unexpected, **kwargs
        )
    else:
        runner = local_evaluation_runner(store, engine, objects, workspace_id, unexpected, **kwargs)
    return runner, store, engine, objects, workspace_id


@pytest.mark.parametrize("kind", ["compile", "train", "evaluate"])
def test_registration_is_explicit_request_bound_and_has_no_io(kind):
    runner, store, engine, objects, workspace_id = registration(
        kind, job_id="durable-job", lease_seconds=120, max_stage_seconds=600
    )
    assert type(runner) is JobRunner
    assert runner.store is store and runner.job_id == "durable-job"
    assert runner.lease_seconds == 120 and runner.max_stage_seconds == 600
    if kind == "compile":
        assert runner.kind == "compile_variant"
        assert tuple(stage.name for stage in runner.stages) == ("compilation",)
        worker = runner.stages[0].execute.__self__
        assert isinstance(worker, CanonicalCompilationWorker)
        assert worker.store is store
        assert runner.sink.__self__ is worker
    elif kind == "train":
        assert runner.kind == "train_local"
        assert tuple(stage.name for stage in runner.stages) == ("training", "export")
        worker = runner.stages[0].execute.__self__
        export = runner.stages[1].execute.__self__
        publisher = runner.sink.__self__
        assert isinstance(export, CanonicalLocalExportWorker)
        assert isinstance(publisher, CanonicalLocalModelPublisher)
        assert export.training is publisher.training is worker
        assert worker.backend is export.exporter is unexpected
        assert worker.engine is engine
    else:
        assert runner.kind == "evaluate_local"
        assert tuple(stage.name for stage in runner.stages) == ("evaluation",)
        worker = runner.stages[0].execute.__self__
        assert isinstance(worker, CanonicalLocalEvaluationWorker)
        assert isinstance(runner.sink.__self__, CanonicalLocalEvaluationPublisher)
        assert runner.sink.__self__.workspace_id == workspace_id
        assert worker.predictor is unexpected and worker.engine is engine
    assert worker.workspace_id == workspace_id and worker.objects is objects


@pytest.mark.parametrize("kind", ["compile", "train", "evaluate"])
@pytest.mark.parametrize("job_id", ["", None])
def test_registration_refuses_unscoped_claims(kind, job_id):
    with pytest.raises(ValueError, match="specific durable job ID"):
        registration(kind, job_id=job_id)


@pytest.mark.parametrize("kind", ["compile", "train", "evaluate"])
@pytest.mark.parametrize(
    "settings", [{"lease_seconds": 0}, {"lease_seconds": 3601}, {"max_stage_seconds": 0}]
)
def test_registration_retains_native_runner_bounds(kind, settings):
    with pytest.raises(ValueError):
        registration(kind, job_id="durable-job", **settings)


@pytest.mark.parametrize("kind", ["compile", "train", "evaluate"])
def test_registration_requires_an_explicit_job_id(kind):
    with pytest.raises(TypeError, match="job_id"):
        registration(kind)
