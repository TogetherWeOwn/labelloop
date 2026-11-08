"""Offline job/artifact fixtures; never provider calls or a second scheduler."""

import hashlib
import pickle
from dataclasses import fields, replace
from typing import Literal

import pytest

from labelloop_core.budget import BudgetGate, BudgetPolicy, SpendApproval, SpendRequest
from labelloop_core.domain import AnnotationRevision
from labelloop_core.iteration import (
    PER_RECIPE,
    SHARED,
    STOP_REASONS,
    DurableBudgetAuthorization,
    EvidenceRef,
    IterationManifest,
    IterationState,
    PauseClearance,
    RecipePin,
    SourcePin,
    StageKey,
    StageReceipt,
    StopReason,
    begin_stage,
    cancel,
    continue_iteration,
    finish_stage,
    invalidate,
    next_iteration,
    operation_id,
    prepare_stage,
    reconcile_retired,
    recover_stage,
    resume,
    stage_keys,
    stop,
)
from labelloop_core.iteration_ports import real_iteration_ports
from labelloop_core.review import CorrectionLoop


def ref(name: str) -> EvidenceRef:
    return EvidenceRef(name, hashlib.sha256(name.encode()).hexdigest())


def manifest() -> IterationManifest:
    return IterationManifest(
        project_id="wildlife",
        source_batch_id="batch-a",
        access_scope=ref("access"),
        sources=(SourcePin("image-a", ref("pixels"), ref("annotation")),),
        ontology=ref("ontology"),
        references=ref("references"),
        teacher=ref("teacher"),
        recipes=tuple(
            RecipePin(r, ref(r), ref("training"), ref("incumbent-" + r))
            for r in ("full320", "full640")
        ),
        split_assignment=ref("split"),
        review_pipeline=ref("review-pipeline"),
        execution_environment=ref("environment"),
        acceptance_policy=ref("acceptance"),
        evaluation_protocol=ref("evaluation"),
        evaluation_release=ref("fixture-locked-test"),
        evidence=ref("evidence"),
        policy_renewal=ref("renewal"),
        maximum_training_rounds=2,
    )


def loop() -> CorrectionLoop:
    # Terminal machine evidence is deliberately not training/gold approval.
    return CorrectionLoop(
        AnnotationRevision(1, (), "model"),
        passes=2,
        status="accepted",
        reason="machine_evidence_only",
        object_hashes=("initial", "final"),
    )


def receipt_for(
    state: IterationState,
    key: StageKey,
    *,
    outcome: Literal["complete", "failed"] = "complete",
) -> StageReceipt:
    record = state.record(key)
    assert record is not None
    decision: Literal["promote", "retain"] | None = None
    selected = None
    if key.stage == "promote_retain":
        decision = "retain"
        selected = next(p.incumbent for p in state.manifest.recipes if p.recipe_id == key.recipe_id)
    return StageReceipt(
        record.operation_id,
        record.input_sha256,
        ref("artifact-" + record.operation_id),
        ref("validator-v1"),
        outcome,
        (("image-a", loop()),) if key.stage == "validate" else (),
        decision,
        selected,
    )


def complete(state: IterationState, key: StageKey) -> IterationState:
    state = begin_stage(state, key)
    return finish_stage(state, key, receipt_for(state, key))


def shared(state: IterationState | None = None) -> IterationState:
    state = state or IterationState("iteration-1", manifest())
    for stage in SHARED:
        state = complete(state, StageKey(stage))
    return state


def trained(state: IterationState | None = None) -> IterationState:
    state = shared(state)
    for recipe in state.manifest.recipes:
        for stage in PER_RECIPE:
            state = complete(state, StageKey(stage, recipe.recipe_id))
    return complete(state, StageKey("select_next"))


def round_two() -> IterationState:
    state = trained()
    return next_iteration(
        state, "iteration-2", replace(state.manifest, evidence=ref("new-evidence"))
    )


def test_successful_selection_advances_without_inventing_a_stop_reason() -> None:
    state = trained()
    assert state.reason == "" and state.ready() == ()
    restored = pickle.loads(pickle.dumps(state))
    next_state = next_iteration(
        restored, "iteration-2", replace(state.manifest, evidence=ref("new-evidence"))
    )
    assert next_state.ready() == (StageKey("freeze"),)
    assert next_state.rounds == state.rounds
    assert next_state.correction_loops == state.correction_loops
    assert next_state.retired == state.records
    assert len(trained(next_state).rounds) == 2
    with pytest.raises(ValueError, match="new evidence"):
        next_iteration(restored, "iteration-2", state.manifest)


@pytest.mark.parametrize("selection", ["missing", "pending", "failed"])
def test_running_iteration_cannot_advance_without_successful_selection(selection: str) -> None:
    state = shared()
    if selection != "missing":
        for recipe in state.manifest.recipes:
            for stage in PER_RECIPE:
                state = complete(state, StageKey(stage, recipe.recipe_id))
        key = StageKey("select_next")
        state = begin_stage(state, key)
        if selection == "failed":
            state = finish_stage(state, key, receipt_for(state, key, outcome="failed"))
    with pytest.raises(ValueError, match="finish or pause"):
        next_iteration(state, "iteration-2", replace(state.manifest, evidence=ref("new")))


