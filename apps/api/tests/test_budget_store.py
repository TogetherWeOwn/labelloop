"""Real races/rollback use per-test disposable PostgreSQL schemas in Actions only."""

import io
import json
import os
import runpy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import DBAPIError

from labelloop_api import budget_schema as db
from labelloop_api.budget_store import (
    BillingCharge,
    BudgetStore,
    DispatchIntent,
    decode_entry,
    digest,
)
from labelloop_api.review_budget import ReviewBatch, ReviewPriceTable, ReviewUsage
from labelloop_api.schema import project
from labelloop_core.budget import (
    BudgetPolicy,
    BudgetRefused,
    LedgerEntry,
    SpendApproval,
    SpendRequest,
    TokenPrices,
    TokenUsage,
)

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
PRICES = TokenPrices("fixture-v1", 1_000_000, 2_000_000)
INTENT = DispatchIntent("llm", "fixture", True, 10, 5)
MIGRATION = Path(__file__).parents[1] / "migrations/versions/budget_0001_ledger.py"
REVIEW_MIGRATION = MIGRATION.with_name("budget_0002_review_requests.py")
REVIEW_PRICES = ReviewPriceTable(
    "review-fixture-v1",
    "fixture",
    "fixture-model",
    1_000_000,
    2_000_000,
    3_000_000,
    "2026-01-01",
    "https://example.com/fixture-prices",
)
BATCH = ReviewBatch("annotation-job", ("image-a", "image-b"), "fixture-model", "release-v1", 1)
BATCH_INTENT = replace(INTENT, review_batch=BATCH)


@dataclass(frozen=True)
class Actor:
    id: str
    kind: str
    role: str

    def permits(self, permission):
        return permission == "admin" and self.kind == "human" and self.role == "admin"


ADMIN = Actor("admin-fixture", "human", "admin")
LLM = Actor("llm-fixture", "service", "worker-llm")
PLATFORM = Actor("platform-fixture", "service", "worker-platform")


def request(operation_id="one", *, project_id="budget-fixture", **kwargs):
    return SpendRequest(
        **{
            "operation_id": operation_id,
            "project_id": project_id,
            "iteration_id": "iteration",
            "month": "2026-11",
            "estimate": 40,
            "price_version": PRICES.version,
            "configuration_sha256": "a" * 64,
            **kwargs,
        }
    )


def test_codec_and_strict_price_contract():
    from dataclasses import asdict

    req = request()
    entry = LedgerEntry(req, SpendApproval(ADMIN.id, req.digest))
    assert decode_entry(json.loads(json.dumps(asdict(entry)))) == entry
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    for amount in (-1, True, 1.0, "1"):
        with pytest.raises(ValueError):
            BillingCharge("one", amount, "b" * 64)
    with pytest.raises(ValueError):
        DispatchIntent("llm", "https://invalid.example/key")
    with pytest.raises(ValueError):
        DispatchIntent("llm", "fixture", max_input_tokens=10)
    with pytest.raises(ValueError):
        BudgetStore(sa.create_engine("sqlite://"))


def test_migration_is_frozen_and_matches_metadata():
    migration = runpy.run_path(str(MIGRATION))
    assert migration["revision"] == "budget_0001"
    assert migration["down_revision"] == "0002_local_models"
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration["upgrade"]()
        review_migration = runpy.run_path(str(REVIEW_MIGRATION))
        assert review_migration["down_revision"] == "budget_0001"
        review_migration["upgrade"]()
    ddl = output.getvalue()
    for table in db.metadata.tables:
        assert f"CREATE TABLE {table}" in ddl
    assert "CREATE TRIGGER budget_event_immutable" in ddl
    assert "CREATE TRIGGER budget_operation_identity" in ddl


