"""Explicit one-job dispatch; native queue leases still own execution and recovery."""

from dataclasses import dataclass
from uuid import UUID

from psycopg.rows import dict_row
from sqlalchemy import Engine

from labelloop_core.json_records import canonical_json
from labelloop_worker_core.jobs import JobStore

from .management_compile_worker import CompilationObjects
from .management_evaluate_worker import LocalPredictor
from .management_model_worker import LocalExporter
from .management_train_worker import LocalBackendFactory
from .management_workers import compilation_runner, local_evaluation_runner, local_training_runner

# Only persisted kinds select among these code-owned lanes. No callback comes from input.
_INTENTS = {
    "compile_variant": ("management.compile_variant", "release_id"),
    "train_local": ("management.start_training", "variant_id"),
    "evaluate_local": ("management.start_evaluation", "model_id"),
}


def dispatch_kind(store: JobStore, workspace_id: UUID, job_id: str, kinds: tuple[str, ...]) -> str:
    if not isinstance(workspace_id, UUID) or type(job_id) is not str or not job_id:
        raise ValueError("dispatch requires a trusted workspace and specific durable job ID")
    with store.connect() as connection, connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT j.kind,j.input,p.id::text AS project_id FROM worker_job j "
                "JOIN project p ON p.project_key=j.project_key "
                "WHERE j.job_id=%s AND p.workspace_id=%s",
                (job_id, workspace_id),
            )
            job = cursor.fetchone()
            if job is None or job["kind"] not in kinds or job["kind"] not in _INTENTS:
                raise ValueError("missing owned job in this dispatch lane")
            kind = job["kind"]
            event_type, target = _INTENTS[kind]
            inputs = job["input"]
            if (
                type(inputs) is not dict
                or inputs.get("workspace_id") != str(workspace_id)
                or type(inputs.get("requested_by")) is not str
                or not inputs["requested_by"]
                or type(inputs.get(target)) is not str
                or not inputs[target]
            ):
                raise ValueError("invalid dispatch input identity")
            cursor.execute(
                "SELECT aggregate_id,actor_kind,actor_id,payload FROM canonical_event "
                "WHERE event_type=%s AND payload->'evidence'->>'job_id'=%s LIMIT 2",
                (event_type, job_id),
            )
            receipts = cursor.fetchall()
            if len(receipts) != 1:
                raise ValueError("dispatch requires one human intent receipt")
            receipt = receipts[0]
            payload = receipt["payload"]
            if (
                receipt["aggregate_id"] != inputs[target]
                or receipt["actor_kind"] != "human"
                or receipt["actor_id"] != inputs["requested_by"]
                or type(payload) is not dict
                or payload.get("workspace_id") != str(workspace_id)
                or type(payload.get("result")) is not dict
                or payload["result"].get("project_id") != job["project_id"]
                or type(payload.get("evidence")) is not dict
                or canonical_json(payload["evidence"].get("inputs")) != canonical_json(inputs)
            ):
                raise ValueError("dispatch differs from owned human intent")
            return kind


@dataclass(frozen=True)
class CompilationDispatch:
    store: JobStore
    objects: CompilationObjects
    workspace_id: UUID

    def run_once(self, job_id: str, worker_id: str) -> bool:
        if type(worker_id) is not str or not worker_id:
            raise ValueError("dispatch requires a worker identity")
        dispatch_kind(self.store, self.workspace_id, job_id, ("compile_variant",))
        return compilation_runner(
            self.store, self.objects, self.workspace_id, job_id=job_id
        ).run_once(worker_id)


@dataclass(frozen=True)
class LocalMLDispatch:
    store: JobStore
    engine: Engine
    objects: CompilationObjects
    workspace_id: UUID
    backend: LocalBackendFactory
    exporter: LocalExporter
    predictor: LocalPredictor

    def run_once(self, job_id: str, worker_id: str) -> bool:
        if type(worker_id) is not str or not worker_id:
            raise ValueError("dispatch requires a worker identity")
        kind = dispatch_kind(
            self.store, self.workspace_id, job_id, ("train_local", "evaluate_local")
        )
        if kind == "train_local":
            runner = local_training_runner(
                self.store,
                self.engine,
                self.objects,
                self.workspace_id,
                self.backend,
                self.exporter,
                job_id=job_id,
            )
        else:
            runner = local_evaluation_runner(
                self.store,
                self.engine,
                self.objects,
                self.workspace_id,
                self.predictor,
                job_id=job_id,
            )
        return runner.run_once(worker_id)