def clearance_for(state: IterationState) -> PauseClearance:
    return PauseClearance(
        state.iteration_id,
        state.generation,
        state.manifest.digest,
        state.reason,
        ref("trusted-credit-clearance"),
    )


def test_cleared_predispatch_credit_pause_continues_same_iteration() -> None:
    state = stop(IterationState("iteration-1", manifest()), "insufficient_credits", ref("pause"))
    assert resume(state, ()) == state  # Cache validation alone is never an unpause.
    continued = continue_iteration(state, (), clearance=clearance_for(state))
    assert continued == replace(state, status="running", reason="")
    assert begin_stage(continued, StageKey("freeze")).record(StageKey("freeze")) is not None


def test_credit_continuation_reuses_validated_receipts_and_preserves_bounds() -> None:
    state = shared()
    for stage in PER_RECIPE:
        state = complete(state, StageKey(stage, "full320"))
    for stage in PER_RECIPE[:3]:
        state = complete(state, StageKey(stage, "full640"))
    # A credit pause during a later recipe keeps an already consumed training round.
    state = stop(state, "insufficient_credits", ref("pause"))
    state = pickle.loads(pickle.dumps(state))
    receipts = tuple(r.receipt for r in state.records if r.receipt is not None)
    clearance = clearance_for(state)
    with pytest.raises(ValueError, match="exact artifact"):
        continue_iteration(state, receipts[:-1], clearance=clearance)
    for stale in (
        replace(clearance, iteration_id="other"),
        replace(clearance, generation=state.generation + 1),
        replace(clearance, manifest_sha256=ref("other").sha256),
        replace(clearance, pause_reason="insufficient_credits:old"),
    ):
        with pytest.raises(ValueError, match="clearance does not match"):
            continue_iteration(state, receipts, clearance=stale)
    continued = continue_iteration(state, receipts, clearance=clearance)
    assert continued.records == state.records
    assert continued.rounds == state.rounds
    assert continued.correction_loops == state.correction_loops
    assert continued.ready() == (StageKey("evaluate", "full640"),)


@pytest.mark.parametrize("outcome", ["pending", "failed"])
def test_credit_clearance_never_retries_uncertain_or_failed_intent(outcome: str) -> None:
    key = StageKey("freeze")
    state = begin_stage(IterationState("iteration-1", manifest()), key)
    if outcome == "failed":
        state = finish_stage(state, key, receipt_for(state, key, outcome="failed"))
    state = stop(state, "insufficient_credits", ref("pause"))
    receipts = tuple(r.receipt for r in state.records if r.receipt is not None)
    with pytest.raises(ValueError, match="pending or failed"):
        continue_iteration(state, receipts, clearance=clearance_for(state))
    assert recover_stage(state, key) == ("reconcile" if outcome == "pending" else "reuse")
    assert state.ready() == ()


def test_reconciled_pending_intent_can_continue_but_never_redispatch() -> None:
    key = StageKey("freeze")
    state = begin_stage(IterationState("iteration-1", manifest()), key)
    state = stop(state, "insufficient_credits", ref("pause"))
    receipt = receipt_for(state, key)
    settled = finish_stage(state, key, receipt)
    continued = continue_iteration(settled, (receipt,), clearance=clearance_for(settled))
    assert begin_stage(continued, key) == continued
    assert recover_stage(continued, key) == "reuse"
    assert continued.ready() == (StageKey("propose_discover"),)


def test_fresh_iteration_can_continue_after_reconciled_historical_failure() -> None:
    key = StageKey("freeze")
    state = begin_stage(IterationState("iteration-1", manifest()), key)
    state = finish_stage(state, key, receipt_for(state, key, outcome="failed"))
    state = stop(state, "insufficient_evidence", ref("failure"))
    state = next_iteration(state, "iteration-2", replace(state.manifest, evidence=ref("new")))
    state = stop(state, "insufficient_credits", ref("pause"))
    continued = continue_iteration(state, (), clearance=clearance_for(state))
    assert continued.retired == state.retired and continued.rounds == state.rounds
    assert continued.ready() == (key,)
    assert begin_stage(continued, key).record(key) != state.retired[0]


def test_credit_clearance_is_not_paid_stage_authorization() -> None:
    state = IterationState("iteration-1", replace(manifest(), paid_review=True))
    for stage in SHARED[:2]:
        state = complete(state, StageKey(stage))
    state = stop(state, "insufficient_credits", ref("pause"))
    receipts = tuple(r.receipt for r in state.records if r.receipt is not None)
    continued = continue_iteration(state, receipts, clearance=clearance_for(state))
    key = StageKey("review")
    with pytest.raises(ValueError, match="durable budget"):
        begin_stage(continued, key)
    assert begin_stage(continued, key, budget=budget_for(continued, key)).record(key) is not None


@pytest.mark.parametrize("reason", sorted(STOP_REASONS - {"insufficient_credits"}))
def test_later_credit_pause_cannot_erase_prior_stop_gate(reason: StopReason) -> None:
    state = stop(IterationState("iteration-1", manifest()), reason, ref("original-gate"))
    state = stop(state, "insufficient_credits", ref("later-credit-pause"))
    state = pickle.loads(pickle.dumps(state))
    with pytest.raises(ValueError, match="prior stop gates"):
        continue_iteration(state, (), clearance=clearance_for(state))


