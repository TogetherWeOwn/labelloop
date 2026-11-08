"""Trusted PostgreSQL queue. No endpoint, credential, or handler comes from model output."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class LeaseLost(Exception):
    """Expired, cancelled or superseded worker; its transaction must roll back."""


class ReplayConflict(ValueError):
    pass


def canonical(value: dict) -> bytes:
    result = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(result) > 2_000_000:
        raise ValueError("stage record exceeds bounded inline artifact size")
    return result


def digest(value: dict) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


@dataclass(frozen=True)
class Lease:
    job_id: str
    project_key: str
    kind: str
    generation: int
    worker_id: str
    input: dict


class TransactionalSink(Protocol):
    """Same DB transaction only. Remote effects require a separate durable outbox consumer.

    Implementations must not commit/rollback, close the connection, or emit external effects.
    The API adapter must apply authenticated expected-revision guards before append.
    """

    def __call__(self, connection: psycopg.Connection, lease: Lease, artifacts: dict) -> None: ...


class JobStore:
    def __init__(self, connect: Callable[[], psycopg.Connection]) -> None:
        self.connect = connect

    def enqueue(
        self,
        project_key: str,
        operation_key: str,
        kind: str,
        payload: dict,
        *,
        max_attempts: int = 3,
    ) -> str:
        if not all((project_key, operation_key, kind)) or not 1 <= max_attempts <= 10:
            raise ValueError("jobs need scoped identity and 1..10 attempts")
        canonical(payload)
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "INSERT INTO worker_job(job_id,project_key,operation_key,kind,input,max_attempts) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (project_key,operation_key) DO NOTHING",
                (str(uuid4()), project_key, operation_key, kind, Jsonb(payload), max_attempts),
            )
            cur.execute(
                "SELECT * FROM worker_job WHERE project_key=%s AND operation_key=%s",
                (project_key, operation_key),
            )
            row = cur.fetchone()
            assert row is not None
            if (row["kind"], row["input"], row["max_attempts"]) != (kind, payload, max_attempts):
                raise ReplayConflict("operation key already pins different inputs")
            return row["job_id"]

    def claim(self, kind: str, worker_id: str, *, lease_seconds: int = 60) -> Lease | None:
        self._duration(lease_seconds)
        if not kind or not worker_id:
            raise ValueError("claim requires trusted kind and worker identity")
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            # SKIP LOCKED applies to exhaustion too: no head-of-line blocking on active commits.
            cur.execute(
                "WITH exhausted AS (SELECT job_id FROM worker_job WHERE kind=%s "
                "AND status='running' AND lease_until <= clock_timestamp() "
                "AND attempts >= max_attempts FOR UPDATE SKIP LOCKED) "
                "UPDATE worker_job SET status='failed', lease_until=NULL, worker_id=NULL, "
                "error_code='attempts_exhausted' WHERE job_id IN (SELECT job_id FROM exhausted)",
                (kind,),
            )
            cur.execute(
                "WITH candidate AS (SELECT job_id FROM worker_job WHERE kind=%s "
                "AND attempts < max_attempts AND ((status='queued' "
                "AND available_at <= clock_timestamp()) OR (status='running' "
                "AND lease_until <= clock_timestamp())) "
                "ORDER BY created_at, job_id FOR UPDATE SKIP LOCKED LIMIT 1) "
                "UPDATE worker_job j SET status='running', attempts=attempts+1, "
                "generation=generation+1, worker_id=%s, error_code=NULL, "
                "lease_until=clock_timestamp() + %s * interval '1 second' "
                "FROM candidate c WHERE j.job_id=c.job_id RETURNING j.*",
                (kind, worker_id, lease_seconds),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return Lease(
                row["job_id"],
                row["project_key"],
                row["kind"],
                row["generation"],
                row["worker_id"],
                row["input"],
            )

    @staticmethod
    def _duration(seconds: int) -> None:
        if type(seconds) is not int or not 1 <= seconds <= 3600:
            raise ValueError("lease duration must be 1..3600 seconds")

    @staticmethod
    def _lock(cur: Any, lease: Lease) -> None:
        cur.execute(
            "SELECT job_id FROM worker_job WHERE job_id=%s AND project_key=%s "
            "AND kind=%s AND status='running' AND generation=%s AND worker_id=%s "
            "AND lease_until > clock_timestamp() FOR UPDATE",
            (lease.job_id, lease.project_key, lease.kind, lease.generation, lease.worker_id),
        )
        if cur.fetchone() is None:
            raise LeaseLost("worker lease is no longer current")

    def heartbeat(self, lease: Lease, *, lease_seconds: int = 60) -> None:
        self._duration(lease_seconds)
        with self.connect() as conn, conn.cursor() as cur:
            self._lock(cur, lease)
            cur.execute(
                "UPDATE worker_job SET lease_until=clock_timestamp() + %s * interval '1 second' "
                "WHERE job_id=%s",
                (lease_seconds, lease.job_id),
            )

    def artifacts(self, lease: Lease) -> dict:
        with self.connect() as conn, conn.cursor() as cur:
            self._lock(cur, lease)
            cur.execute(
                "SELECT stage,payload FROM worker_stage_artifact WHERE job_id=%s ORDER BY stage",
                (lease.job_id,),
            )
            return dict(cur.fetchall())

    def save_stage(self, lease: Lease, stage: str, payload: dict) -> None:
        if not stage:
            raise ValueError("stage identity is required")
        sha256 = digest(payload)
        with self.connect() as conn, conn.cursor() as cur:
            self._lock(cur, lease)
            cur.execute(
                "INSERT INTO worker_stage_artifact(job_id,stage,sha256,payload,generation) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (lease.job_id, stage, sha256, Jsonb(payload), lease.generation),
            )
            cur.execute(
                "SELECT sha256 FROM worker_stage_artifact WHERE job_id=%s AND stage=%s",
                (lease.job_id, stage),
            )
            if cur.fetchone()[0] != sha256:
                raise ReplayConflict("completed stage artifact cannot be replaced")
            self._lock(cur, lease)

    def complete(
        self, lease: Lease, required_stages: tuple[str, ...], sink: TransactionalSink
    ) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            self._lock(cur, lease)
            cur.execute(
                "SELECT stage,payload FROM worker_stage_artifact WHERE job_id=%s",
                (lease.job_id,),
            )
            artifacts = dict(cur.fetchall())
            if set(artifacts) != set(required_stages):
                raise ValueError("cannot commit incomplete or unexpected stage artifacts")
            cur.execute(
                "INSERT INTO worker_application(job_id,generation) VALUES (%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING job_id",
                (lease.job_id, lease.generation),
            )
            if cur.fetchone() is not None:
                sink(conn, lease, artifacts)
            # A slow sink may cross the deadline while the row is locked. Roll back all effects.
            self._lock(cur, lease)
            cur.execute(
                "UPDATE worker_job SET status='succeeded',lease_until=NULL,worker_id=NULL "
                "WHERE job_id=%s",
                (lease.job_id,),
            )

    def fail(self, lease: Lease) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            self._lock(cur, lease)
            cur.execute(
                "UPDATE worker_job SET status=CASE WHEN attempts < max_attempts THEN 'queued' "
                "ELSE 'failed' END, available_at=clock_timestamp() + "
                "LEAST(300, power(2, attempts)) * interval '1 second', "
                "lease_until=NULL,worker_id=NULL,error_code='stage_failed' WHERE job_id=%s",
                (lease.job_id,),
            )

    def cancel(self, project_key: str, job_id: str) -> bool:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE worker_job SET status='cancelled', generation=generation+1, "
                "lease_until=NULL,worker_id=NULL WHERE project_key=%s AND job_id=%s "
                "AND status IN ('queued','running') RETURNING job_id",
                (project_key, job_id),
            )
            return cur.fetchone() is not None


@dataclass(frozen=True)
class StageContext:
    store: JobStore
    lease: Lease
    artifacts: dict

    def heartbeat(self) -> None:
        self.store.heartbeat(self.lease)


@dataclass(frozen=True)
class Stage:
    name: str
    execute: Callable[[StageContext], dict]


class JobRunner:
    """One bounded claim per invocation; process supervision is deployment-owned."""

    def __init__(
        self, store: JobStore, kind: str, stages: tuple[Stage, ...], sink: TransactionalSink
    ) -> None:
        if not stages or len({stage.name for stage in stages}) != len(stages):
            raise ValueError("runner needs distinct code-owned stages")
        self.store, self.kind, self.stages, self.sink = store, kind, stages, sink

    def run_once(self, worker_id: str) -> bool:
        lease = self.store.claim(self.kind, worker_id)
        if lease is None:
            return False
        try:
            artifacts = self.store.artifacts(lease)
            for stage in self.stages:
                if stage.name not in artifacts:
                    payload = stage.execute(StageContext(self.store, lease, dict(artifacts)))
                    self.store.save_stage(lease, stage.name, payload)
                    artifacts[stage.name] = payload
                self.store.heartbeat(lease)
            self.store.complete(lease, tuple(stage.name for stage in self.stages), self.sink)
        except LeaseLost:
            pass
        except Exception:
            # Never store exception text: adapters may include secrets or untrusted payloads.
            try:
                self.store.fail(lease)
            except LeaseLost:
                pass
        return True


class ProposalRunSink:
    """Append-only proposal evidence, not the authenticated annotation writer."""

    def __call__(self, connection: psycopg.Connection, lease: Lease, artifacts: dict) -> None:
        record = artifacts["proposal"]
        expected = {
            "approval_state": "proposed",
            "review_tier": "unreviewed",
            "scope_completeness": "unreviewed",
            "training_eligible": False,
            "gold_eligible": False,
        }
        if any(
            type(record.get(k)) is not type(v) or record.get(k) != v for k, v in expected.items()
        ):
            raise ValueError("workers can append proposed/unreviewed evidence only")
        if record["project_id"] != lease.project_key or record["run_id"] != lease.input["run_id"]:
            raise ValueError("proposal identity differs from the pinned job")
        connection.execute(
            "INSERT INTO worker_proposal_run(run_id,job_id,project_key,sha256,record) "
            "VALUES (%s,%s,%s,%s,%s)",
            (record["run_id"], lease.job_id, lease.project_key, digest(record), Jsonb(record)),
        )


class ProposalJobRunner(JobRunner):
    def __init__(
        self,
        store: JobStore,
        discover: Callable[[StageContext], dict],
        propose: Callable[[StageContext], dict],
        sink: TransactionalSink | None = None,
    ) -> None:
        super().__init__(
            store,
            "proposals",
            (Stage("discovery", discover), Stage("proposal", propose)),
            sink if sink is not None else ProposalRunSink(),
        )