@pytest.fixture
def store(monkeypatch):
    if os.environ.get("LABELLOOP_BUDGET_POSTGRES") != "1":
        pytest.skip("disposable PostgreSQL Actions fixture only")
    url = os.environ["DATABASE_URL"]
    schema = "budget_test_" + uuid4().hex
    control = sa.create_engine(url)
    with control.begin() as connection:
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    engine = sa.create_engine(
        url,
        connect_args={"options": f"-csearch_path={schema},public"},
        pool_size=10,
        max_overflow=0,
    )
    try:
        with engine.begin() as connection:
            project.create(connection)
            with Operations.context(MigrationContext.configure(connection)):
                runpy.run_path(str(MIGRATION))["upgrade"]()
                runpy.run_path(str(REVIEW_MIGRATION))["upgrade"]()
            connection.execute(
                sa.insert(project).values(
                    project_key="budget-fixture",
                    workspace_id=uuid4(),
                    task="detect",
                    scope="fixture",
                    domain="licence-clean",
                )
            )
        result = BudgetStore(engine, lambda: NOW)
        result.set_policy(
            "budget-fixture",
            actor=ADMIN,
            expected_revision=0,
            policy=BudgetPolicy(100, 100, 100),
            pixel_providers=("fixture",),
            prices=PRICES,
        )
        yield result
    finally:
        engine.dispose()
        with control.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


def reserve(store, req=None, intent=INTENT):
    req = req or request()
    dry = store.prepare(req, intent, actor=LLM if intent.stage == "llm" else PLATFORM)
    return store.reserve(req.project_id, req.operation_id, dry.digest, actor=ADMIN)


def claim(store, operation_id="one", actor=LLM):
    return store.claim("budget-fixture", operation_id, actor=actor, configuration_sha256="a" * 64)


def refused(code, function, *args, **kwargs):
    with pytest.raises(BudgetRefused) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code


@pytest.mark.parametrize(
    "limits,iterations,expected",
    [
        (BudgetPolicy(100, 500, 100), True, 2),
        (BudgetPolicy(500, 100, 100), False, 2),
        (BudgetPolicy(500, 500, 39), True, 0),
    ],
)
def test_racing_reservations_obey_each_limit(store, limits, iterations, expected):
    store.set_policy(
        "budget-fixture",
        actor=ADMIN,
        expected_revision=1,
        policy=limits,
        pixel_providers=("fixture",),
        prices=PRICES,
    )
    dry_runs = []
    for index in range(8):
        req = request(str(index), iteration_id=str(index) if iterations else "iteration")
        try:
            dry_runs.append(store.prepare(req, INTENT, actor=LLM))
        except BudgetRefused as error:
            assert error.code == "budget_exceeded"
    if not dry_runs:
        assert expected == 0
        return
    barrier = Barrier(len(dry_runs))

    def run(dry):
        barrier.wait(timeout=20)
        try:
            store.reserve("budget-fixture", dry.request.operation_id, dry.digest, actor=ADMIN)
            return True
        except BudgetRefused as error:
            assert error.code == "budget_exceeded"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(run, dry_runs)) == expected
    gate = store.snapshot("budget-fixture")
    assert gate.monthly_exposure("2026-11") == expected * 40
    assert all(
        gate.iteration_exposure(e.request.iteration_id) <= limits.iteration_limit
        for e in gate.entries
    )


def test_one_claim_across_workers_and_fresh_engine_restart(store):
    reserve(store)
    barrier = Barrier(8)

    def run(_):
        barrier.wait(timeout=20)
        try:
            return claim(store)
        except BudgetRefused as error:
            assert error.code == "operation_not_dispatchable"
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = [item for item in pool.map(run, range(8)) if item]
    assert len(claims) == 1
    store.engine.dispose()
    restarted = BudgetStore(store.engine, lambda: NOW)
    refused("operation_not_dispatchable", claim, restarted)
    refused("reconciliation_required", restarted.cancel, "budget-fixture", "one", actor=ADMIN)
    assert restarted.snapshot("budget-fixture").entry("one").exposure == 40
    restarted.outcome("budget-fixture", "one", actor=LLM, status="unconfirmed")
    refused("operation_not_dispatchable", claim, restarted)
    assert restarted.snapshot("budget-fixture").entry("one").actual is None