def test_credit_clearance_cannot_erase_invalidation_or_other_stop_gates() -> None:
    original = IterationState("iteration-1", manifest())
    changed = invalidate(original, replace(original.manifest, teacher=ref("changed-teacher")))
    changed = stop(changed, "insufficient_credits", ref("pause"))
    with pytest.raises(ValueError, match="invalidated"):
        continue_iteration(changed, (), clearance=clearance_for(changed))
    for state in (
        original,
        cancel(original),
        *(
            stop(original, reason, ref("gate"))
            for reason in STOP_REASONS
            if reason != "insufficient_credits"
        ),
    ):
        with pytest.raises(ValueError, match="credit pause"):
            continue_iteration(state, (), clearance=clearance_for(state))


@pytest.mark.parametrize("disposition", ["stopped", "cancelled", "failed"])
def test_iteration_ids_survive_zero_training_history_and_serialization(disposition: str) -> None:
    state = IterationState("iteration-a", manifest())
    for stage in SHARED[:3]:
        state = complete(state, StageKey(stage))
    if disposition == "failed":
        key = StageKey("validate")
        state = begin_stage(state, key)
        state = finish_stage(
            state, key, replace(receipt_for(state, key, outcome="failed"), correction_loops=())
        )
    state = cancel(state) if disposition == "cancelled" else stop(state, "plateau", ref("stop"))
    second_manifest = replace(state.manifest, evidence=ref("second-evidence"))
    state = next_iteration(state, "iteration-b", second_manifest)
    state = stop(state, "plateau", ref("stop"))
    state = pickle.loads(pickle.dumps(state))
    assert state.rounds == ()
    third_manifest = replace(state.manifest, evidence=ref("third-evidence"))
    with pytest.raises(ValueError, match="fresh ID"):
        next_iteration(state, "iteration-a", third_manifest)
    fresh = next_iteration(state, "iteration-c", third_manifest)
    assert fresh.admitted_iteration_ids == ("iteration-a", "iteration-b", "iteration-c")
    assert fresh.ready() == (StageKey("freeze"),)


def test_iteration_identity_history_must_be_complete_unique_and_immutable() -> None:
    state = IterationState("iteration-1", manifest())
    assert state.admitted_iteration_ids == ("iteration-1",)
    for invalid in (("other",), ("iteration-1", "iteration-1")):
        with pytest.raises(ValueError, match="identity history"):
            replace(state, admitted_iteration_ids=invalid)
    with pytest.raises(ValueError, match="immutable tuples"):
        replace(state, admitted_iteration_ids=["iteration-1"])  # type: ignore[arg-type]


def test_batch_switch_cannot_reuse_old_renewal_to_raise_exhausted_ceiling() -> None:
    state = stop(trained(round_two()), "plateau", ref("plateau"))
    batch_a = state.manifest
    batch_b = replace(batch_a, source_batch_id="batch-b")
    state = next_iteration(state, "batch-b-1", batch_b)
    state = stop(state, "no_informative_data", ref("batch-b-empty"))
    increased = replace(batch_a, evidence=ref("third"), maximum_training_rounds=3)
    with pytest.raises(ValueError, match="ceiling requires"):
        next_iteration(state, "batch-a-3", increased)
    renewed = replace(increased, policy_renewal=ref("fresh-renewal"))
    state = next_iteration(state, "batch-a-3", renewed)
    assert len(state.rounds) == 2
    # A previously used renewal cannot be recycled after another batch switch.
    state = stop(state, "plateau", ref("pause"))
    state = next_iteration(state, "batch-b-2", batch_b)
    state = stop(state, "plateau", ref("pause"))
    with pytest.raises(ValueError, match="ceiling requires"):
        next_iteration(state, "batch-a-4", replace(increased, maximum_training_rounds=4))


def test_retired_validation_readback_preserves_consumed_correction_history() -> None:
    state = IterationState("iteration-1", manifest())
    for stage in SHARED[:3]:
        state = complete(state, StageKey(stage))
    key = StageKey("validate")
    state = begin_stage(state, key)
    receipt = receipt_for(state, key)
    changed = replace(state.manifest, evidence=ref("new-evidence"))
    state = invalidate(state, changed)
    assert state.correction_loops == ()
    state = reconcile_retired(state, receipt)
    assert dict(state.correction_loops)["image-a"].passes == 2
    assert reconcile_retired(state, receipt) == state
    state = next_iteration(state, "iteration-2", changed)
    for stage in SHARED[:3]:
        state = complete(state, StageKey(stage))
    state = begin_stage(state, key)
    with pytest.raises(ValueError, match="cannot reset"):
        finish_stage(
            state,
            key,
            replace(
                receipt_for(state, key),
                correction_loops=(("image-a", replace(loop(), passes=0)),),
            ),
        )


def test_invalidated_restart_eligibility_survives_later_stop_reason() -> None:
    state = trained()
    old_evidence = state.manifest.round_evidence
    changed = replace(state.manifest, evidence=ref("genuinely-new-evidence"))
    state = invalidate(state, changed)
    state = stop(state, "insufficient_credits", ref("credits"))
    assert changed.round_evidence != old_evidence
    restarted = next_iteration(state, "iteration-2", changed)
    assert restarted.ready() == (StageKey("freeze"),)
    # A round cannot be justified merely by invalidating then restoring its original inputs.
    original = manifest()
    state = invalidate(state, original)
    with pytest.raises(ValueError, match="new evidence"):
        next_iteration(state, "iteration-2", original)


