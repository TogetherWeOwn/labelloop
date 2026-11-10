import json
from dataclasses import replace

import pytest
from qualification_fixture import qualification_context
from test_registry import model, report

from labelloop_core.drift import AcceptanceState
from labelloop_core.evaluation_lifecycle import CandidateSelection, FinalTestState
from labelloop_core.qualification import (
    QualificationContext,
    QualificationEvidence,
    QualificationPolicy,
)
from labelloop_core.qualification_wire import MAX_RECORD_BYTES, dumps, loads
from labelloop_core.registry import PromotionPolicy


def test_durable_roundtrip_is_deterministic_and_preserves_historical_assessments() -> None:
    release = model(320)
    final = report(release)
    context = qualification_context(release, final, PromotionPolicy(1, 0.9))
    restored = loads(dumps(context), QualificationContext)
    assert restored == context and dumps(restored) == dumps(context)
    assert restored.evidence.targets.fixture_only is True
    assert restored.evidence.annotation_audit.truth_basis == "constructive_synthetic"
    restored.require_qualified(release, final)
    evidence = context.evidence
    assert loads(dumps(evidence), QualificationEvidence) == evidence
    configured = QualificationPolicy(evidence.pipeline, evidence.scope, evidence.targets)
    assert loads(dumps(configured), QualificationPolicy) == configured
    with pytest.raises(ValueError, match="exact configured targets"):
        replace(configured, targets=replace(evidence.targets, min_class_support=100))
    selection = evidence.final_test.assessments[0].selection
    assert loads(dumps(selection), CandidateSelection) == selection
    adapted = evidence.final_test.record_adaptation("aggregate guided tuning", expected_revision=1)
    assert loads(dumps(adapted), FinalTestState) == adapted
    assert loads(dumps(adapted), FinalTestState).assessments == evidence.final_test.assessments
    state = AcceptanceState(release.project_id, release.recipe_id).pause(
        release,
        pause_id="drift",
        qualification_id=evidence.qualification_id,
        scope=evidence.scope,
        reason="size distribution shift",
        expected_revision=0,
    )
    assert loads(dumps(state), AcceptanceState) == state


@pytest.mark.parametrize(
    "mutation",
    [
        "schema",
        "root_type",
        "extra",
        "missing",
        "boolean_count",
        "float_count",
        "string_flag",
        "unknown_truth_basis",
        "duplicate_group",
        "missing_nested",
    ],
)
def test_unknown_schema_and_malformed_nested_evidence_are_rejected(mutation: str) -> None:
    release = model(320)
    context = qualification_context(release, report(release), PromotionPolicy(1, 0.9))
    data = json.loads(dumps(context))
    payload = data["payload"]
    if mutation == "schema":
        data["schema"] = "future.v2"
    elif mutation == "root_type":
        data["record_type"] = "arbitrary.module.Type"
    elif mutation == "extra":
        payload["passed"] = True
    elif mutation == "missing":
        del payload["current_final_test"]
    elif mutation == "boolean_count":
        payload["evidence"]["targets"]["min_class_support"] = True
    elif mutation == "float_count":
        payload["evidence"]["targets"]["min_class_support"] = 1.0
    elif mutation == "string_flag":
        payload["evidence"]["annotation_audit"]["observations"][0]["labels_correct"] = "true"
    elif mutation == "unknown_truth_basis":
        payload["evidence"]["annotation_audit"]["truth_basis"] = "independent-model-votes"
    elif mutation == "duplicate_group":
        payload["evidence"]["lineage"]["training_group_ids"] = ["same", "same"]
    else:
        del payload["evidence"]["canary_audit"]["sources"][0]["group_id"]
    with pytest.raises(ValueError):
        loads(json.dumps(data), QualificationContext)


def test_duplicate_json_keys_nonfinite_numbers_and_oversized_records_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        loads('{"schema":"one","schema":"two"}', QualificationContext)
    with pytest.raises(ValueError, match="non-finite"):
        loads('{"payload":NaN}', QualificationContext)
    with pytest.raises(ValueError, match="oversized"):
        loads(" " * (MAX_RECORD_BYTES + 1), QualificationContext)


def test_decoding_does_not_turn_stale_or_adapted_evidence_into_authority() -> None:
    release = model(320)
    final = report(release)
    context = qualification_context(release, final, PromotionPolicy(1, 0.9))
    context = replace(
        context,
        current_final_test=context.current_final_test.quarantine(
            "late cross-split relation",
            expected_revision=1,
        ),
    )
    with pytest.raises(ValueError, match="stale or mismatched"):
        loads(dumps(context), QualificationContext).require_qualified(release, final)
