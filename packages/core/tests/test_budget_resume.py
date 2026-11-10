"""Acknowledgements are exact historical evidence, not general pause bypasses."""

from dataclasses import replace
from typing import Any, Literal

import pytest

from labelloop_core.budget import (
    AcknowledgedPause,
    BudgetGate,
    BudgetPolicy,
    BudgetRefused,
    LedgerEntry,
    SpendApproval,
    SpendRequest,
)


def entry(operation: str = "failed", **changes: Any) -> LedgerEntry:
    request = SpendRequest(operation, "project", "iteration", "2026-11", 10, "v1", "a" * 64)
    return LedgerEntry(request, SpendApproval("admin", request.digest), **changes)


@pytest.mark.parametrize("reason", ["insufficient_credits", "estimate_overrun"])
def test_explicit_acknowledgement_preserves_history_and_default_invariants(
    reason: Literal["insufficient_credits", "estimate_overrun"],
) -> None:
    failed = (
        entry(state="insufficient_credits")
        if reason == "insufficient_credits"
        else entry(state="settled", actual=12)
    )
    with pytest.raises(ValueError):
        BudgetGate("project", entries=(failed,))
    paused = BudgetGate("project", BudgetPolicy(100, 100, 20), (failed,), reason)
    resumed = paused.acknowledge_pause()
    assert resumed.pause_reason is None
    assert resumed.entries == paused.entries
    assert resumed.policy == paused.policy
    assert resumed.monthly_exposure("2026-11") == paused.monthly_exposure("2026-11")
    assert resumed.iteration_exposure("iteration") == failed.exposure
    assert replace(resumed) == resumed  # restart-style reconstruction
    with pytest.raises(BudgetRefused, match="operation_not_dispatchable"):
        resumed.dispatch("failed")
    new = entry("new").request
    with pytest.raises(BudgetRefused, match="approval_required"):
        resumed.reserve(new)
    assert resumed.reserve(new, approval=SpendApproval("admin", new.digest)).entry("new")
    with pytest.raises(BudgetRefused, match="paid_stages_not_paused"):
        resumed.acknowledge_pause()


def test_later_cause_on_new_or_same_operation_repauses() -> None:
    old = entry(state="insufficient_credits")
    resumed = BudgetGate("project", entries=(old,), pause_reason="insufficient_credits")
    resumed = resumed.acknowledge_pause()
    # A later attributable overrun on the exact acknowledged 402 is a fresh cause.
    overrun = resumed.settle("failed", 20)
    assert overrun.pause_reason == "estimate_overrun"
    assert (
        overrun.acknowledge_pause()
        ._replace(replace(overrun.entry("failed"), exposure_floor=21, actual=21))
        .pause_reason
        == "estimate_overrun"
    )
    new = entry("new", state="dispatched")
    resumed = replace(resumed, entries=(*resumed.entries, new))
    assert resumed.insufficient_credits("new").pause_reason == "insufficient_credits"
    with pytest.raises(ValueError):
        replace(resumed, entries=(*resumed.entries, entry("later", state="insufficient_credits")))


@pytest.mark.parametrize("state", ["reserved", "dispatched", "unconfirmed"])
def test_in_flight_entries_cannot_be_acknowledged(state: str) -> None:
    paused = BudgetGate("project", entries=(entry(state=state),), pause_reason="estimate_overrun")
    with pytest.raises(BudgetRefused, match="resume_in_flight"):
        paused.acknowledge_pause()


def test_acknowledgement_rejects_foreign_substituted_and_malformed_history() -> None:
    failed = entry(state="insufficient_credits")
    gate = BudgetGate("project", entries=(failed,), pause_reason="insufficient_credits")
    ack = gate.acknowledge_pause().acknowledged_pause
    assert ack is not None
    malformed: Any = {}
    with pytest.raises(ValueError):
        replace(gate, pause_reason=None, acknowledged_pause=malformed)
    with pytest.raises(ValueError):
        replace(gate, project_id="foreign", entries=(), acknowledged_pause=ack)
    with pytest.raises(ValueError):
        replace(gate, entries=(), acknowledged_pause=ack)
    with pytest.raises(ValueError):
        replace(
            gate, entries=(replace(failed, reserved_autonomously=True),), acknowledged_pause=ack
        )
    malformed_entries: tuple[Any, ...] = ([], (), (failed, failed), (entry(),))
    for entries in malformed_entries:
        with pytest.raises(ValueError):
            AcknowledgedPause("project", "insufficient_credits", entries)
    malformed_reason: Any = "approval_exhausted"
    with pytest.raises(ValueError):
        AcknowledgedPause("project", malformed_reason, (failed,))
    with pytest.raises(ValueError):
        replace(
            gate,
            pause_reason=None,
            acknowledged_pause=replace(ack, entries=(replace(failed, exposure_floor=11),)),
        )


def test_acknowledgement_never_changes_exposure_or_replenishes_limits() -> None:
    failed = entry(state="insufficient_credits", exposure_floor=15)
    resumed = BudgetGate(
        "project", BudgetPolicy(15, 15, 20), (failed,), "insufficient_credits"
    ).acknowledge_pause()
    request = entry("new").request
    with pytest.raises(BudgetRefused, match="budget_exceeded"):
        resumed.reserve(request, approval=SpendApproval("admin", request.digest))