def test_tightened_correction_ceiling_refuses_impossible_or_uncertain_history() -> None:
    state = stop(trained(), "plateau", ref("pause"))
    tightened = replace(state.manifest, evidence=ref("new"), maximum_correction_passes=1)
    with pytest.raises(ValueError, match="consumed correction"):
        next_iteration(state, "iteration-2", tightened)
    with pytest.raises(ValueError, match="consumed correction"):
        invalidate(state, tightened)
    pending = IterationState("pending", manifest())
    for stage in SHARED[:3]:
        pending = complete(pending, StageKey(stage))
    pending = begin_stage(pending, StageKey("validate"))
    with pytest.raises(ValueError, match="pending validation"):
        invalidate(pending, tightened)


def test_retired_validation_uses_intents_original_sources_and_correction_policy() -> None:
    state = IterationState("iteration-1", replace(manifest(), maximum_correction_passes=1))
    for stage in SHARED[:3]:
        state = complete(state, StageKey(stage))
    key = StageKey("validate")
    state = begin_stage(state, key)
    receipt = replace(
        receipt_for(state, key),
        correction_loops=(
            (
                "image-a",
                replace(
                    loop(),
                    maximum_passes=1,
                    passes=1,
                ),
            ),
        ),
    )
    changed = replace(manifest(), sources=(SourcePin("image-b", ref("b"), ref("label-b")),))
    state = invalidate(state, changed)
    with pytest.raises(ValueError, match="outside source batch"):
        reconcile_retired(state, replace(receipt, correction_loops=(("image-b", loop()),)))
    with pytest.raises(ValueError, match="policy mismatch"):
        reconcile_retired(state, replace(receipt, correction_loops=(("image-a", loop()),)))
    state = reconcile_retired(state, receipt)
    assert dict(state.correction_loops)["image-a"].passes == 1
    assert state.manifest.sources[0].asset_id == "image-b"


@pytest.mark.parametrize("failed_predecessor", [False, True])
def test_failed_registry_readback_settles_intent_without_selecting_model(
    failed_predecessor: bool,
) -> None:
    state = shared()
    if failed_predecessor:
        key = StageKey("compile", "full320")
        state = begin_stage(state, key)
        state = finish_stage(state, key, receipt_for(state, key, outcome="failed"))
    else:
        for stage in PER_RECIPE[:-1]:
            state = complete(state, StageKey(stage, "full320"))
    key = StageKey("promote_retain", "full320")
    state = begin_stage(state, key)
    receipt = replace(receipt_for(state, key, outcome="failed"), decision=None, selected_model=None)
    for invalid in (
        replace(receipt, decision="retain"),
        replace(receipt, selected_model=ref("uncommitted-model")),
    ):
        with pytest.raises(ValueError, match="failed registry"):
            finish_stage(state, key, invalid)
    state = finish_stage(state, key, receipt)
    assert finish_stage(state, key, receipt) == state
    assert recover_stage(state, key) == "reuse"
    state = resume(state, tuple(r.receipt for r in state.records if r.receipt is not None))
    for stage in PER_RECIPE:
        state = complete(state, StageKey(stage, "full640"))
    assert StageKey("select_next") not in state.ready()
    with pytest.raises(ValueError, match="not ready"):
        begin_stage(state, StageKey("select_next"))
    state = stop(state, "insufficient_evidence", ref("registry-failure"))
    restarted = next_iteration(
        state, "iteration-2", replace(state.manifest, evidence=ref("new-evidence"))
    )
    assert restarted.ready() == (StageKey("freeze"),)
    assert restarted.rounds == state.rounds
    assert receipt in tuple(r.receipt for r in restarted.retired)


@pytest.mark.parametrize("switch_batch", [False, True])
def test_new_iteration_refuses_previously_consumed_round_evidence(switch_batch: bool) -> None:
    state = stop(trained(), "plateau", ref("pause"))
    original = state.manifest
    changed = replace(original, evidence=ref("new-evidence"))
    if switch_batch:
        changed = replace(changed, source_batch_id="batch-b")
    state = next_iteration(state, "iteration-2", changed)
    state = stop(trained(state), "plateau", ref("pause"))
    state = pickle.loads(pickle.dumps(state))
    for reused in (original, replace(original, paid_review=True)):
        with pytest.raises(ValueError, match="new evidence"):
            next_iteration(state, "iteration-3", reused)
    renewed = replace(original, policy_renewal=ref("fresh-renewal"))
    assert next_iteration(state, "iteration-3", renewed).ready() == (StageKey("freeze"),)


def test_exhausted_paid_training_preflight_never_dispatches_budget() -> None:
    state = stop(trained(round_two()), "plateau", ref("pause"))
    state = next_iteration(
        state,
        "iteration-3",
        replace(state.manifest, evidence=ref("third"), backend="ultralytics_platform"),
    )
    state = shared(state)
    state = complete(state, StageKey("compile", "full320"))
    sync = StageKey("sync", "full320")
    state = begin_stage(state, sync, budget=budget_for(state, sync))
    state = finish_stage(state, sync, receipt_for(state, sync))
    train = StageKey("train", "full320")
    ledger = BudgetGate(state.manifest.project_id, BudgetPolicy(100, 100, 100))
    preview = prepare_stage(state, train)
    # The trusted transaction must not reserve/dispatch when no new intent is prepared.
    if preview.record(train) is not None:
        ledger = budget_for(state, train).ledger
    assert ledger.entries == ()
    assert preview.status == "paused" and preview.reason == "round_limit"
    assert preview.rounds == state.rounds and preview.records == state.records
    assert begin_stage(state, train) == preview


