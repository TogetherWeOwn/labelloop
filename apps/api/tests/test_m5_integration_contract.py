"""Synthetic integration inputs, not independently adjudicated domain-quality gold.

Exercise delivered M2 codecs/registry now. Qualification vetoes, authenticated HTTP,
PostgreSQL recovery and operational reads remain separate end-to-end acceptance cases.
"""

import hashlib
from dataclasses import dataclass, replace

import pytest
from qualification_fixture import qualification_context, synthetic_final

from labelloop_api.model_store import decode_release, decode_report, json_record
from labelloop_core.audit import (
    AuditCandidate,
    AuditObservation,
    AuditSample,
    assess_audit,
    select_audit_sample,
)
from labelloop_core.domain import AnnotationRevision, DatasetObject
from labelloop_core.evaluation import (
    DerivedPredictions,
    Detection,
    EvaluationImage,
    EvaluationReport,
    EvaluationRunner,
    GroundTruth,
)
from labelloop_core.geometry import Box, full_frame_transform
from labelloop_core.registry import ModelRelease, PromotionPolicy, RegistryState
from labelloop_core.training import InferencePolicy


def digest(identity: str) -> str:
    return hashlib.sha256(identity.encode()).hexdigest()


@dataclass(frozen=True)
class RecipeCase:
    incumbent: ModelRelease
    challenger: ModelRelease
    incumbent_report: EvaluationReport
    challenger_report: EvaluationReport
    audit_sample: AuditSample
    adverse_observations: tuple[AuditObservation, ...]


def make_release(side: int, generation: str) -> ModelRelease:
    identity = f"fixture-{side}-{generation}"
    # Distinct values expose partial rollback, omitted policy fields and cross-recipe mixing.
    policy = InferencePolicy(
        side,
        nms="external" if generation == "incumbent" else "end2end",
        iou=0.6 if side == 320 else 0.8,
        conf=0.2 if generation == "incumbent" else 0.4,
        max_det=71 if generation == "incumbent" else 113,
        agnostic_nms=generation != "incumbent",
    )
    return ModelRelease(
        release_id=identity,
        project_id="synthetic-qualification",
        recipe_id=f"full-letterbox-{side}-v1",
        weights_sha256=digest(identity + "-weights"),
        preprocessing_sha256=digest(f"recipe-{side}"),
        class_map_sha256=digest("fixture-class-map"),
        class_ids=("component", "marker"),
        policy=policy,
        ultralytics_version="8.4.204",
        torch_version="2.9.1+cpu",
        export_sha256=digest(identity + "-export"),
        export_format="onnx",
        static_input_signature=(1, 3, side, side),
        evaluation_run_ids=(identity + "-development",),
    )


def make_report(release: ModelRelease) -> EvaluationReport:
    # One occluded/truncated small-source target and a larger target, plus a negative.
    objects = (
        GroundTruth("component-1", "component", Box(4, 8, 20, 24), True, True),
        GroundTruth("marker-1", "marker", Box(80, 40, 160, 100)),
    )
    transform = full_frame_transform(200, 120, release.policy.imgsz)
    detections = []
    for obj in objects:
        mapped = transform.apply(obj.box)
        assert mapped is not None
        detections.append(Detection(obj.class_id, mapped, 0.99))
    return EvaluationRunner(release.class_ids, release.policy).evaluate(
        run_id=release.evaluation_run_ids[0],
        dataset_release_id="synthetic-development-v1",
        recipe_id=release.recipe_id,
        weights_sha256=release.weights_sha256,
        class_map_sha256=release.class_map_sha256,
        role="development",
        images=(
            EvaluationImage("fixture-positive", 200, 120, objects),
            EvaluationImage("fixture-negative", 200, 120, ()),
        ),
        derived=(
            DerivedPredictions("fixture-positive", transform, tuple(detections)),
            DerivedPredictions("fixture-negative", transform, ()),
        ),
    )


@pytest.fixture(params=[320, 640], ids=["320", "640"])
def recipe_case(request: pytest.FixtureRequest) -> RecipeCase:
    incumbent = make_release(request.param, "incumbent")
    challenger = make_release(request.param, "challenger")
    accepted = AnnotationRevision(
        1,
        (DatasetObject("wrong-identity", "marker", Box(4, 8, 20, 24)),),
        "system",
        approval_state="auto_accepted",
        review_tier="machine_verified_no_human_review",
        scope_completeness="exhaustive_for_scope",
        training_eligible=True,
    )
    sample = select_audit_sample(
        (
            AuditCandidate(
                "ordinary-confidently-wrong",
                accepted,
                "synthetic-pipeline",
                frozenset({"fixture-teacher", "fixture-reviewer"}),
            ),
            AuditCandidate(
                "ordinary-false-negative",
                replace(accepted, objects=()),
                "synthetic-pipeline",
                frozenset({"fixture-teacher", "fixture-reviewer"}),
            ),
        ),
        sample_id=f"synthetic-audit-{request.param}",
        size=2,
        seed=17,
    )
    return RecipeCase(
        incumbent,
        challenger,
        make_report(incumbent),
        make_report(challenger),
        sample,
        (
            AuditObservation(
                "ordinary-confidently-wrong", 1, "fixture-independent-auditor", True, False, True
            ),
            AuditObservation(
                "ordinary-false-negative", 1, "fixture-independent-auditor", True, True, False
            ),
        ),
    )