def test_uncertainty_then_attributed_usage_with_original_prices(store):
    reserve(store)
    claim(store)
    entry = store.reconcile("budget-fixture", "one", actor=LLM)
    assert entry.state == "unconfirmed" and entry.exposure == 40 and entry.actual is None
    with store.engine.connect() as connection:
        assert connection.execute(sa.select(db.operation.c.evidence.is_(None))).scalar_one()
    refused(
        "usage_attribution_mismatch",
        store.reconcile,
        "budget-fixture",
        "one",
        actor=LLM,
        usage=TokenUsage(2, 3),
        usage_operation_id="other",
        usage_receipt_sha256="b" * 64,
    )
    store.set_policy(
        "budget-fixture",
        actor=ADMIN,
        expected_revision=1,
        policy=BudgetPolicy(100, 100, 100),
        pixel_providers=(),
        prices=TokenPrices("fixture-v2", 9_000_000, 9_000_000),
    )
    kwargs = dict(
        actor=LLM, usage=TokenUsage(2, 3), usage_operation_id="one", usage_receipt_sha256="b" * 64
    )
    entry = store.reconcile("budget-fixture", "one", **kwargs)
    assert entry.actual == 8 and entry.exposure == 8
    assert store.reconcile("budget-fixture", "one", **kwargs) == entry
    refused(
        "actual_conflict",
        store.reconcile,
        "budget-fixture",
        "one",
        **(kwargs | {"usage_receipt_sha256": "c" * 64}),
    )


def test_platform_estimate_is_not_confirmed_billing_and_402_never_retries(store):
    reserve(store, intent=DispatchIntent("platform", "fixture"))
    claim(store, actor=PLATFORM)
    store.outcome("budget-fixture", "one", actor=PLATFORM, status="insufficient_credits")
    assert store.reconcile("budget-fixture", "one", actor=PLATFORM).actual is None
    refused("paid_stages_paused", claim, store, actor=PLATFORM)
    refused("reconciliation_required", store.cancel, "budget-fixture", "one", actor=ADMIN)
    refused("paid_stages_paused", reserve, store, request("two"))
    refused(
        "billing_attribution_mismatch",
        store.reconcile,
        "budget-fixture",
        "one",
        actor=PLATFORM,
        billing=BillingCharge("other", 0, "b" * 64),
    )
    entry = store.reconcile(
        "budget-fixture", "one", actor=PLATFORM, billing=BillingCharge("one", 0, "b" * 64)
    )
    assert entry.actual == 0
    assert store.snapshot("budget-fixture").pause_reason == "insufficient_credits"


def test_actual_overrun_counts_real_cost_and_pauses_even_after_policy_edit(store):
    reserve(store)
    claim(store)
    assert store.reconcile("budget-fixture", "one", actor=LLM).actual is None
    entry = store.reconcile(
        "budget-fixture",
        "one",
        actor=LLM,
        usage=TokenUsage(200, 0),
        usage_operation_id="one",
        usage_receipt_sha256="b" * 64,
    )
    assert entry.exposure == 200
    assert store.snapshot("budget-fixture").pause_reason == "estimate_overrun"
    store.set_policy(
        "budget-fixture",
        actor=ADMIN,
        expected_revision=1,
        policy=BudgetPolicy(1000, 1000, 1000),
        prices=PRICES,
    )
    refused("paid_stages_paused", reserve, store, request("two"))