def test_paid_preflight_is_not_dispatch_authorization() -> None:
    state = IterationState("iteration-1", replace(manifest(), paid_review=True))
    for stage in SHARED[:2]:
        state = complete(state, StageKey(stage))
    key = StageKey("review")
    preview = prepare_stage(state, key)
    assert preview.record(key) is not None
    with pytest.raises(ValueError, match="durable budget"):
        begin_stage(state, key)
    committed = begin_stage(state, key, budget=budget_for(state, key))
    assert committed == preview
    assert prepare_stage(committed, key) == committed
    assert begin_stage(committed, key) == committed


def test_manifest_pins_complete_inputs_and_order() -> None:
    m = manifest()
    assert len(stage_keys(m)) == 17
    assert IterationState("iteration-1", m).ready() == (StageKey("freeze"),)
    assert m.digest == manifest().digest
    # Every evidence field is part of the key, including provider/schema/environment/access.
    for field in fields(m):
        current = getattr(m, field.name)
        if isinstance(current, EvidenceRef):
            changed = replace(m, **{field.name: ref("changed-" + field.name)})  # type: ignore[arg-type]
            assert changed.digest != m.digest
    with pytest.raises(ValueError, match="unique and sorted"):
        replace(m, recipes=tuple(reversed(m.recipes)))
    with pytest.raises(ValueError, match="unique and sorted"):
        replace(m, sources=(*m.sources, m.sources[0]))
    with pytest.raises(ValueError, match="correction passes"):
        replace(m, maximum_correction_passes=3)
    with pytest.raises(ValueError, match="positive ceiling"):
        replace(m, maximum_training_rounds=True)
    with pytest.raises(ValueError, match="unknown iteration stage"):
        StageKey("arbitrary")  # type: ignore[arg-type]


def test_two_non_game_projects_use_identical_controller() -> None:
    a = trained()
    b = trained(IterationState("industrial-1", replace(manifest(), project_id="industrial-parts")))
    assert len(a.records) == len(b.records) == 17
    assert len(a.rounds) == len(b.rounds) == 1
    assert a.rounds[0].iteration_id == "iteration-1"
    assert b.manifest.project_id == "industrial-parts"


def test_restart_reuses_exact_artifact_ids_after_validation() -> None:
    state = trained()
    restored = pickle.loads(pickle.dumps(state))  # Only our own fixture, never untrusted pickle.
    receipts = tuple(r.receipt for r in restored.records if r.receipt is not None)
    assert resume(restored, receipts) == state
    assert restored.ready() == ()
    for record in restored.records:
        assert recover_stage(restored, record.key) == "reuse"
        assert begin_stage(restored, record.key) == restored
        assert record.receipt is not None
        assert finish_stage(restored, record.key, record.receipt) == restored
    with pytest.raises(ValueError, match="exact artifact"):
        resume(restored, receipts[:-1])
    with pytest.raises(ValueError, match="exact artifact"):
        resume(restored, (replace(receipts[0], artifact=ref("tampered")), *receipts[1:]))


@pytest.mark.parametrize(
    "change",
    ["source", "policy", "reference", "teacher", "recipe", "protocol", "evaluation_release"],
)
def test_changed_inputs_retire_all_descendants_not_history(change: str) -> None:
    state = trained()
    m = state.manifest
    changes: dict[str, object] = {
        "source": replace(m, sources=(replace(m.sources[0], annotation=ref("bad-label-fixed")),)),
        "policy": replace(m, acceptance_policy=ref("new-policy")),
        "protocol": replace(m, evaluation_protocol=ref("changed-after-results-protocol")),
        "evaluation_release": replace(m, evaluation_release=ref("fresh-locked-test")),
        "reference": replace(m, references=ref("new-references")),
        "teacher": replace(m, teacher=ref("new-teacher")),
        "recipe": replace(
            m, recipes=(replace(m.recipes[0], definition=ref("new-recipe")), m.recipes[1])
        ),
    }
    changed = changes[change]
    assert isinstance(changed, IterationManifest)
    invalid = invalidate(state, changed)
    assert invalid.records == () and invalid.retired == state.records
    assert invalid.rounds == state.rounds and invalid.correction_loops == state.correction_loops
    assert invalid.status == "paused" and invalid.ready() == ()
    old = state.records[-1]
    assert old.receipt is not None
    with pytest.raises(ValueError, match="no committed"):
        finish_stage(invalid, old.key, old.receipt)
    assert invalidate(state, m) == state


