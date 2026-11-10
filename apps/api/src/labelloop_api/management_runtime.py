"""Deployment-owned, opt-in composition; no connections, provisioning or startup."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from minio import Minio
from sqlalchemy import Engine

from labelloop_worker_core.jobs import JobStore

from .management_canonical import refuse
from .management_configuration import ConfigurationTrainingBinding
from .management_dispatch import CompilationDispatch, LocalMLDispatch
from .management_evaluate_worker import LocalPredictor
from .management_evaluation import ApprovedDevelopmentProtocol
from .management_model_worker import LocalExporter
from .management_objects import BoundedMinioObjects
from .management_operations import CanonicalManagementOperations
from .management_train_worker import LocalBackendFactory
from .management_training import ApprovedTrainingProfile


@dataclass(frozen=True, slots=True)
class _GroupingReader:
    read: Callable[[str], bytes]


class _UnavailableAdmission:
    def put(self, key: str, data: bytes) -> None:
        refuse("management_unavailable")


def api_operations(
    *,
    engine: Engine,
    workspace_id: UUID,
    client: Minio,
    bucket: str,
    max_object_bytes: int,
    profiles: tuple[ApprovedTrainingProfile, ...] = (),
    protocols: tuple[ApprovedDevelopmentProtocol, ...] = (),
    configurations: tuple[ConfigurationTrainingBinding, ...] = (),
) -> CanonicalManagementOperations:
    objects = BoundedMinioObjects(client, bucket, max_object_bytes)
    return CanonicalManagementOperations(
        engine,
        _UnavailableAdmission(),
        workspace_id,
        profiles=profiles,
        protocols=protocols,
        grouping_objects=_GroupingReader(objects.read),
        configurations=configurations,
    )


def compiler_dispatch(
    *,
    store: JobStore,
    workspace_id: UUID,
    client: Minio,
    bucket: str,
    max_object_bytes: int,
) -> CompilationDispatch:
    if not isinstance(workspace_id, UUID):
        raise ValueError("compiler composition requires a trusted workspace UUID")
    return CompilationDispatch(
        store, BoundedMinioObjects(client, bucket, max_object_bytes), workspace_id
    )


def local_ml_dispatch(
    *,
    store: JobStore,
    engine: Engine,
    workspace_id: UUID,
    client: Minio,
    bucket: str,
    max_object_bytes: int,
    backend: LocalBackendFactory,
    exporter: LocalExporter,
    predictor: LocalPredictor,
) -> LocalMLDispatch:
    if engine.dialect.name != "postgresql" or not isinstance(workspace_id, UUID):
        raise ValueError("local ML composition requires Postgres and a trusted workspace UUID")
    if not all(callable(callback) for callback in (backend, exporter, predictor)):
        raise ValueError("local ML composition requires trusted local callbacks")
    return LocalMLDispatch(
        store,
        engine,
        BoundedMinioObjects(client, bucket, max_object_bytes),
        workspace_id,
        backend,
        exporter,
        predictor,
    )