def test_changed_estimate_intent_or_policy_invalidates_click(store):
    req = request()
    dry = store.prepare(req, INTENT, actor=LLM)
    changed = store.prepare(replace(req, estimate=50), INTENT, actor=LLM)
    refused(
        "stale_dry_run", store.reserve, req.project_id, req.operation_id, dry.digest, actor=ADMIN
    )
    latest = store.prepare(
        replace(req, estimate=50), replace(INTENT, sends_pixels=False), actor=LLM
    )
    refused(
        "stale_dry_run",
        store.reserve,
        req.project_id,
        req.operation_id,
        changed.digest,
        actor=ADMIN,
    )
    store.set_policy(
        req.project_id,
        actor=ADMIN,
        expected_revision=1,
        policy=BudgetPolicy(100, 100, 100),
        prices=PRICES,
    )
    refused(
        "stale_dry_run", store.reserve, req.project_id, req.operation_id, latest.digest, actor=ADMIN
    )
    reserve(store, intent=replace(INTENT, sends_pixels=False))
    store.set_policy(
        req.project_id,
        actor=ADMIN,
        expected_revision=2,
        policy=BudgetPolicy(100, 100, 100),
        prices=PRICES,
    )
    refused("stale_dry_run", claim, store)
    store.cancel(req.project_id, req.operation_id, actor=ADMIN)
    assert store.snapshot(req.project_id).entry(req.operation_id).exposure == 0
    refused("operation_conflict", reserve, store, intent=replace(INTENT, sends_pixels=False))


def test_roles_privacy_forecast_and_current_dispatch_month(store):
    req = request()
    reviewer = Actor("reviewer", "human", "reviewer")
    refused(
        "admin_required",
        store.set_policy,
        req.project_id,
        actor=LLM,
        expected_revision=1,
        policy=BudgetPolicy(100, 100, 100),
    )
    refused("admin_required", store.prepare, req, INTENT, actor=reviewer)
    refused("worker_scope_mismatch", store.prepare, req, INTENT, actor=PLATFORM)
    refused("forecast_required", store.prepare, req, DispatchIntent("llm", "fixture"), actor=LLM)
    refused("estimate_below_forecast", store.prepare, replace(req, estimate=1), INTENT, actor=LLM)
    refused(
        "price_version_mismatch",
        store.prepare,
        replace(req, price_version="other"),
        INTENT,
        actor=LLM,
    )
    dry = store.prepare(req, INTENT, actor=LLM)
    refused(
        "approval_required", store.reserve, req.project_id, req.operation_id, dry.digest, actor=LLM
    )
    store.reserve(req.project_id, req.operation_id, dry.digest, actor=ADMIN)
    refused(
        "intent_mismatch",
        store.claim,
        req.project_id,
        req.operation_id,
        actor=LLM,
        configuration_sha256="b" * 64,
    )
    refused("worker_scope_mismatch", claim, store, actor=PLATFORM)
    store.clock = lambda: datetime(2026, 12, 1, tzinfo=UTC)
    refused("reservation_month_mismatch", claim, store)
    store.cancel(req.project_id, req.operation_id, actor=ADMIN)
    store.set_policy(
        req.project_id,
        actor=ADMIN,
        expected_revision=1,
        policy=BudgetPolicy(100, 100, 100),
        prices=PRICES,
    )
    refused(
        "pixel_provider_disallowed",
        store.prepare,
        request("two", month="2026-12"),
        INTENT,
        actor=LLM,
    )


def test_autonomous_policy_still_persists_principal_and_single_use_claim(store):
    store.set_policy(
        "budget-fixture",
        actor=ADMIN,
        expected_revision=1,
        policy=BudgetPolicy(100, 100, 100, True),
        pixel_providers=("fixture",),
        prices=PRICES,
    )
    req = request()
    dry = store.prepare(req, INTENT, actor=LLM)
    entry = store.reserve(req.project_id, req.operation_id, dry.digest, actor=LLM)
    assert entry.approval is None and entry.reserved_autonomously
    assert store.reserve(req.project_id, req.operation_id, dry.digest, actor=LLM) == entry
    claim(store)
    with store.engine.connect() as connection:
        records = connection.execute(sa.select(db.event)).mappings().all()
    assert {row["principal_id"] for row in records} == {ADMIN.id, LLM.id}
    assert all(len(row["principal_version"]) == 64 for row in records)
    assert records[0]["policy_revision"] > 0


