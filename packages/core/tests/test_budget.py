from dataclasses import replace
from typing import Any

import pytest

from labelloop_core.budget import (
    BudgetGate,
    BudgetPolicy,
    BudgetRefused,
    LedgerEntry,
    SpendApproval,
    SpendRequest,
    TokenPrices,
    TokenUsage,
)


def request(**changes: Any) -> SpendRequest:
    return replace(
        SpendRequest("one", "project", "iteration", "2026-11", 40, "v1", "a" * 64), **changes
    )


def gate(**changes: Any) -> BudgetGate:
    return replace(BudgetGate("project", BudgetPolicy(100, 80, 60, True)), **changes)


def test_new_projects_are_disabled_and_manual_by_default() -> None:
    ledger = BudgetGate("project")
    assert not ledger.policy.autonomous_spend
    with pytest.raises(BudgetRefused, match="budget_not_configured"):
        ledger.reserve(request())
    manual = gate(policy=BudgetPolicy(100, 80, 60))
    with pytest.raises(BudgetRefused, match="approval_required"):
        manual.reserve(request())
    approval = SpendApproval("authenticated-admin", request().digest)
    reserved = manual.reserve(request(), approval=approval)
    assert reserved.entry("one").approval == approval
    assert manual.entries == ()


@pytest.mark.parametrize(
    "change",
    [
        {"estimate": 41},
        {"project_id": "other"},
        {"iteration_id": "other"},
        {"month": "2026-12"},
        {"price_version": "v2"},
        {"configuration_sha256": "b" * 64},
        {"operation_id": "other"},
    ],
)
def test_click_binds_every_dry_run_field(change: dict[str, Any]) -> None:
    approval = SpendApproval("admin", request().digest)
    altered = request(**change)
    assert altered.digest != approval.request_digest
    with pytest.raises(ValueError):
        gate().reserve(altered, approval=approval)


@pytest.mark.parametrize(
    "policy",
    [
        BudgetPolicy(None, 80, 60, True),
        BudgetPolicy(100, None, 60, True),
        BudgetPolicy(100, 80, None, True),
    ],
)
def test_every_budget_limit_is_required(policy: BudgetPolicy) -> None:
    with pytest.raises(BudgetRefused, match="budget_not_configured"):
        gate(policy=policy).reserve(request())


@pytest.mark.parametrize(
    "policy,second",
    [
        (BudgetPolicy(100, 100, 39, True), False),
        (BudgetPolicy(79, 100, 60, True), True),
        (BudgetPolicy(100, 79, 60, True), True),
    ],
)
def test_limits_refuse_without_adding_an_entry(policy: BudgetPolicy, second: bool) -> None:
    ledger = gate(policy=policy)
    if second:
        ledger = ledger.reserve(request())
    before = ledger.entries
    with pytest.raises(BudgetRefused, match="budget_exceeded"):
        ledger.reserve(request(operation_id="two"))
    assert ledger.entries == before


def test_exact_boundaries_and_project_month_iteration_scopes() -> None:
    ledger = gate(policy=BudgetPolicy(80, 80, 40, True)).reserve(request())
    ledger = ledger.reserve(request(operation_id="two"))
    assert ledger.monthly_exposure("2026-11") == 80
    assert ledger.iteration_exposure("iteration") == 80
    with pytest.raises(BudgetRefused, match="budget_exceeded"):
        ledger.reserve(request(operation_id="three", month="2026-12"))
    # A new iteration gets its own limit, but shares the monthly ceiling.
    with pytest.raises(BudgetRefused, match="budget_exceeded"):
        ledger.reserve(request(operation_id="three", iteration_id="new"))
    next_month = ledger.reserve(request(operation_id="three", iteration_id="new", month="2026-12"))
    assert next_month.monthly_exposure("2026-12") == 40
    with pytest.raises(BudgetRefused, match="project_mismatch"):
        ledger.reserve(request(operation_id="foreign", project_id="other"))