def test_release_and_evaluation_codecs_preserve_nondefault_promotion_inputs(
    recipe_case: RecipeCase,
) -> None:
    for release, report in (
        (recipe_case.incumbent, recipe_case.incumbent_report),
        (recipe_case.challenger, recipe_case.challenger_report),
    ):
        assert decode_release(json_record(release)) == release
        restored = decode_report(json_record(report))
        assert restored == report
        assert restored.slices
        assert len(restored.coverage) == 2
        assert all(item.covered_fraction == 1 for item in restored.coverage)
        # A fixture-only wiring target, never the project's production acceptance policy.
        PromotionPolicy(1, 0.9).qualify(decode_release(json_record(release)), restored)


def test_paired_recipe_rollback_survives_store_codec_boundary(recipe_case: RecipeCase) -> None:
    first = decode_release(json_record(recipe_case.incumbent))
    second = decode_release(json_record(recipe_case.challenger))
    before_report = decode_report(json_record(recipe_case.incumbent_report))
    after_report = synthetic_final(
        decode_report(json_record(recipe_case.challenger_report)),
        run_id=second.release_id + "-final",
        dataset_id="synthetic-final-v1",
    )
    before_report = synthetic_final(
        before_report, run_id=before_report.run_id, dataset_id="synthetic-final-v1"
    )
    second = replace(second, evaluation_run_ids=(*second.evaluation_run_ids, after_report.run_id))
    policy = PromotionPolicy(1, 0.9)
    promoted = RegistryState(first.project_id, first.recipe_id, 1, first).promote(
        second,
        after_report,
        policy,
        expected_revision=1,
        incumbent_report=before_report,
        qualification=qualification_context(second, after_report, policy),
    )
    # Rehydrate the same snapshot shape used by persisted promotion events.
    snapshot = json_record(promoted)
    reloaded = RegistryState(
        snapshot["project_id"],
        snapshot["recipe_id"],
        snapshot["revision"],
        decode_release(snapshot["champion"]),
        decode_release(snapshot["rollback"]),
    )
    restored = reloaded.roll_back(expected_revision=2)
    assert restored.champion == recipe_case.incumbent
    assert restored.rollback == second
    assert restored.revision == 3
    assert first.policy != second.policy
    assert first.weights_sha256 != second.weights_sha256
    assert first.export_sha256 != second.export_sha256


@pytest.mark.parametrize("foreign_field", ["weights_sha256", "class_map_sha256", "policy"])
def test_decoded_evidence_cannot_be_spliced_into_another_release(
    recipe_case: RecipeCase, foreign_field: str
) -> None:
    release = decode_release(json_record(recipe_case.challenger))
    report = json_record(recipe_case.challenger_report)
    other = json_record(make_release(640 if release.policy.imgsz == 320 else 320, "incumbent"))
    # Both recipes share a class map; use a different catalogue for that negative control.
    report[foreign_field] = (
        digest("foreign-catalogue") if foreign_field == "class_map_sha256" else other[foreign_field]
    )
    with pytest.raises(ValueError, match="atomic model release"):
        PromotionPolicy(1, 0.9).qualify(release, decode_report(report))


def test_adverse_and_missing_audits_are_available_for_final_gate_wiring(
    recipe_case: RecipeCase,
) -> None:
    missing = assess_audit(recipe_case.audit_sample, ())
    assert not missing.passed
    assert set(missing.pending_asset_ids) == {
        "ordinary-confidently-wrong",
        "ordinary-false-negative",
    }
    adverse = assess_audit(recipe_case.audit_sample, recipe_case.adverse_observations)
    assert not adverse.passed
    assert adverse.blocking_asset_ids == (
        "ordinary-confidently-wrong",
        "ordinary-false-negative",
    )
    assert not adverse.pending_asset_ids
    # The score is deliberately good: later qualification must veto despite this report.
    PromotionPolicy(1, 0.9).qualify(recipe_case.challenger, recipe_case.challenger_report)