def test_audit_failure_rolls_back_reservation_claim_actual_and_pause(store):
    reserve(store)
    second = store.prepare(request("two"), INTENT, actor=LLM)
    with store.engine.begin() as connection:
        connection.execute(
            sa.text("""
CREATE FUNCTION reject_budget_test_event() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'injected audit failure'; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER reject_budget_event BEFORE INSERT ON budget_event
FOR EACH ROW EXECUTE FUNCTION reject_budget_test_event();
""")
        )
    with pytest.raises(DBAPIError):
        claim(store)
    assert store.snapshot("budget-fixture").entry("one").state == "reserved"
    with pytest.raises(DBAPIError):
        store.reserve("budget-fixture", "two", second.digest, actor=ADMIN)
    assert len(store.snapshot("budget-fixture").entries) == 1
    with store.engine.begin() as connection:
        connection.execute(sa.text("DROP TRIGGER reject_budget_event ON budget_event"))
    claim(store)
    with store.engine.begin() as connection:
        connection.execute(
            sa.text("""
CREATE TRIGGER reject_budget_event BEFORE INSERT ON budget_event
FOR EACH ROW EXECUTE FUNCTION reject_budget_test_event();
""")
        )
    with pytest.raises(DBAPIError):
        store.reconcile(
            "budget-fixture",
            "one",
            actor=LLM,
            usage=TokenUsage(200, 0),
            usage_operation_id="one",
            usage_receipt_sha256="b" * 64,
        )
    gate = store.snapshot("budget-fixture")
    assert gate.entry("one").state == "dispatched"
    assert gate.entry("one").exposure == 40 and gate.pause_reason is None


def test_history_dispatch_identity_and_payloads_are_not_mutable(store):
    reserve(store)
    claim(store)
    for statement in (
        sa.delete(db.event),
        sa.delete(db.operation),
        sa.update(db.operation).values(claim_id=None, claimed_by=None),
    ):
        with pytest.raises(DBAPIError), store.engine.begin() as connection:
            connection.execute(statement)
    with store.engine.connect() as connection:
        rows = connection.execute(sa.select(db.event)).mappings().all()
    serialized = json.dumps([dict(row) for row in rows], default=str)
    assert "authorization" not in serialized.lower()
    assert "api_key" not in serialized.lower()
    assert "https://" not in serialized
    assert "configuration_sha256" in serialized
    with pytest.raises(TypeError):
        store.outcome(
            "budget-fixture",
            "one",
            actor=LLM,
            status="unconfirmed",
            provider_body={"api_key": "fixture-secret-never-stored"},
        )


def test_iteration_does_not_reset_across_month_and_projects_are_isolated(store):
    reserve(store)
    claim(store)
    store.clock = lambda: datetime(2026, 12, 1, tzinfo=UTC)
    reserve(store, request("two", month="2026-12"))
    refused("budget_exceeded", reserve, store, request("three", month="2026-12"))
    with store.engine.begin() as connection:
        connection.execute(
            sa.insert(project).values(
                project_key="another",
                workspace_id=uuid4(),
                task="detect",
                scope="fixture",
                domain="licence-clean",
            )
        )
    store.set_policy(
        "another",
        actor=ADMIN,
        expected_revision=0,
        policy=BudgetPolicy(100, 100, 100),
        pixel_providers=("fixture",),
        prices=PRICES,
    )
    reserve(store, request("one", project_id="another", month="2026-12"))
    assert store.snapshot("another").monthly_exposure("2026-12") == 40
    assert store.snapshot("budget-fixture").iteration_exposure("iteration") == 80


def configure_review(store, *, autonomous=False, prices=REVIEW_PRICES):
    store.set_policy(
        "budget-fixture",
        actor=ADMIN,
        expected_revision=1,
        policy=BudgetPolicy(100, 100, 100, autonomous),
        pixel_providers=("fixture",),
        prices=PRICES,
        review_prices=prices,
    )


