"""Disposable PostgreSQL only; enabled explicitly in the Actions lease job."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest

from labelloop_worker_core.jobs import (
    JobRunner,
    JobStore,
    LeaseLost,
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
    runner = ProposalJobRunner(store, workflow.discover, workflow.propose)
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
