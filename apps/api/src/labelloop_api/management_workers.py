"""Explicit, request-bound registration on the delivered fenced queue runner.

Deployment code supplies adapters and a specific durable job ID. Nothing is
registered at import time, and no request/model value selects callbacks or kinds.
Compiler and ML processes can bind separate database/object-store capabilities.
"""

from uuid import UUID

from sqlalchemy import Engine

from labelloop_worker_core.jobs import JobRunner, JobStore, Stage

from .management_compile_worker import CanonicalCompilationWorker, CompilationObjects
from .management_evaluate_worker import (
    CanonicalLocalEvaluationPublisher,
    CanonicalLocalEvaluationWorker,
    LocalPredictor,
)
from .management_model_worker import (
    CanonicalLocalExportWorker,
    CanonicalLocalModelPublisher,
    LocalExporter,
)
from .management_train_worker import CanonicalLocalTrainingWorker, LocalBackendFactory


def compilation_runner(
    store: JobStore,
    objects: CompilationObjects,
    workspace_id: UUID,
    *,
    job_id: str,
    lease_seconds: int = 60,
    max_stage_seconds: int = 3600,
) -> JobRunner:
    if not job_id:
        raise ValueError("management runners require a specific durable job ID")
    worker = CanonicalCompilationWorker(store, objects, workspace_id)
    return JobRunner(
        store,
        "compile_variant",
        (Stage("compilation", worker.compile),),
        worker.publish,
        job_id=job_id,
        lease_seconds=lease_seconds,
        max_stage_seconds=max_stage_seconds,
    )


def local_training_runner(
    store: JobStore,
    engine: Engine,
    objects: CompilationObjects,
    workspace_id: UUID,
    backend: LocalBackendFactory,
    exporter: LocalExporter,
    *,
    job_id: str,
    lease_seconds: int = 60,
    max_stage_seconds: int = 3600,
) -> JobRunner:
    if not job_id:
        raise ValueError("management runners require a specific durable job ID")
    training = CanonicalLocalTrainingWorker(engine, objects, workspace_id, backend)
    export = CanonicalLocalExportWorker(training, exporter)
    publisher = CanonicalLocalModelPublisher(training)
    return JobRunner(
        store,
        "train_local",
        (Stage("training", training.train), Stage("export", export.export)),
        publisher.publish,
        job_id=job_id,
        lease_seconds=lease_seconds,
        max_stage_seconds=max_stage_seconds,
    )


def local_evaluation_runner(
    store: JobStore,
    engine: Engine,
    objects: CompilationObjects,
    workspace_id: UUID,
    predictor: LocalPredictor,
    *,
    job_id: str,
    lease_seconds: int = 60,
    max_stage_seconds: int = 3600,
) -> JobRunner:
    if not job_id:
        raise ValueError("management runners require a specific durable job ID")
    evaluation = CanonicalLocalEvaluationWorker(engine, objects, workspace_id, predictor)
    publisher = CanonicalLocalEvaluationPublisher(workspace_id)
    return JobRunner(
        store,
        "evaluate_local",
        (Stage("evaluation", evaluation.evaluate),),
        publisher.publish,
        job_id=job_id,
        lease_seconds=lease_seconds,
        max_stage_seconds=max_stage_seconds,
    )