def test_reserved_dispatched_and_uncertain_costs_all_hold_the_estimate() -> None:
    reserved = gate().reserve(request())
    dispatched = reserved.dispatch("one")
    uncertain = dispatched.unconfirmed("one")
    for ledger in (reserved, dispatched, uncertain):
        assert ledger.monthly_exposure("2026-11") == 40
        assert ledger.iteration_exposure("iteration") == 40
        assert ledger.entry("one").actual is None
    assert uncertain.unconfirmed("one") == uncertain
    with pytest.raises(BudgetRefused, match="reconciliation_required"):
        uncertain.cancel("one")
    with pytest.raises(BudgetRefused, match="operation_not_dispatchable"):
        uncertain.dispatch("one")
    reconciled = uncertain.settle("one", 7)
    assert reconciled.monthly_exposure("2026-11") == 7
    assert reconciled.entry("one").request.estimate == 40
    assert uncertain.entry("one").state == "unconfirmed"


def test_reservation_replay_does_not_authorize_redispatch_or_rebill() -> None:
    ledger = gate().reserve(request())
    assert ledger.reserve(request()) is ledger
    dispatched = ledger.dispatch("one")
    assert dispatched.reserve(request()) is dispatched
    with pytest.raises(BudgetRefused, match="operation_not_dispatchable"):
        dispatched.dispatch("one")
    with pytest.raises(BudgetRefused, match="operation_conflict"):
        ledger.reserve(request(estimate=39))
    with pytest.raises(BudgetRefused, match="operation_conflict"):
        ledger.reserve(request(), approval=SpendApproval("admin", request().digest))
    settled = dispatched.settle("one", 0)
    assert settled.monthly_exposure("2026-11") == 0
    assert settled.settle("one", 0) is settled
    with pytest.raises(BudgetRefused, match="actual_conflict"):
        settled.settle("one", 1)


def test_cancellation_only_releases_unsent_reservations_without_reusing_ids() -> None:
    ledger = gate().reserve(request())
    cancelled = ledger.cancel("one")
    assert cancelled.monthly_exposure("2026-11") == 0
    assert cancelled.cancel("one") is cancelled
    assert cancelled.reserve(request()) is cancelled
    with pytest.raises(BudgetRefused, match="operation_not_dispatchable"):
        cancelled.dispatch("one")
    with pytest.raises(BudgetRefused, match="operation_not_dispatched"):
        cancelled.settle("one", 0)
    with pytest.raises(BudgetRefused, match="reconciliation_required"):
        ledger.dispatch("one").cancel("one")


def test_402_pauses_all_paid_stages_and_never_redispatches() -> None:
    ledger = gate().reserve(request()).reserve(request(operation_id="two"))
    paused = ledger.dispatch("one").insufficient_credits("one")
    assert paused.pause_reason == "insufficient_credits"
    assert paused.monthly_exposure("2026-11") == 80
    assert paused.insufficient_credits("one") is paused
    with pytest.raises(BudgetRefused, match="paid_stages_paused"):
        paused.dispatch("two")
    with pytest.raises(BudgetRefused, match="paid_stages_paused"):
        paused.reserve(request(operation_id="three", estimate=0))
    confirmed = paused.settle("one", 0)
    assert confirmed.pause_reason == "insufficient_credits"
    assert confirmed.monthly_exposure("2026-11") == 40
    assert confirmed.cancel("two").pause_reason == "insufficient_credits"


def test_actual_overrun_is_recorded_even_above_limits_and_pauses_dispatch() -> None:
    ledger = gate().reserve(request()).dispatch("one").settle("one", 101)
    assert ledger.entry("one").actual == 101
    assert ledger.monthly_exposure("2026-11") == 101
    assert ledger.pause_reason == "estimate_overrun"
    with pytest.raises(BudgetRefused, match="paid_stages_paused"):
        ledger.reserve(request(operation_id="two", month="2026-12"))


@pytest.mark.parametrize("transition", ["settle", "unconfirmed", "insufficient_credits"])
def test_reservations_cannot_be_mistaken_for_dispatched_operations(transition: str) -> None:
    ledger = gate().reserve(request())
    with pytest.raises(BudgetRefused, match="operation_not_dispatched"):
        if transition == "settle":
            ledger.settle("one", 0)
        elif transition == "unconfirmed":
            ledger.unconfirmed("one")
        else:
            ledger.insufficient_credits("one")