def reserve_review(store, *, intent=BATCH_INTENT, estimate=40, actor=ADMIN):
    req = request(price_version=REVIEW_PRICES.version, estimate=estimate)
    dry = store.prepare(req, intent, actor=LLM)
    store.reserve(req.project_id, req.operation_id, dry.digest, actor=actor)
    return dry


def review_claim(store, request_id="request-a", **kwargs):
    return store.claim_review_request(
        "budget-fixture",
        "one",
        request_id,
        **{
            "actor": LLM,
            "configuration_sha256": "a" * 64,
            "image_id": "image-a",
            "pass_index": 0,
            "estimate": 20,
            "forecast": ReviewUsage(10, 5),
            **kwargs,
        },
    )


def review_actual(store, request_id="request-a", **kwargs):
    return store.reconcile_review_request(
        "budget-fixture",
        "one",
        request_id,
        **{
            "actor": LLM,
            "usage": ReviewUsage(2, 3),
            "usage_request_id": request_id,
            "receipt_sha256": "b" * 64,
            **kwargs,
        },
    )


def test_review_contract_round_trip_and_child_exposure_floor():
    from dataclasses import asdict

    decoded = DispatchIntent(**json.loads(json.dumps(asdict(BATCH_INTENT))))
    assert decoded == BATCH_INTENT
    assert len(decoded.review_batch.image_set_sha256) == 64
    assert REVIEW_PRICES.cost(ReviewUsage(1, 1, 1)) == 6
    req = request()
    entry = LedgerEntry(req, SpendApproval(ADMIN.id, req.digest), exposure_floor=200)
    assert entry.actual is None and entry.exposure == 200
    assert decode_entry(json.loads(json.dumps(asdict(entry)))) == entry
    with pytest.raises(ValueError):
        replace(entry, state="settled", actual=40)
    for changed in (
        {"currency": "EUR"},
        {"source_url": "https://user:password@example.com"},
        {"image_per_million": -1},
        {"effective_date": "2026-1-1"},
    ):
        with pytest.raises(ValueError):
            replace(REVIEW_PRICES, **changed)
    for changed in (
        {"max_passes": True},
        {"image_ids": ("image-b", "image-a")},
        {"image_ids": ("image-a", "image-a")},
        {"max_passes": 0},
    ):
        with pytest.raises(ValueError):
            replace(BATCH, **changed)


def test_review_binding_prices_full_max_pass_reservation_and_stale_click(store):
    configure_review(store)
    req = request(price_version=REVIEW_PRICES.version)
    dry = store.prepare(req, BATCH_INTENT, actor=LLM)
    for batch in (
        replace(BATCH, image_ids=("image-b",)),
        replace(BATCH, model_id="other"),
        replace(BATCH, release_id="release-v2"),
        replace(BATCH, max_passes=2),
    ):
        intent = replace(BATCH_INTENT, review_batch=batch)
        # Changed model requires its own price table; no fallback to another model's rates.
        if batch.model_id == "other":
            refused("price_version_mismatch", store.prepare, req, intent, actor=LLM)
        elif batch.max_passes == 2:
            refused("estimate_below_forecast", store.prepare, req, intent, actor=LLM)
        else:
            store.prepare(req, intent, actor=LLM)
            refused(
                "stale_dry_run",
                store.reserve,
                req.project_id,
                req.operation_id,
                dry.digest,
                actor=ADMIN,
            )
    reserve_review(
        store, intent=replace(BATCH_INTENT, review_batch=replace(BATCH, max_passes=2)), estimate=80
    )
    assert store.snapshot(req.project_id).monthly_exposure(req.month) == 80
    refused("budget_exceeded", reserve, store, request("two"))
    refused("review_request_required", claim, store)
    refused("review_request_required", store.reconcile, req.project_id, req.operation_id, actor=LLM)


