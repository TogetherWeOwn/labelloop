"""Dedicated disposable PostgreSQL acknowledgement races/restart/rollback tests."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from threading import Barrier, Event

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from test_budget_store import (
    ADMIN,
    BATCH_INTENT,
    LLM,
    NOW,
    PLATFORM,
    PRICES,
    Actor,
    claim,
    configure_review,
    refused,
    request,
    reserve,
    reserve_review,
    review_actual,
    review_claim,
)
from test_budget_store import store as store

from labelloop_api import budget_schema as db
from labelloop_api.budget_resume_store import BudgetResumeStore
from labelloop_api.budget_store import BillingCharge, BudgetStore, DispatchIntent
from labelloop_api.review_budget import ReviewUsage
from labelloop_api.schema import project
from labelloop_core.budget import BudgetPolicy, BudgetRefused

PROJECT = "budget-fixture"


def resume_store(store):
    return BudgetResumeStore(store.engine, lambda: NOW)


def pause(store, reason="insufficient_credits", operation="one"):
    reserve(store, request(operation), intent=DispatchIntent("platform", "fixture"))
    claim(store, operation, PLATFORM)
    if reason == "insufficient_credits":
        store.outcome(PROJECT, operation, actor=PLATFORM, status=reason)
    else:
        store.reconcile(
            PROJECT,
            operation,
            actor=PLATFORM,
            billing=BillingCharge(operation, 50, "b" * 64),
        )


def resume(service, state=None):
    state = state or service.paused_state(PROJECT, actor=ADMIN)
    return service.resume(
        state.project_id,
        actor=ADMIN,
        expected_revision=state.policy_revision,
        state_token=state.state_token,
    )


def rows(store):
    with store.engine.begin() as connection:
        return {
            table.name: sorted(
                (dict(row) for row in connection.execute(sa.select(table)).mappings()),
                key=str,
            )
            for table in (db.head, db.operation, db.review_request, db.dry_run, db.event)
        }


@pytest.mark.parametrize("reason", ["insufficient_credits", "estimate_overrun"])
def test_resume_restart_preserves_full_ledger_policy_claims_and_history(store, reason):
    # Unsent dry runs survive, but cannot be used after acknowledgement.
    dry = store.prepare(request("unsent"), DispatchIntent("llm", "fixture", True, 10, 5), actor=LLM)
    pause(store, reason)
    before = rows(store)
    gate = store.snapshot(PROJECT)
    service = resume_store(store)
    state = service.paused_state(PROJECT, actor=ADMIN)
    assert state.pause_reason == reason and len(state.state_token) == 67
    receipt = resume(service, state)
    assert asdict(receipt) == {"project_id": PROJECT, "policy_revision": 2}
    # Dispose pooled connections and reconstruct through the original worker store.
    store.engine.dispose()
    restarted = BudgetStore(store.engine, lambda: NOW).snapshot(PROJECT)
    assert restarted.entries == gate.entries
    assert restarted.monthly_exposure("2026-11") == gate.monthly_exposure("2026-11")
    assert restarted.pause_reason is None and restarted.acknowledged_pause
    after = rows(store)
    for table in ("budget_operation", "budget_review_request", "budget_dry_run"):
        assert after[table] == before[table]
    assert after["budget_head"][0]["policy"] == before["budget_head"][0]["policy"]
    assert len(after["budget_event"]) == len(before["budget_event"]) + 1
    event = next(row for row in after["budget_event"] if row["action"] == "resume")
    assert event["principal_id"] == ADMIN.id and event["policy_revision"] == 2
    assert event["record"]["acknowledgement"]["entries"] == [asdict(gate.entry("one"))]
    refused("policy_conflict", resume, service, state)
    refused("stale_dry_run", store.reserve, PROJECT, "unsent", dry.digest, actor=ADMIN)
    refused("stale_dry_run", claim, store, "one", PLATFORM)
    # A safe NEW operation still needs explicit approval, provider privacy and budgets.
    fresh = store.prepare(
        request("new", estimate=20), DispatchIntent("platform", "fixture"), actor=PLATFORM
    )
    refused("approval_required", store.reserve, PROJECT, "new", fresh.digest, actor=PLATFORM)
    store.reserve(PROJECT, "new", fresh.digest, actor=ADMIN)
    claim(store, "new", PLATFORM)
    refused("operation_not_dispatchable", claim, store, "new", PLATFORM)
    refused(
        "pixel_provider_disallowed",
        store.prepare,
        request("foreign"),
        DispatchIntent("platform", "other"),
        actor=PLATFORM,
    )
    refused(
        "budget_exceeded",
        store.prepare,
        request("large", estimate=100),
        DispatchIntent("platform", "fixture"),
        actor=PLATFORM,
    )


@pytest.mark.parametrize("state", ["reserved", "dispatched", "unconfirmed"])
def test_in_flight_parent_rejects_without_mutation(store, state):
    reserve(store, request("busy"))
    if state != "reserved":
        claim(store, "busy")
    if state == "unconfirmed":
        store.outcome(PROJECT, "busy", actor=LLM, status=state)
    pause(store)
    service = resume_store(store)
    assert service.paused_state(PROJECT, actor=ADMIN).state_token is None
    before = rows(store)
    refused(
        "resume_in_flight",
        service.resume,
        PROJECT,
        actor=ADMIN,
        expected_revision=1,
        state_token="v1." + "a" * 64,
    )
    assert rows(store) == before


def test_same_revision_outcome_invalidates_token_and_repauses_same_operation(store):
    pause(store)
    service = resume_store(store)
    original = service.paused_state(PROJECT, actor=ADMIN)
    # Later attributable evidence at the SAME revision changes the generation.
    store.reconcile(PROJECT, "one", actor=PLATFORM, billing=BillingCharge("one", 50, "b" * 64))
    current = service.paused_state(PROJECT, actor=ADMIN)
    assert original.policy_revision == current.policy_revision
    assert original.state_token != current.state_token
    before = rows(store)
    refused("paused_state_conflict", resume, service, original)
    assert rows(store) == before
    resume(service, current)
    # A new operation outcome pauses at the unchanged post-resume revision.
    pause(store, operation="later")
    later = service.paused_state(PROJECT, actor=ADMIN)
    assert later.policy_revision == 2 and later.pause_reason == "insufficient_credits"
    assert later.state_token not in {original.state_token, current.state_token}
    refused("policy_conflict", resume, service, current)
    resume(service, later)
    assert service.paused_state(PROJECT, actor=ADMIN).policy_revision == 3


def test_later_overrun_on_acknowledged_402_repauses(store):
    pause(store)
    service = resume_store(store)
    resume(service)
    store.reconcile(PROJECT, "one", actor=PLATFORM, billing=BillingCharge("one", 50, "b" * 64))
    state = service.paused_state(PROJECT, actor=ADMIN)
    assert state.policy_revision == 2 and state.pause_reason == "estimate_overrun"
    resume(service, state)


def test_missing_unpaused_foreign_and_denied_actor_are_noops(store):
    service = resume_store(store)
    before = rows(store)
    refused("budget_not_configured", service.paused_state, "missing", actor=ADMIN)
    refused(
        "budget_not_configured",
        service.resume,
        "missing",
        actor=ADMIN,
        expected_revision=1,
        state_token="v1." + "a" * 64,
    )
    refused(
        "paid_stages_not_paused",
        service.resume,
        PROJECT,
        actor=ADMIN,
        expected_revision=1,
        state_token="v1." + "a" * 64,
    )
    for actor in (Actor("reviewer", "human", "reviewer"), LLM, PLATFORM):
        refused("admin_required", service.paused_state, PROJECT, actor=actor)
        refused(
            "admin_required",
            service.resume,
            PROJECT,
            actor=actor,
            expected_revision=1,
            state_token="v1." + "a" * 64,
        )
    assert rows(store) == before
    with store.engine.begin() as connection:
        connection.execute(
            sa.insert(project).values(
                project_key="other",
                workspace_id="00000000-0000-0000-0000-000000000001",
                task="detect",
                scope="fixture",
                domain="fixture",
            )
        )
    store.set_policy("other", actor=ADMIN, expected_revision=0, policy=BudgetPolicy(100, 100, 100))
    pause(store)
    state = service.paused_state(PROJECT, actor=ADMIN)
    # A token never selects its project, even with a valid other project's head.
    refused(
        "paid_stages_not_paused",
        service.resume,
        "other",
        actor=ADMIN,
        expected_revision=1,
        state_token=state.state_token,
    )
    assert store.snapshot("other").entries == ()


def test_policy_edit_preserves_pause_and_invalidates_token(store):
    pause(store)
    service = resume_store(store)
    state = service.paused_state(PROJECT, actor=ADMIN)
    store.set_policy(
        PROJECT,
        actor=ADMIN,
        expected_revision=1,
        policy=BudgetPolicy(200, 200, 100),
        pixel_providers=("fixture",),
        prices=PRICES,
    )
    refused("policy_conflict", resume, service, state)
    assert service.paused_state(PROJECT, actor=ADMIN).pause_reason == "insufficient_credits"
    resume(service)
    assert store.snapshot(PROJECT).policy.monthly_limit == 200


def test_two_resumes_race_only_one_event_commits(store):
    pause(store)
    service = resume_store(store)
    state = service.paused_state(PROJECT, actor=ADMIN)
    barrier = Barrier(2)

    def run(_):
        barrier.wait(timeout=20)
        try:
            resume(service, state)
            return "committed"
        except BudgetRefused as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, range(2))) == ["committed", "policy_conflict"]
    assert len([event for event in rows(store)["budget_event"] if event["action"] == "resume"]) == 1


@pytest.mark.parametrize("writer", ["policy", "outcome"])
def test_competing_writer_holds_existing_head_lock(store, writer):
    pause(store)
    service = resume_store(store)
    state = service.paused_state(PROJECT, actor=ADMIN)
    acquired, release, started = Event(), Event(), Event()

    def held_event(connection, actor, head, action, record, operation_id=None):
        original_event(connection, actor, head, action, record, operation_id)
        if action in {"policy", "reconcile"}:
            acquired.set()
            assert release.wait(timeout=20)

    original_event = store._event
    store._event = held_event

    def write():
        if writer == "policy":
            store.set_policy(
                PROJECT, actor=ADMIN, expected_revision=1, policy=BudgetPolicy(200, 200, 100)
            )
        else:
            store.reconcile(
                PROJECT, "one", actor=PLATFORM, billing=BillingCharge("one", 50, "b" * 64)
            )

    def acknowledge():
        started.set()
        try:
            resume(service, state)
        except BudgetRefused as error:
            return error.code
        return "unexpected commit"

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(write)
        try:
            assert acquired.wait(timeout=20)
            acknowledging = pool.submit(acknowledge)
            assert started.wait(timeout=20)
            # The existing SELECT FOR UPDATE cannot be acquired concurrently.
            with store.engine.begin() as connection:
                connection.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
                with pytest.raises(DBAPIError):
                    service._head(connection, PROJECT)
        finally:
            release.set()
        writing.result(timeout=20)
        assert acknowledging.result(timeout=20) == (
            "policy_conflict" if writer == "policy" else "paused_state_conflict"
        )
    assert store.snapshot(PROJECT).pause_reason is not None


@pytest.mark.parametrize("failure", ["head", "event"])
def test_injected_post_write_failure_rolls_back_every_row(store, failure, monkeypatch):
    pause(store)
    service = resume_store(store)
    before = rows(store)
    state = service.paused_state(PROJECT, actor=ADMIN)
    if failure == "event":
        original = service._event

        def failing(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected after immutable acknowledgement insertion")

        monkeypatch.setattr(service, "_event", failing)
    else:

        def failing(*args, **kwargs):
            raise RuntimeError("injected after head update")

        monkeypatch.setattr(service, "_event", failing)
    with pytest.raises(RuntimeError):
        resume(service, state)
    assert rows(store) == before
    assert service.paused_state(PROJECT, actor=ADMIN) == state


def test_review_child_pause_exposure_and_approval_exhaustion_are_preserved(store):
    configure_review(store)
    reserve_review(store, estimate=40)
    review_claim(store, estimate=20)
    # 402 is terminal but unattributed: full parent estimate and child estimate remain.
    store.reconcile_review_request(
        PROJECT, "one", "request-a", actor=LLM, status="insufficient_credits"
    )
    before = rows(store)
    service = resume_store(store)
    state = service.paused_state(PROJECT, actor=ADMIN)
    resume(service, state)
    assert store.snapshot(PROJECT).entry("one").exposure == 40
    for table in ("budget_operation", "budget_review_request"):
        assert rows(store)[table] == before[table]
    refused("operation_not_dispatchable", review_claim, store)
    # Later usage for that same child increases exposure and pauses anew.
    review_actual(store, usage=ReviewUsage(50, 0))
    assert store.snapshot(PROJECT).pause_reason == "estimate_overrun"
    assert store.review_snapshot(PROJECT, "one")["pause_reason"] == "approval_exhausted"
    store.finish_review_batch(PROJECT, "one", actor=LLM)
    before = rows(store)
    resume(service)
    assert store.snapshot(PROJECT).entry("one").exposure == 50
    assert rows(store)["budget_review_request"] == before["budget_review_request"]
    assert rows(store)["budget_operation"] == before["budget_operation"]
    refused("approval_exhausted", review_claim, store, "new-child")


@pytest.mark.parametrize("child_state", ["dispatched", "unconfirmed"])
def test_review_children_in_flight_forbid_resume(store, child_state):
    configure_review(store)
    reserve_review(store)
    review_claim(store)
    if child_state == "unconfirmed":
        store.reconcile_review_request(PROJECT, "one", "request-a", actor=LLM)
    # A separate operation causes the project-wide pause.
    pause(store, operation="failed")
    service = resume_store(store)
    assert service.paused_state(PROJECT, actor=ADMIN).state_token is None
    before = rows(store)
    refused(
        "resume_in_flight",
        service.resume,
        PROJECT,
        actor=ADMIN,
        expected_revision=2,
        state_token="v1." + "a" * 64,
    )
    assert rows(store) == before


def test_child_overrun_below_parent_estimate_can_be_acknowledged_after_finish(store):
    configure_review(store)
    reserve_review(store, intent=replace(BATCH_INTENT, max_input_tokens=5, max_output_tokens=0))
    review_claim(store, estimate=5, forecast=ReviewUsage(5, 0))
    review_actual(store, usage=ReviewUsage(6, 0))
    assert store.snapshot(PROJECT).pause_reason == "estimate_overrun"
    service = resume_store(store)
    assert service.paused_state(PROJECT, actor=ADMIN).state_token is None
    store.finish_review_batch(PROJECT, "one", actor=LLM)
    resume(service)
    assert store.snapshot(PROJECT).entry("one").actual == 6


@pytest.mark.parametrize(
    "corruption", ["version", "actor", "project", "entry", "revision", "token"]
)
def test_malformed_or_substituted_acknowledgement_fails_closed(store, corruption):
    pause(store)
    service = resume_store(store)
    resume(service)
    # Fixture-only corruption with the immutable trigger deliberately disabled.
    with store.engine.begin() as connection:
        row = (
            connection.execute(sa.select(db.event).where(db.event.c.action == "resume"))
            .mappings()
            .one()
        )
        record = row["record"]
        updates = {}
        if corruption == "version":
            record["version"] = 2
        elif corruption == "actor":
            updates["principal_kind"] = "service"
        elif corruption == "project":
            record["acknowledgement"]["project_id"] = "other"
        elif corruption == "entry":
            record["acknowledgement"]["entries"][0]["reserved_autonomously"] = True
        elif corruption == "revision":
            record["expected_revision"] = 2
        else:
            record["state_token"] = "unknown"
        connection.execute(
            sa.text("ALTER TABLE budget_event DISABLE TRIGGER budget_event_immutable")
        )
        connection.execute(
            sa.update(db.event).where(db.event.c.id == row["id"]).values(record=record, **updates)
        )
        connection.execute(
            sa.text("ALTER TABLE budget_event ENABLE TRIGGER budget_event_immutable")
        )
    with pytest.raises(ValueError):
        service.snapshot(PROJECT)