@pytest.mark.parametrize("invalid", [-1, True, 1.5, float("inf"), float("nan"), "1", None])
def test_money_and_token_inputs_are_strict_nonnegative_integers(invalid: Any) -> None:
    for create in (
        lambda: request(estimate=invalid),
        lambda: TokenUsage(invalid, 0),
        lambda: TokenUsage(0, invalid),
        lambda: TokenPrices("v1", invalid, 0),
        lambda: TokenPrices("v1", 0, invalid),
        lambda: gate().reserve(request()).dispatch("one").settle("one", invalid),
    ):
        with pytest.raises(ValueError):
            create()
    if invalid is not None:
        with pytest.raises(ValueError):
            BudgetPolicy(monthly_limit=invalid)


def test_token_prices_are_exact_and_round_up_once_for_the_combined_usage() -> None:
    prices = TokenPrices("fixture-v1", 2_000_000, 8_000_000)
    assert prices.cost(TokenUsage(100, 20)) == 360
    assert prices.cost(TokenUsage(0, 0)) == 0
    assert TokenPrices("fractional", 1, 1).cost(TokenUsage(1, 1)) == 1
    huge = 10**18
    assert prices.cost(TokenUsage(huge, huge)) == 10 * huge


@pytest.mark.parametrize(
    "change",
    [
        {"month": "2026-13"},
        {"month": "2026-1"},
        {"month": "2026-11Z"},
        {"operation_id": " "},
        {"iteration_id": ""},
        {"project_id": "x" * 257},
        {"price_version": ""},
        {"configuration_sha256": "A" * 64},
        {"configuration_sha256": "g" * 64},
    ],
)
def test_requests_reject_malformed_context(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        request(**change)


def test_manual_policy_change_preserves_history_but_stops_unapproved_dispatch() -> None:
    ledger = gate().reserve(request()).reserve(request(operation_id="two")).dispatch("one")
    manual = replace(ledger, policy=BudgetPolicy(100, 80, 60))
    settled = manual.settle("one", 7)
    assert settled.entry("one").reserved_autonomously
    assert settled.monthly_exposure("2026-11") == 47
    with pytest.raises(BudgetRefused, match="approval_required"):
        settled.dispatch("two")
    assert settled.cancel("two").monthly_exposure("2026-11") == 7


@pytest.mark.parametrize(
    "policy",
    [
        BudgetPolicy(),
        BudgetPolicy(39, 80, 60, True),
        BudgetPolicy(100, 39, 60, True),
        BudgetPolicy(100, 80, 39, True),
    ],
)
def test_dispatch_rechecks_current_limits(policy: BudgetPolicy) -> None:
    ledger = replace(gate().reserve(request()), policy=policy)
    with pytest.raises(BudgetRefused):
        ledger.dispatch("one")
    assert ledger.entry("one").state == "reserved"


def test_ledger_reconstruction_rejects_inconsistent_state() -> None:
    entry = LedgerEntry(request(), reserved_autonomously=True)
    with pytest.raises(ValueError, match="unique"):
        gate(entries=(entry, entry))
    with pytest.raises(ValueError, match="project"):
        gate(entries=(replace(entry, request=request(project_id="other")),))
    with pytest.raises(ValueError, match="actuals"):
        replace(entry, actual=1)
    with pytest.raises(ValueError, match="actuals"):
        replace(entry, state="settled")
    with pytest.raises(ValueError, match="402"):
        gate(entries=(replace(entry, state="insufficient_credits"),))
    with pytest.raises(ValueError, match="overrun"):
        gate(entries=(replace(entry, state="settled", actual=41),))
    with pytest.raises(ValueError, match="click"):
        LedgerEntry(request())
    with pytest.raises(ValueError, match="immutable tuple"):
        gate(entries=[entry])
    with pytest.raises(ValueError, match="boolean"):
        BudgetPolicy(autonomous_spend=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown operation"):
        gate().dispatch("missing")