def test_review_price_table_required_admin_audit_and_policy_staleness(store):
    configure_review(store, prices=None)
    refused("no_price_table", reserve_review, store)
    store.set_policy(
        "budget-fixture",
        actor=ADMIN,
        expected_revision=2,
        policy=BudgetPolicy(100, 100, 100),
        pixel_providers=("fixture",),
        review_prices=REVIEW_PRICES,
    )
    reserve_review(store)
    store.set_policy(
        "budget-fixture",
        actor=ADMIN,
        expected_revision=3,
        policy=BudgetPolicy(100, 100, 100),
        pixel_providers=("fixture",),
        review_prices=replace(REVIEW_PRICES, version="review-fixture-v2"),
    )
    refused("stale_dry_run", review_claim, store)
    with store.engine.connect() as connection:
        events = (
            connection.execute(sa.select(db.event).where(db.event.c.action == "policy"))
            .mappings()
            .all()
        )
    assert events[-1]["principal_id"] == ADMIN.id
    assert events[-1]["record"]["review_prices"]["source_url"] == REVIEW_PRICES.source_url


def test_review_child_claim_race_exhaustion_is_durable_and_not_replayable(store):
    configure_review(store)
    reserve_review(store)
    barrier = Barrier(2)

    def run(image):
        barrier.wait(timeout=20)
        try:
            return review_claim(store, image, image_id=image, estimate=30)
        except BudgetRefused as error:
            assert error.code == "approval_exhausted"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = [c for c in pool.map(run, BATCH.image_ids) if c]
    assert len(claims) == 1
    store.engine.dispose()
    view = store.review_snapshot("budget-fixture", "one")
    assert view["pause_reason"] == "approval_exhausted"
    assert len(view["requests"]) == 1
    assert view["requests"][0]["estimate"] == 30
    assert store.snapshot("budget-fixture").entry("one").exposure == 40
    refused("approval_exhausted", review_claim, store, "request-new")
    refused("reconciliation_required", store.cancel, "budget-fixture", "one", actor=ADMIN)


def test_review_one_request_per_image_pass_across_restart_and_strict_scope(store):
    configure_review(store)
    reserve_review(store)
    for code, kwargs in (
        ("worker_scope_mismatch", {"actor": PLATFORM}),
        ("review_scope_mismatch", {"image_id": "not-in-batch"}),
        ("review_scope_mismatch", {"pass_index": 1}),
        ("intent_mismatch", {"configuration_sha256": "c" * 64}),
        ("forecast_exceeds_batch", {"forecast": ReviewUsage(11, 5)}),
        ("estimate_below_forecast", {"estimate": 1}),
    ):
        refused(code, review_claim, store, **kwargs)
    review_claim(store)
    store.engine.dispose()
    refused("operation_not_dispatchable", review_claim, store)
    refused("operation_not_dispatchable", review_claim, store, "different-request-id")
    assert len(store.review_snapshot("budget-fixture", "one")["requests"]) == 1


def test_review_unknown_then_original_price_actual_and_batch_finalization(store):
    configure_review(store)
    reserve_review(store)
    review_claim(store)
    store.reconcile_review_request("budget-fixture", "one", "request-a", actor=LLM)
    child = store.review_snapshot("budget-fixture", "one")["requests"][0]
    assert child["state"] == "unconfirmed" and child["actual"] is None
    with store.engine.connect() as connection:
        assert connection.execute(sa.select(db.review_request.c.evidence.is_(None))).scalar_one()
    refused(
        "reconciliation_required", store.finish_review_batch, "budget-fixture", "one", actor=LLM
    )
    refused("usage_attribution_mismatch", review_actual, store, usage_request_id="other")
    store.set_policy(
        "budget-fixture",
        actor=ADMIN,
        expected_revision=2,
        policy=BudgetPolicy(100, 100, 100),
        review_prices=replace(REVIEW_PRICES, input_per_million=9_000_000),
    )
    review_actual(store)
    review_actual(store)
    refused("actual_conflict", review_actual, store, receipt_sha256="c" * 64)
    entry = store.finish_review_batch("budget-fixture", "one", actor=LLM)
    assert entry.actual == 8 and entry.exposure == 8
    assert store.finish_review_batch("budget-fixture", "one", actor=LLM) == entry
    # Parent is settled, not a reusable batch allowance.
    refused("operation_not_dispatchable", review_claim, store, "request-b", image_id="image-b")