def test_uncertain_external_intent_reconciles_never_dispatches_twice() -> None:
    state = shared()
    state = complete(state, StageKey("compile", "full320"))
    key = StageKey("sync", "full320")
    state = begin_stage(state, key)
    # Simulated provider mutation applied, response lost before the job stores a receipt.
    committed = state.record(key)
    assert committed is not None
    restored = pickle.loads(pickle.dumps(state))
    for _ in range(3):
        repeated = begin_stage(restored, key)
        assert repeated == restored
        assert repeated.record(key) == committed
        restored = repeated
        assert recover_stage(restored, key) == "reconcile"
    restored = finish_stage(restored, key, receipt_for(restored, key))
    assert recover_stage(restored, key) == "reuse"
    completed = restored.record(key)
    assert completed is not None and completed.operation_id == committed.operation_id
    assert operation_id(restored, key) == committed.operation_id


def test_cancel_retains_pending_intent_until_readback() -> None:
    state = begin_stage(IterationState("iteration-1", manifest()), StageKey("freeze"))
    cancelled = cancel(state)
    assert cancel(cancelled) == cancelled
    assert cancelled.ready() == ()
    with pytest.raises(ValueError, match="not running"):
        begin_stage(cancelled, StageKey("freeze"))
    with pytest.raises(ValueError, match="reconciliation"):
        next_iteration(cancelled, "iteration-2", replace(manifest(), evidence=ref("new")))
    settled = finish_stage(
        cancelled, StageKey("freeze"), receipt_for(cancelled, StageKey("freeze"))
    )
    assert settled.status == "cancelled"
    assert (
        next_iteration(settled, "iteration-2", replace(manifest(), evidence=ref("new"))).rounds
        == ()
    )


def test_invalidated_uncertain_intent_blocks_new_round_until_reconciled() -> None:
    key = StageKey("freeze")
    state = begin_stage(IterationState("iteration-1", manifest()), key)
    receipt = receipt_for(state, key, outcome="failed")  # Confirmed absence, not a timeout.
    changed = replace(state.manifest, evidence=ref("new"))
    invalid = invalidate(state, changed)
    with pytest.raises(ValueError, match="reconciliation"):
        next_iteration(invalid, "iteration-2", changed)
    invalid = reconcile_retired(invalid, receipt)
    assert reconcile_retired(invalid, receipt) == invalid
    assert next_iteration(invalid, "iteration-2", changed).ready() == (key,)
    with pytest.raises(ValueError, match="conflicting reconciliation"):
        reconcile_retired(invalid, replace(receipt, artifact=ref("other")))


def test_failure_in_one_recipe_only_allows_explicit_retain() -> None:
    state = shared()
    key = StageKey("compile", "full320")
    state = begin_stage(state, key)
    state = finish_stage(state, key, receipt_for(state, key, outcome="failed"))
    assert StageKey("train", "full320") not in state.ready()
    assert StageKey("promote_retain", "full320") in state.ready()
    assert StageKey("compile", "full640") in state.ready()
    decision = StageKey("promote_retain", "full320")
    state = begin_stage(state, decision)
    receipt = receipt_for(state, decision)
    with pytest.raises(ValueError, match="can only retain"):
        finish_stage(
            state, decision, replace(receipt, decision="promote", selected_model=ref("new"))
        )
    with pytest.raises(ValueError, match="pinned incumbent"):
        finish_stage(
            state, decision, replace(receipt, selected_model=ref("wrong-recipe-incumbent"))
        )
    state = finish_stage(state, decision, receipt)
    assert state.record(StageKey("promote_retain", "full640")) is None
    assert StageKey("select_next") not in state.ready()
    for stage in PER_RECIPE:
        state = complete(state, StageKey(stage, "full640"))
    assert state.ready() == (StageKey("select_next"),)


def test_recipe_decisions_cannot_implicitly_promote_or_cross_identity() -> None:
    state = shared()
    for stage in PER_RECIPE[:-1]:
        state = complete(state, StageKey(stage, "full320"))
    key = StageKey("promote_retain", "full320")
    state = begin_stage(state, key)
    receipt = receipt_for(state, key)
    with pytest.raises(ValueError, match="explicit promote or retain"):
        finish_stage(state, key, replace(receipt, decision=None))
    with pytest.raises(ValueError, match="separately qualified"):
        finish_stage(state, key, replace(receipt, decision="promote"))
    good = replace(receipt, decision="promote", selected_model=ref("qualified-320"))
    state = finish_stage(state, key, good)
    assert state.record(StageKey("promote_retain", "full640")) is None


def test_round_ceiling_survives_resume_and_two_recipes_count_once() -> None:
    state = trained(round_two())
    assert len(state.rounds) == 2
    state = resume(state, tuple(r.receipt for r in state.records if r.receipt is not None))
    state = stop(state, "plateau", ref("plateau"))
    third = next_iteration(state, "iteration-3", replace(state.manifest, evidence=ref("third")))
    third = shared(third)
    third = complete(third, StageKey("compile", "full320"))
    third = complete(third, StageKey("sync", "full320"))
    bounded = begin_stage(third, StageKey("train", "full320"))
    assert bounded.status == "paused" and bounded.reason == "round_limit"
    assert len(bounded.rounds) == 2
    assert bounded.record(StageKey("train", "full320")) is None


