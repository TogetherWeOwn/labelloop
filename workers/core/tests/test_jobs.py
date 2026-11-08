"""Disposable PostgreSQL only; enabled explicitly in the Actions lease job."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from labelloop_worker_core.jobs import (
    JobRunner,
    JobStore,
    LeaseLost,
    ProposalJobRunner,
    ProposalRunSink,
    ReplayConflict,
    Stage,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("LABELLOOP_QUEUE_TESTS") != "1", reason="requires disposable Actions PostgreSQL"
)


@pytest.fixture
def store():
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    return JobStore(lambda: psycopg.connect(url))


def enqueue(store, *, max_attempts=3):
    project = str(uuid4())
    return project, store.enqueue(
        project, "operation", "proposals", {"run_id": str(uuid4())}, max_attempts=max_attempts
    )


def expire(store, lease):
    with store.connect() as conn:
        conn.execute(
            "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE job_id=%s",
            (lease.job_id,),
        )


def status(store, job_id):
    with store.connect() as conn:
        return conn.execute(
            "SELECT status,attempts,generation FROM worker_job WHERE job_id=%s", (job_id,)
        ).fetchone()


def test_idempotent_enqueue_rejects_different_inputs(store):
    project, job_id = enqueue(store)
    lease = store.claim("proposals", "worker")
    assert lease.job_id == job_id
    assert store.enqueue(project, "operation", "proposals", lease.input) == job_id
    with pytest.raises(ReplayConflict):
        store.enqueue(project, "operation", "proposals", {"run_id": "changed"})
    store.cancel(project, job_id)


@pytest.mark.parametrize("payload", [{"nested": [(1, 2), {"values": (3, 4)}]}, {1: "value"}])
def test_idempotent_enqueue_compares_json_normalized_inputs(store, payload):
    project = str(uuid4())
    job_id = store.enqueue(project, "operation", "proposals", payload)
    assert store.enqueue(project, "operation", "proposals", payload) == job_id
    lease = store.claim("proposals", "worker", job_id=job_id)
    assert store.enqueue(project, "operation", "proposals", lease.input) == job_id
    with pytest.raises(ReplayConflict):
        store.enqueue(project, "operation", "proposals", {"changed": True})
    store.cancel(project, job_id)


@pytest.mark.parametrize("first,second", [(True, 1), (1, True), (False, 0), (0, False)])
def test_enqueue_preserves_nested_json_scalar_types(store, first, second):
    project = str(uuid4())
    payload = {"nested": [{"enabled": first}]}
    job_id = store.enqueue(project, "operation", "proposals", payload)
    assert store.enqueue(project, "operation", "proposals", payload) == job_id
    with pytest.raises(ReplayConflict):
        store.enqueue(project, "operation", "proposals", {"nested": [{"enabled": second}]})
    store.cancel(project, job_id)


@pytest.mark.parametrize("reverse", [False, True])
def test_queue_rejects_colliding_json_keys_without_persisting_data(store, reverse):
    pairs = [(1, "original"), ("1", "replacement")]
    payload = {"nested": [{"values": dict(reversed(pairs) if reverse else pairs)}]}
    project = str(uuid4())
    with pytest.raises(ValueError, match="colliding JSON object keys"):
        store.enqueue(project, "operation", "proposals", payload)
    with store.connect() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM worker_job WHERE project_key=%s", (project,)
            ).fetchone()[0]
            == 0
        )
    job_id = store.enqueue(project, "operation", "proposals", {"accepted": True})
    lease = store.claim("proposals", "worker", job_id=job_id)
    with pytest.raises(ValueError, match="colliding JSON object keys"):
        store.save_stage(lease, "stage", payload)
    assert store.artifacts(lease) == {}
    with pytest.raises(ValueError, match="colliding JSON object keys"):
        store.enqueue(project, "operation", "proposals", payload)
    assert store.save_stage(lease, "stage", {"accepted": True}) == {"accepted": True}
    store.cancel(project, job_id)


def bundle_counts(store, project_key):
    with store.connect() as conn:
        return tuple(
            conn.execute(query, (project_key,)).fetchone()[0]
            for query in (
                "SELECT count(*) FROM project WHERE project_key=%s",
                "SELECT count(*) FROM canonical_event WHERE aggregate_id=%s",
                "SELECT count(*) FROM worker_job WHERE project_key=%s",
            )
        )


@pytest.mark.parametrize("autocommit", [False, True])
@pytest.mark.parametrize("failure", [None, "caller", "audit"])
def test_transactional_enqueue_commits_or_rolls_back_state_audit_and_job(
    store, autocommit, failure
):
    project_key = str(uuid4())
    payload = {"nested": [(True, {2: "two"})]}
    outcome = (
        nullcontext()
        if failure is None
        else pytest.raises(RuntimeError if failure == "caller" else psycopg.errors.NotNullViolation)
    )
    with store.connect() as conn:
        conn.autocommit = autocommit
        with outcome, conn.transaction():
            conn.execute(
                "INSERT INTO project(project_key,workspace_id,task,scope,domain) "
                "VALUES (%s,%s,'detection','fixture scope','fixture domain')",
                (project_key, uuid4()),
            )
            job_id = store.enqueue_in_transaction(conn, project_key, "compile", "compile", payload)
            assert (
                store.enqueue_in_transaction(
                    conn, project_key, "compile", "compile", {"nested": [[True, {"2": "two"}]]}
                )
                == job_id
            )
            assert not conn.closed
            conn.execute(
                "INSERT INTO canonical_event(event_type,aggregate_id,expected_revision,"
                "payload,actor_kind,actor_id) VALUES ('management.fixture_enqueue',%s,%s,"
                "'{}','human','fixture-admin')",
                (project_key, None if failure == "audit" else 0),
            )
            assert bundle_counts(store, project_key) == (0, 0, 0)
            if failure == "caller":
                raise RuntimeError("caller rejected the transaction")
        assert not conn.closed
    expected = 1 if failure is None else 0
    assert bundle_counts(store, project_key) == (expected,) * 3
    if failure is None:
        lease = store.claim("compile", "fixture-worker", job_id=job_id)
        assert lease.job_id == job_id and lease.input == {"nested": [[True, {"2": "two"}]]}
        store.cancel(project_key, job_id)


@pytest.mark.parametrize("autocommit", [False, True])
def test_transactional_enqueue_refuses_idle_connections(store, autocommit):
    project_key = str(uuid4())
    with store.connect() as conn:
        conn.autocommit = autocommit
        with pytest.raises(ValueError, match="active caller transaction"):
            store.enqueue_in_transaction(conn, project_key, "compile", "compile", {})
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    assert bundle_counts(store, project_key) == (0, 0, 0)


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "train"},
        {"payload": {"enabled": 1}},
        {"max_attempts": 4},
    ],
)
def test_transactional_enqueue_conflict_rolls_back_caller_state(store, changes):
    project_key = str(uuid4())
    original = {"kind": "compile", "payload": {"enabled": True}, "max_attempts": 3}
    job_id = store.enqueue(project_key, "compile", **original)
    with pytest.raises(ReplayConflict), store.connect() as conn, conn.transaction():
        conn.execute(
            "INSERT INTO project(project_key,workspace_id,task,scope,domain) "
            "VALUES (%s,%s,'detection','fixture scope','fixture domain')",
            (project_key, uuid4()),
        )
        store.enqueue_in_transaction(conn, project_key, "compile", **(original | changes))
    assert bundle_counts(store, project_key) == (0, 0, 1)
    assert store.enqueue(project_key, "compile", **original) == job_id
    store.cancel(project_key, job_id)


@pytest.mark.parametrize("changed", [False, True])
def test_concurrent_transactional_enqueue_reuses_unique_job_or_refuses_conflict(store, changed):
    project_key, barrier = str(uuid4()), Barrier(2)

    def submit(index):
        with store.connect() as conn, conn.transaction():
            conn.execute("SET LOCAL lock_timeout='5s'")
            barrier.wait(timeout=5)
            try:
                return store.enqueue_in_transaction(
                    conn, project_key, "compile", "compile", {"pin": index if changed else 0}
                )
            except ReplayConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results.count("conflict") == int(changed)
    assert len(set(result for result in results if result != "conflict")) == 1
    assert bundle_counts(store, project_key) == (0, 0, 1)
    for job_id in set(results) - {"conflict"}:
        store.cancel(project_key, job_id)


@pytest.mark.parametrize(
    "changes",
    [
        {"project_key": ""},
        {"operation_key": ""},
        {"kind": ""},
        {"max_attempts": 0},
        {"max_attempts": 11},
        {"payload": {"nested": {1: "one", "1": "collision"}}},
    ],
)
def test_transactional_enqueue_preserves_input_validation_without_writing(store, changes):
    project_key = str(uuid4())
    arguments = {
        "project_key": project_key,
        "operation_key": "compile",
        "kind": "compile",
        "payload": {},
    }
    with store.connect() as conn, conn.transaction():
        conn.execute("SELECT 1")
        with pytest.raises(ValueError):
            store.enqueue_in_transaction(conn, **(arguments | changes))
        assert (
            conn.execute(
                "SELECT count(*) FROM worker_job WHERE project_key=%s", (project_key,)
            ).fetchone()[0]
            == 0
        )
    assert bundle_counts(store, project_key) == (0, 0, 0)


def test_stage_hash_matches_normalized_readback_and_rejects_changes(store):
    from labelloop_worker_core.jobs import digest

    project, job_id = enqueue(store)
    lease = store.claim("proposals", "worker", job_id=job_id)
    payload = {"values": {2: "two", 10: "ten"}, "nested": [(True, False)]}
    store.save_stage(lease, "stage", payload)
    saved = store.artifacts(lease)["stage"]
    assert saved == {"values": {"2": "two", "10": "ten"}, "nested": [[True, False]]}
    with store.connect() as conn:
        assert conn.execute(
            "SELECT sha256 FROM worker_stage_artifact WHERE job_id=%s AND stage='stage'",
            (job_id,),
        ).fetchone()[0] == digest(saved)
    store.save_stage(lease, "stage", saved)
    store.save_stage(lease, "stage", payload)
    for changed in (
        saved | {"values": {"2": "changed", "10": "ten"}},
        saved | {"nested": [[1, 0]]},
    ):
        with pytest.raises(ReplayConflict):
            store.save_stage(lease, "stage", changed)
    store.cancel(project, job_id)


@pytest.mark.parametrize("resume", [False, True])
def test_runner_passes_persisted_stage_shape_to_subsequent_stages(store, resume):
    project, job_id = enqueue(store)
    payload = {"values": {2: "two", 10: "ten"}, "nested": [(True, False)]}
    normalized = {"values": {"2": "two", "10": "ten"}, "nested": [[True, False]]}
    if resume:
        lease = store.claim("proposals", "old-worker", job_id=job_id)
        store.save_stage(lease, "first", payload)
        expire(store, lease)

    def first(context):
        assert not resume, "completed stage must not run again"
        return payload

    def second(context):
        assert context.artifacts == {"first": normalized}
        return {"checked": True}

    def sink(conn, lease, artifacts):
        assert artifacts == {"first": normalized, "second": {"checked": True}}

    runner = JobRunner(
        store, "proposals", (Stage("first", first), Stage("second", second)), sink, job_id=job_id
    )
    assert runner.run_once("worker")
    assert status(store, job_id)[0] == "succeeded"
    assert not store.cancel(project, job_id)


@pytest.mark.parametrize("resume", [False, True])
def test_runner_preserves_custom_duration_for_all_heartbeats(store, monkeypatch, resume):
    project, job_id = enqueue(store)
    if resume:
        lease = store.claim("proposals", "old-worker", job_id=job_id, lease_seconds=600)
        store.save_stage(lease, "stage", {"saved": True})
        expire(store, lease)
    durations = []
    heartbeat = store.heartbeat

    def record_heartbeat(lease, *, lease_seconds=60):
        durations.append(lease_seconds)
        heartbeat(lease, lease_seconds=lease_seconds)

    monkeypatch.setattr(store, "heartbeat", record_heartbeat)

    def handler(context):
        assert not resume, "completed stage must not run again"
        context.heartbeat()
        return {"saved": True}

    def sink(conn, lease, artifacts):
        remaining = conn.execute(
            "SELECT EXTRACT(EPOCH FROM lease_until-clock_timestamp()) "
            "FROM worker_job WHERE job_id=%s",
            (job_id,),
        ).fetchone()[0]
        assert remaining > 590, "handler or post-stage heartbeat shortened the lease"

    runner = JobRunner(
        store,
        "proposals",
        (Stage("stage", handler),),
        sink,
        job_id=job_id,
        lease_seconds=600,
    )
    assert runner.run_once("worker")
    assert status(store, job_id)[0] == "succeeded"
    assert len(durations) >= (1 if resume else 3)
    assert set(durations) == {600}
    assert not store.cancel(project, job_id)


def test_expired_worker_resumes_artifacts_and_cannot_write_or_commit(store):
    project, job_id = enqueue(store)
    first = store.claim("proposals", "old")
    store.save_stage(first, "discovery", {"blind": True})
    store.heartbeat(first)
    expire(store, first)
    second = store.claim("proposals", "new")
    assert second.job_id == job_id and second.generation == first.generation + 1
    assert store.artifacts(second) == {"discovery": {"blind": True}}
    for action in (
        lambda: store.heartbeat(first),
        lambda: store.save_stage(first, "teacher", {}),
        lambda: store.complete(first, ("discovery",), lambda *args: pytest.fail("stale")),
    ):
        with pytest.raises(LeaseLost):
            action()
    with pytest.raises(ReplayConflict):
        store.save_stage(second, "discovery", {"blind": False})
    store.save_stage(second, "discovery", {"blind": True})
    calls = []
    store.complete(second, ("discovery",), lambda *args: calls.append("apply"))
    with pytest.raises(LeaseLost):
        store.complete(second, ("discovery",), lambda *args: calls.append("duplicate"))
    assert calls == ["apply"]
    assert status(store, job_id)[0] == "succeeded"
    assert store.cancel(project, job_id) is False


def test_skip_locked_claims_distinct_jobs_while_one_row_is_locked(store):
    project, first_id = enqueue(store)
    _, second_id = enqueue(store)
    with store.connect() as conn:
        conn.execute("SELECT job_id FROM worker_job WHERE job_id=%s FOR UPDATE", (first_id,))
        second = store.claim("proposals", "worker")
        assert second.job_id == second_id
    first = store.claim("proposals", "worker")
    assert first.job_id == first_id
    store.cancel(project, first_id)
    store.cancel(second.project_key, second_id)


def test_simultaneous_claimers_never_share_a_generation(store):
    jobs = [enqueue(store) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        leases = list(pool.map(lambda i: store.claim("proposals", f"worker-{i}"), range(4)))
    assert {lease.job_id for lease in leases} == {job for _, job in jobs}
    for lease in leases:
        store.cancel(lease.project_key, lease.job_id)


def test_expired_budget_and_cancellation_are_terminal(store):
    project, job_id = enqueue(store, max_attempts=1)
    lease = store.claim("proposals", "worker")
    expire(store, lease)
    assert store.claim("proposals", "next") is None
    assert status(store, job_id)[0] == "failed"
    project, job_id = enqueue(store)
    lease = store.claim("proposals", "worker")
    assert store.cancel("different-project", job_id) is False
    assert store.cancel(project, job_id)
    with pytest.raises(LeaseLost):
        store.save_stage(lease, "proposal", {})
    assert status(store, job_id)[0] == "cancelled"


def test_retry_backoff_and_bounded_failure(store):
    _, job_id = enqueue(store, max_attempts=2)
    first = store.claim("proposals", "worker")
    store.save_stage(first, "discovery", {})
    store.fail(first)
    assert store.claim("proposals", "worker") is None
    with store.connect() as conn:
        conn.execute(
            "UPDATE worker_job SET available_at=clock_timestamp() WHERE job_id=%s", (job_id,)
        )
    second = store.claim("proposals", "worker")
    assert store.artifacts(second) == {"discovery": {}}
    store.fail(second)
    assert status(store, job_id)[:2] == ("failed", 2)


def test_sink_exception_rolls_back_effects_and_application_marker(store):
    project, job_id = enqueue(store)
    lease = store.claim("proposals", "worker")
    store.save_stage(lease, "proposal", proposal(lease))

    def failing(conn, lease, artifacts):
        ProposalRunSink()(conn, lease, artifacts)
        raise RuntimeError("injected sink failure")

    with pytest.raises(RuntimeError, match="sink failure"):
        store.complete(lease, ("proposal",), failing)
    with store.connect() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM worker_application WHERE job_id=%s", (job_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM worker_proposal_run WHERE job_id=%s", (job_id,)
            ).fetchone()[0]
            == 0
        )
    store.complete(lease, ("proposal",), ProposalRunSink())
    assert status(store, job_id)[0] == "succeeded"
    assert not store.cancel(project, job_id)


def test_deadline_crossed_inside_sink_rolls_back_all_effects(store):
    project, job_id = enqueue(store)
    lease = store.claim("proposals", "worker")
    store.save_stage(lease, "proposal", proposal(lease))

    def crossing_deadline(conn, lease, artifacts):
        ProposalRunSink()(conn, lease, artifacts)
        conn.execute(
            "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE job_id=%s",
            (lease.job_id,),
        )

    with pytest.raises(LeaseLost):
        store.complete(lease, ("proposal",), crossing_deadline)
    with store.connect() as conn:
        for table in ("worker_application", "worker_proposal_run"):
            assert (
                conn.execute(f"SELECT count(*) FROM {table} WHERE job_id=%s", (job_id,)).fetchone()[
                    0
                ]
                == 0
            )
    assert status(store, job_id)[0] == "running"
    store.cancel(project, job_id)


@pytest.mark.parametrize(
    "changes",
    [
        {"approval_state": None},
        {"review_tier": "human_reviewed"},
        {"gold_eligible": True},
        {"training_eligible": "false"},
    ],
)
def test_database_rejects_human_or_null_fields_without_python_sink(store, changes):
    from psycopg.types.json import Jsonb

    project, job_id = enqueue(store)
    lease = store.claim("proposals", "worker")
    record = proposal(lease) | changes
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        conn.execute(
            "INSERT INTO worker_proposal_run(run_id,job_id,project_key,sha256,record) "
            "VALUES (%s,%s,%s,%s,%s)",
            (lease.input["run_id"], job_id, project, "a" * 64, Jsonb(record)),
        )
    store.cancel(project, job_id)


def proposal(lease):
    return {
        "run_id": lease.input["run_id"],
        "project_id": lease.project_key,
        "approval_state": "proposed",
        "review_tier": "unreviewed",
        "scope_completeness": "unreviewed",
        "training_eligible": False,
        "gold_eligible": False,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("approval_state", "human_accepted"),
        ("review_tier", "adjudicated"),
        ("scope_completeness", "exhaustive_for_scope"),
        ("training_eligible", True),
        ("gold_eligible", True),
        ("training_eligible", 0),
    ],
)
def test_proposal_sink_refuses_human_and_eligibility_fields(store, field, value):
    project, job_id = enqueue(store)
    lease = store.claim("proposals", "worker-ml")
    record = proposal(lease) | {field: value}
    store.save_stage(lease, "proposal", record)
    with pytest.raises(ValueError, match="proposed/unreviewed"):
        store.complete(lease, ("proposal",), ProposalRunSink())
    with store.connect() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM worker_proposal_run WHERE job_id=%s", (job_id,)
            ).fetchone()[0]
            == 0
        )
    store.cancel(project, job_id)


def test_runner_discovery_saved_before_teacher_and_resume_never_repeats_it(store):
    project, job_id = enqueue(store)
    events = []
    lease = store.claim("proposals", "crashed-worker")
    store.save_stage(lease, "discovery", {"zero_boxes": True})
    expire(store, lease)

    def teacher(context):
        assert context.artifacts == {"discovery": {"zero_boxes": True}}
        context.heartbeat()
        events.append("teacher")
        return proposal(context.lease)

    runner = JobRunner(
        store,
        "proposals",
        (
            Stage("discovery", lambda ctx: pytest.fail("must resume discovery")),
            Stage("proposal", teacher),
        ),
        ProposalRunSink(),
    )
    assert runner.run_once("worker-ml")
    assert not runner.run_once("worker-ml")
    assert events == ["teacher"] and status(store, job_id)[0] == "succeeded"
    with store.connect() as conn:
        record = conn.execute(
            "SELECT record FROM worker_proposal_run WHERE job_id=%s", (job_id,)
        ).fetchone()[0]
        assert record["approval_state"] == "proposed"
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            conn.execute("UPDATE worker_proposal_run SET record='{}' WHERE job_id=%s", (job_id,))


def test_forged_generation_and_incomplete_stages_cannot_commit(store):
    project, job_id = enqueue(store)
    lease = store.claim("proposals", "worker")
    with pytest.raises(LeaseLost):
        store.save_stage(replace(lease, generation=lease.generation + 1), "fake", {})
    with pytest.raises(ValueError, match="incomplete"):
        store.complete(lease, ("missing",), lambda *args: pytest.fail("incomplete"))
    store.cancel(project, job_id)


def test_offline_workflow_pins_configuration_and_persists_zero_detection_review(store):
    import hashlib

    from labelloop_core.domain import Recipe, SourceAsset
    from labelloop_core.registry import RegistryState
    from labelloop_worker_core.jobs import ProposalJobRunner, StageContext
    from labelloop_worker_ml.proposal_workflow import ProposalWorkflow
    from labelloop_worker_ml.proposals import DiscoveryResult, ProposalProvider, ProposalRequest

    original = b"recorded source fixture"
    sha256 = hashlib.sha256(original).hexdigest()
    source = SourceAsset("asset", str(uuid4()), sha256, f"sha256/{sha256[:2]}/{sha256}", 400, 300)
    request = ProposalRequest(
        str(uuid4()),
        source,
        0,
        "a" * 64,
        "scope",
        "b" * 64,
        Recipe("full320", 320, "area"),
        ("class",),
    )
    events = []

    class Blind:
        def discover(self, source, original, scope):
            events.append("discovery")
            return DiscoveryResult("blind-v1")

    class Teacher:
        def predict(self, *args):
            pytest.fail("no champion must not invoke inference")

    workflow = ProposalWorkflow(
        request,
        RegistryState(source.project_id, request.recipe.recipe_id),
        (),
        "blind-v1",
        ProposalProvider(Teacher()),
        Blind(),
        lambda source: original,
    )
    job_id = store.enqueue(source.project_id, request.run_id, "proposals", workflow.job_input())
    lease = store.claim("proposals", "worker-drift")
    with pytest.raises(ValueError, match="configuration differs"):
        replace(workflow, discovery_version="different").discover(StageContext(store, lease, {}))
    assert events == []
    expire(store, lease)
    runner = ProposalJobRunner(store, workflow.discover, workflow.propose, job_id=job_id)
    assert runner.run_once("worker-ml")
    assert status(store, job_id)[0] == "succeeded"
    assert not runner.run_once("worker-ml") and events == ["discovery"]
    with store.connect() as conn:
        record = conn.execute(
            "SELECT record FROM worker_proposal_run WHERE job_id=%s", (job_id,)
        ).fetchone()[0]
        assert not record["supported"] and record["teacher_candidates"] == []
        assert record["scope_completeness"] == "unreviewed"
        assert record["binding"]["source"]["sha256"] == sha256
        assert record["source"] == "none" and record["discovery_candidates"] == []


def test_request_bound_runner_does_not_consume_other_jobs_attempts(store):
    first_project, first_id = enqueue(store)
    _, second_id = enqueue(store)
    runner = ProposalJobRunner(
        store,
        lambda context: {"blind": True},
        lambda context: proposal(context.lease),
        job_id=second_id,
    )
    assert runner.run_once("worker-for-second")
    assert status(store, first_id) == ("queued", 0, 0)
    assert status(store, second_id)[0] == "succeeded"
    assert not runner.run_once("worker-for-second")
    assert status(store, first_id) == ("queued", 0, 0)
    store.cancel(first_project, first_id)


def test_autocommit_factory_still_rolls_back_complete_atomically(store):
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    autocommit_store = JobStore(lambda: psycopg.connect(url, autocommit=True))
    project, job_id = enqueue(autocommit_store)
    lease = autocommit_store.claim("proposals", "worker", job_id=job_id)
    autocommit_store.save_stage(lease, "proposal", proposal(lease))

    def failing(conn, lease, artifacts):
        ProposalRunSink()(conn, lease, artifacts)
        raise RuntimeError("sink failed after append")

    with pytest.raises(RuntimeError, match="after append"):
        autocommit_store.complete(lease, ("proposal",), failing)
    with store.connect() as conn:
        for table in ("worker_application", "worker_proposal_run"):
            assert (
                conn.execute(f"SELECT count(*) FROM {table} WHERE job_id=%s", (job_id,)).fetchone()[
                    0
                ]
                == 0
            )
    assert status(store, job_id)[0] == "running"
    autocommit_store.complete(lease, ("proposal",), ProposalRunSink())
    assert status(store, job_id)[0] == "succeeded"
    assert not store.cancel(project, job_id)


def test_heartbeat_waiting_for_unchanged_row_cannot_revive_expired_lease(store):
    import time

    project, job_id = enqueue(store)
    lease = store.claim("proposals", "worker", lease_seconds=2, job_id=job_id)
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    application_name = "lease-test-" + str(uuid4())
    waiting_store = JobStore(lambda: psycopg.connect(url, application_name=application_name))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.connect() as locked:
            locked.execute("SELECT job_id FROM worker_job WHERE job_id=%s FOR UPDATE", (job_id,))
            future = pool.submit(waiting_store.heartbeat, lease)
            deadline = time.monotonic() + 1.5
            while True:
                with store.connect() as observer:
                    blocked = observer.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE application_name=%s "
                        "AND wait_event_type='Lock'",
                        (application_name,),
                    ).fetchone()[0]
                if blocked:
                    break
                assert time.monotonic() < deadline, "heartbeat did not reach the row lock"
                time.sleep(0.01)
            locked.execute("SELECT pg_sleep(2.1)")
        with pytest.raises(LeaseLost):
            future.result(timeout=5)
    with store.connect() as conn:
        assert conn.execute(
            "SELECT lease_until < clock_timestamp() FROM worker_job WHERE job_id=%s", (job_id,)
        ).fetchone()[0]
    store.cancel(project, job_id)


def test_blocking_stage_renews_lease_during_provider_call(store):
    project, job_id = enqueue(store)

    def blocking_provider(context):
        with store.connect() as conn:
            conn.execute("SELECT pg_sleep(1.4)")
        return {"result": "finished beyond initial one-second lease"}

    runner = JobRunner(
        store,
        "proposals",
        (Stage("blocking", blocking_provider),),
        lambda *args: None,
        job_id=job_id,
        lease_seconds=1,
        max_stage_seconds=5,
    )
    assert runner.run_once("worker")
    assert status(store, job_id)[0] == "succeeded"
    assert not store.cancel(project, job_id)


def test_stage_runtime_ceiling_discards_late_results(store):
    project, job_id = enqueue(store)

    def hung_provider(context):
        with store.connect() as conn:
            conn.execute("SELECT pg_sleep(1.2)")
        return {"late": True}

    runner = JobRunner(
        store,
        "proposals",
        (Stage("blocking", hung_provider),),
        lambda *args: pytest.fail("must not apply"),
        job_id=job_id,
        lease_seconds=1,
        max_stage_seconds=1,
    )
    assert runner.run_once("worker")
    with store.connect() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM worker_stage_artifact WHERE job_id=%s", (job_id,)
            ).fetchone()[0]
            == 0
        )
    store.cancel(project, job_id)