def test_review_402_and_overrun_keep_uncertain_exposure_and_pause_every_paid_stage(store):
    configure_review(store)
    reserve_review(store)
    review_claim(store)
    review_claim(store, "request-b", image_id="image-b")
    review_actual(store, usage=ReviewUsage(200, 0))
    gate = store.snapshot("budget-fixture")
    assert gate.pause_reason == "estimate_overrun"
    assert gate.entry("one").actual is None and gate.entry("one").exposure == 220
    assert store.review_snapshot("budget-fixture", "one")["pause_reason"] == "approval_exhausted"
    store.reconcile_review_request(
        "budget-fixture", "one", "request-b", actor=LLM, status="insufficient_credits"
    )
    assert store.snapshot("budget-fixture").pause_reason == "insufficient_credits"
    refused("paid_stages_paused", reserve, store, request("two"))
    refused("paid_stages_paused", review_claim, store, "third")
    refused(
        "reconciliation_required", store.finish_review_batch, "budget-fixture", "one", actor=LLM
    )
    review_actual(store, "request-b", usage=ReviewUsage(0, 0))
    assert store.finish_review_batch("budget-fixture", "one", actor=LLM).actual == 200
    assert store.snapshot("budget-fixture").pause_reason == "insufficient_credits"


def test_review_autonomous_child_limits_and_image_pricing(store):
    configure_review(store, autonomous=True)
    batch = replace(BATCH, max_image_tokens=2)
    intent = replace(BATCH_INTENT, review_batch=batch)
    refused("estimate_below_forecast", reserve_review, store, intent=intent)
    reserve_review(store, intent=intent, estimate=52, actor=LLM)
    refused("estimate_below_forecast", review_claim, store, forecast=ReviewUsage(10, 5, 2))
    review_claim(store, forecast=ReviewUsage(10, 5, 2), estimate=26)
    review_actual(store, usage=ReviewUsage(2, 3, 1))
    entry = store.finish_review_batch("budget-fixture", "one", actor=LLM)
    assert entry.actual == 11 and entry.reserved_autonomously


def test_review_audit_failure_rolls_back_child_claim_and_evidence(store):
    configure_review(store)
    reserve_review(store)
    with store.engine.begin() as connection:
        connection.execute(
            sa.text("""
CREATE FUNCTION reject_budget_test_event() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'injected audit failure'; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER reject_budget_event BEFORE INSERT ON budget_event
FOR EACH ROW EXECUTE FUNCTION reject_budget_test_event();
""")
        )
    with pytest.raises(DBAPIError):
        review_claim(store)
    assert store.review_snapshot("budget-fixture", "one")["requests"] == []
    assert store.snapshot("budget-fixture").entry("one").state == "reserved"
    with store.engine.begin() as connection:
        connection.execute(sa.text("DROP TRIGGER reject_budget_event ON budget_event"))
    review_claim(store)
    with store.engine.begin() as connection:
        connection.execute(
            sa.text("""
CREATE TRIGGER reject_budget_event BEFORE INSERT ON budget_event
FOR EACH ROW EXECUTE FUNCTION reject_budget_test_event();
""")
        )
    with pytest.raises(DBAPIError):
        review_actual(store, usage=ReviewUsage(200, 0))
    assert store.snapshot("budget-fixture").pause_reason is None
    assert store.review_snapshot("budget-fixture", "one")["requests"][0]["actual"] is None
    for statement in (
        sa.delete(db.review_request),
        sa.update(db.review_request).values(estimate=0),
        sa.update(db.review_request).values(claim_id=str(uuid4())),
    ):
        with pytest.raises(DBAPIError), store.engine.begin() as connection:
            connection.execute(statement)