def aliased_manifest(m: IterationManifest, alias: str) -> IterationManifest:
    source = m.sources[0]
    recipe = m.recipes[0]
    if alias == "evidence":
        return replace(m, evidence=replace(m.evidence, artifact_id="evidence-alias"))
    if alias == "pixels":
        source = replace(source, pixels=replace(source.pixels, artifact_id="pixels-alias"))
    elif alias == "annotation":
        source = replace(
            source, annotation=replace(source.annotation, artifact_id="annotation-alias")
        )
    elif alias in {"asset", "duplicate_source"}:
        source = replace(source, asset_id="image-alias")
    elif alias == "definition":
        recipe = replace(recipe, definition=replace(recipe.definition, artifact_id="recipe-alias"))
    elif alias == "training_profile":
        recipe = replace(
            recipe,
            training_profile=replace(recipe.training_profile, artifact_id="training-alias"),
        )
    elif alias in {"recipe", "duplicate_recipe"}:
        recipe = replace(recipe, recipe_id="full-alias")
    else:
        raise AssertionError("unknown fixture alias")
    return replace(
        m,
        sources=tuple(
            sorted(
                (*m.sources, source) if alias == "duplicate_source" else (source, *m.sources[1:]),
                key=lambda p: p.asset_id,
            )
        ),
        recipes=tuple(
            sorted(
                (*m.recipes, recipe) if alias == "duplicate_recipe" else (recipe, *m.recipes[1:]),
                key=lambda p: p.recipe_id,
            )
        ),
    )


@pytest.mark.parametrize(
    "alias",
    [
        "evidence",
        "pixels",
        "annotation",
        "asset",
        "definition",
        "training_profile",
        "recipe",
        "duplicate_source",
        "duplicate_recipe",
    ],
)
@pytest.mark.parametrize("history", ["stopped", "consumed", "switched_batch"])
def test_same_content_aliases_cannot_admit_a_new_iteration(alias: str, history: str) -> None:
    original = manifest()
    if history == "stopped":
        state = stop(IterationState("iteration-1", original), "no_informative_data", ref("stop"))
    else:
        state = stop(trained(), "plateau", ref("stop"))
        changed = replace(original, evidence=ref("new-evidence"))
        if history == "switched_batch":
            changed = replace(changed, source_batch_id="batch-b")
        state = stop(next_iteration(state, "iteration-2", changed), "plateau", ref("stop"))
    state = pickle.loads(pickle.dumps(state))
    aliased = aliased_manifest(original, alias)
    assert aliased.digest != original.digest  # Cache and lineage stay identity-sensitive.
    key = StageKey("freeze")
    initial = IterationState("lineage", original)
    assert initial.input_digest(key) != replace(initial, manifest=aliased).input_digest(key)
    assert aliased.round_evidence == original.round_evidence
    with pytest.raises(ValueError, match="new evidence"):
        next_iteration(state, "fresh-iteration", aliased)
    invalidated = invalidate(state, aliased)
    assert invalidated.records == () and invalidated.rounds == state.rounds
    assert invalidated.correction_loops == state.correction_loops
    assert invalidated.started_round_evidence == state.started_round_evidence
    with pytest.raises(ValueError, match="new evidence"):
        next_iteration(invalidated, "fresh-iteration", aliased)


@pytest.mark.parametrize(
    "changed", ["pixels", "annotation", "evidence", "definition", "training_profile", "renewal"]
)
def test_changed_content_or_trusted_renewal_admits_a_round(changed: str) -> None:
    state = stop(trained(), "plateau", ref("stop"))
    m = state.manifest
    source = m.sources[0]
    recipe = m.recipes[0]
    if changed == "pixels":
        source = replace(source, pixels=replace(source.pixels, sha256=ref("new").sha256))
    elif changed == "annotation":
        source = replace(source, annotation=replace(source.annotation, sha256=ref("new").sha256))
    elif changed == "definition":
        recipe = replace(recipe, definition=replace(recipe.definition, sha256=ref("new").sha256))
    elif changed == "training_profile":
        recipe = replace(
            recipe, training_profile=replace(recipe.training_profile, sha256=ref("new").sha256)
        )
    elif changed == "evidence":
        m = replace(m, evidence=replace(m.evidence, sha256=ref("new").sha256))
    else:
        m = replace(m, policy_renewal=ref("trusted-fresh-renewal"))
    m = replace(m, sources=(source,), recipes=(recipe, *m.recipes[1:]))
    assert m.round_evidence != state.manifest.round_evidence
    advanced = next_iteration(pickle.loads(pickle.dumps(state)), "iteration-2", m)
    assert advanced.ready() == (StageKey("freeze"),)
    assert advanced.rounds == state.rounds
    assert advanced.correction_loops == state.correction_loops
    assert len(trained(advanced).rounds) == 2


def test_new_round_requires_evidence_recipe_or_explicit_renewal() -> None:
    state = stop(trained(), "plateau", ref("plateau"))
    with pytest.raises(ValueError, match="new evidence"):
        next_iteration(state, "iteration-2", state.manifest)
    # An unrelated teacher/policy/config bump is not an excuse for more training.
    with pytest.raises(ValueError, match="new evidence"):
        next_iteration(state, "iteration-2", replace(state.manifest, teacher=ref("teacher-2")))
    with pytest.raises(ValueError, match="ceiling requires"):
        next_iteration(
            state,
            "iteration-2",
            replace(
                state.manifest,
                maximum_training_rounds=3,
                evidence=ref("new"),
            ),
        )
    renewal = replace(state.manifest, maximum_training_rounds=3, policy_renewal=ref("renewed"))
    assert next_iteration(state, "iteration-2", renewal).rounds == state.rounds
    with pytest.raises(ValueError, match="fresh ID"):
        next_iteration(state, "iteration-1", renewal)
    with pytest.raises(ValueError, match="across projects"):
        invalidate(state, replace(state.manifest, project_id="other"))


@pytest.mark.parametrize("reason", sorted(STOP_REASONS))
def test_stop_pause_gates_are_evidence_bound_and_block_new_mutation(reason: StopReason) -> None:
    state = stop(IterationState("iteration-1", manifest()), reason, ref("scoped-evidence"))
    assert state.status in {"paused", "stopped"}
    assert ref("scoped-evidence").sha256 in state.reason
    assert state.ready() == ()
    with pytest.raises(ValueError, match="not running"):
        begin_stage(state, StageKey("freeze"))
    assert not hasattr(state, "independently_verified")


def budget_for(state: IterationState, key: StageKey) -> DurableBudgetAuthorization:
    request = SpendRequest(
        operation_id(state, key),
        state.manifest.project_id,
        state.iteration_id,
        "2026-11",
        10,
        "prices-v1",
        state.input_digest(key),
    )
    ledger = (
        BudgetGate(state.manifest.project_id, BudgetPolicy(100, 100, 100))
        .reserve(
            request,
            approval=SpendApproval("authenticated-admin", request.digest),
        )
        .dispatch(request.operation_id)
    )
    return DurableBudgetAuthorization(ref("durable-commit"), ledger, request.operation_id)


@pytest.mark.parametrize("stage", ["review", "sync", "train"])
def test_paid_ports_fail_closed_without_matching_durable_budget(stage: str) -> None:
    m = replace(manifest(), paid_review=True, backend="ultralytics_platform")
    state = IterationState("iteration-1", m)
    key = StageKey("review")
    for s in ("freeze", "propose_discover"):
        state = complete(state, StageKey(s))
    if stage != "review":
        state = begin_stage(state, key, budget=budget_for(state, key))
        state = finish_stage(state, key, receipt_for(state, key))
        for s in SHARED[3:]:
            state = complete(state, StageKey(s))
        state = complete(state, StageKey("compile", "full320"))
        key = StageKey("sync", "full320")
        if stage == "train":
            state = begin_stage(state, key, budget=budget_for(state, key))
            state = finish_stage(state, key, receipt_for(state, key))
            key = StageKey("train", "full320")
    with pytest.raises(ValueError, match="durable budget"):
        begin_stage(state, key)
    authorization = budget_for(state, key)
    assert begin_stage(state, key, budget=authorization).record(key) is not None
    entry = authorization.ledger.entries[0]
    for changed in (
        replace(authorization, operation_id="wrong"),
        replace(
            authorization, ledger=replace(authorization.ledger, pause_reason="estimate_overrun")
        ),
        replace(
            authorization,
            ledger=replace(authorization.ledger, entries=(replace(entry, state="reserved"),)),
        ),
        replace(
            authorization,
            ledger=replace(
                authorization.ledger,
                entries=(
                    replace(
                        entry,
                        request=replace(entry.request, configuration_sha256=ref("stale").sha256),
                        approval=None,
                        reserved_autonomously=True,
                    ),
                ),
            ),
        ),
    ):
        with pytest.raises(ValueError, match="budget authorization"):
            begin_stage(state, key, budget=changed)


def test_correction_history_cannot_reset_on_restart_or_next_round() -> None:
    state = round_two()
    for stage in SHARED[:3]:
        state = complete(state, StageKey(stage))
    key = StageKey("validate")
    state = begin_stage(state, key)
    receipt = receipt_for(state, key)
    with pytest.raises(ValueError, match="cannot reset"):
        finish_stage(
            state, key, replace(receipt, correction_loops=(("image-a", replace(loop(), passes=0)),))
        )
    with pytest.raises(ValueError, match="every source"):
        finish_stage(state, key, replace(receipt, correction_loops=()))
    running = replace(loop(), status="running")
    with pytest.raises(ValueError, match="settle bounded"):
        finish_stage(state, key, replace(receipt, correction_loops=(("image-a", running),)))
    unresolved = replace(loop(), status="unresolved", reason="oscillating_corrections")
    state = finish_stage(state, key, replace(receipt, correction_loops=(("image-a", unresolved),)))
    assert state.ready() == (StageKey("adjudicate_audit"),)
    assert dict(state.correction_loops)["image-a"].passes == 2
    assert not dict(state.correction_loops)["image-a"].current.gold_eligible


def test_stale_wrong_duplicate_and_conflicting_receipts_are_rejected() -> None:
    key = StageKey("freeze")
    state = begin_stage(IterationState("iteration-1", manifest()), key)
    receipt = receipt_for(state, key)
    with pytest.raises(ValueError, match="committed intent"):
        finish_stage(state, key, replace(receipt, input_sha256=ref("wrong").sha256))
    state = finish_stage(state, key, receipt)
    with pytest.raises(ValueError, match="conflicting stage"):
        finish_stage(state, key, replace(receipt, artifact=ref("wrong")))
    with pytest.raises(ValueError, match="operation identities cannot repeat"):
        replace(state, records=(*state.records, state.records[0]))
    with pytest.raises(RuntimeError, match="disabled"):
        real_iteration_ports()
