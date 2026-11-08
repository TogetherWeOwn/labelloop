from dataclasses import replace

import pytest
from qualification_fixture import qualification_context

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


def model(side: int, release_id: str = "model-1") -> ModelRelease:
    return ModelRelease(
        release_id=release_id,
        project_id="project-1",
        recipe_id=f"full-letterbox-{side}-v1",
        weights_sha256="a" * 64,
        preprocessing_sha256="b" * 64,
        class_map_sha256="c" * 64,
        class_ids=("object",),
        policy=InferencePolicy(side),
        ultralytics_version="8.4.204",
        torch_version="2.9.1+cpu",
        export_sha256="d" * 64,
        export_format="onnx",
        static_input_signature=(1, 3, side, side),
        evaluation_run_ids=(f"evaluation-{release_id}", f"development-{release_id}"),
    )


def report(
    release: ModelRelease, *, missed: bool = False, truth_classes: tuple[str, ...] = ("object",)
) -> EvaluationReport:
    transform = full_frame_transform(100, 100, release.policy.imgsz)
    box = Box(10, 10, 30, 30)
    mapped = transform.apply(box)
    assert mapped is not None
    result = EvaluationRunner(release.class_ids, release.policy).evaluate(
        run_id=release.evaluation_run_ids[0],
        dataset_release_id="eval-data-1",
        recipe_id=release.recipe_id,
        weights_sha256=release.weights_sha256,
        class_map_sha256=release.class_map_sha256,
        role="locked_final_test",
        images=(
            EvaluationImage(
                "source",
                100,
                100,
                tuple(GroundTruth(class_id, class_id, box) for class_id in truth_classes),
            ),
        ),
        derived=(
            DerivedPredictions(
                "source",
                transform,
                ()
                if missed
                else tuple(Detection(class_id, mapped, 0.9) for class_id in truth_classes),
            ),
        ),
    )
    return replace(result, slices=result.slices | {"fixture_critical": result.overall})


def test_recipe_promotion_and_rollback_preserve_the_whole_release_independently() -> None:
    policy = PromotionPolicy(1, 0.9)
    small, large = model(320), model(640)
    state320 = RegistryState("project-1", small.recipe_id)
    state640 = RegistryState("project-1", large.recipe_id)
    promoted320 = state320.promote(
        small,
        report(small),
        policy,
        expected_revision=0,
        qualification=qualification_context(small, report(small), policy),
    )
    promoted640 = state640.promote(
        large,
        report(large),
        policy,
        expected_revision=0,
        qualification=qualification_context(large, report(large), policy),
    )
    next320 = model(320, "model-2")
    replaced320 = promoted320.promote(
        next320,
        report(next320),
        policy,
        expected_revision=1,
        incumbent_report=report(small),
        qualification=qualification_context(next320, report(next320), policy),
    )
    assert replaced320.champion == next320
    assert replaced320.rollback == small
    restored = replaced320.roll_back(expected_revision=2)
    assert restored.champion == small
    assert restored.rollback == next320
    assert restored.revision == 3
    assert promoted640.champion == large
    assert promoted640.rollback is None
    assert state320.champion is None
    assert restored.champion.static_input_signature == (1, 3, 320, 320)
    assert restored.champion.policy == small.policy


def test_stale_revision_and_cross_recipe_or_project_promotions_are_rejected() -> None:
    release = model(320)
    state = RegistryState("project-1", release.recipe_id)
    with pytest.raises(ValueError, match="stale"):
        state.promote(release, report(release), PromotionPolicy(1, 0.9), expected_revision=1)
    with pytest.raises(ValueError, match="recipe boundaries"):
        state.promote(model(640), report(model(640)), PromotionPolicy(1, 0.9), expected_revision=0)
    with pytest.raises(ValueError, match="recipe boundaries"):
        state.promote(
            replace(release, project_id="other"),
            report(release),
            PromotionPolicy(1, 0.9),
            expected_revision=0,
        )
    with pytest.raises(ValueError, match="rollback"):
        state.roll_back(expected_revision=0)


def test_absent_classes_and_mismatched_artifact_evidence_cannot_pass() -> None:
    release = model(320)
    policy = PromotionPolicy(1, 0.9)
    evidence = report(release)
    with pytest.raises(ValueError, match="atomic model release"):
        policy.qualify(release, replace(evidence, weights_sha256="f" * 64))
    with pytest.raises(ValueError, match="atomic model release"):
        policy.qualify(release, replace(evidence, class_map_sha256="f" * 64))
    with pytest.raises(ValueError, match="atomic model release"):
        policy.qualify(release, replace(evidence, policy=InferencePolicy(640)))
    with pytest.raises(ValueError, match="target failed"):
        policy.qualify(release, report(release, missed=True))
    unsupported = replace(release, class_ids=("object", "absent"))
    with pytest.raises(ValueError, match="unsupported"):
        policy.qualify(unsupported, report(unsupported))


def test_incumbent_comparison_is_required_and_uses_the_same_frozen_dataset() -> None:
    first, second = model(320), model(320, "second")
    policy = PromotionPolicy(1, 0)
    champion = RegistryState("project-1", first.recipe_id).promote(
        first,
        report(first),
        policy,
        expected_revision=0,
        qualification=qualification_context(first, report(first), policy),
    )
    with pytest.raises(ValueError, match="comparison evidence"):
        champion.promote(second, report(second), policy, expected_revision=1)
    with pytest.raises(ValueError, match="frozen evaluation protocol"):
        champion.promote(
            second,
            replace(report(second), dataset_release_id="other-data"),
            policy,
            expected_revision=1,
            incumbent_report=report(first),
        )
    with pytest.raises(ValueError, match="regression"):
        champion.promote(
            second,
            report(second, missed=True),
            policy,
            expected_revision=1,
            incumbent_report=report(first),
        )


@pytest.mark.parametrize("gate", [PromotionPolicy(1, 0.9), PromotionPolicy(2, 0)])
def test_better_candidate_can_replace_incumbent_below_current_absolute_gates(
    gate: PromotionPolicy,
) -> None:
    first, second = model(320), model(320, "second")
    incumbent = report(first, missed=True)
    evidence = report(second)
    evidence = replace(
        evidence,
        overall=replace(
            evidence.overall,
            per_class={
                key: replace(metrics, support=2)
                for key, metrics in evidence.overall.per_class.items()
            },
        ),
    )
    state = RegistryState("project-1", first.recipe_id, 1, first)
    with pytest.raises(ValueError):
        gate.qualify(first, incumbent)
    evidence = replace(evidence, slices=evidence.slices | {"fixture_critical": evidence.overall})
    promoted = state.promote(
        second,
        evidence,
        gate,
        expected_revision=1,
        incumbent_report=incumbent,
        qualification=qualification_context(second, evidence, gate),
    )
    assert promoted.champion == second and promoted.rollback == first
    with pytest.raises(ValueError, match="atomic model release"):
        state.promote(
            second,
            evidence,
            gate,
            expected_revision=1,
            incumbent_report=replace(incumbent, weights_sha256="f" * 64),
        )


@pytest.mark.parametrize(
    "before,after,digest",
    [
        (("object", "other"), ("object",), "c" * 64),
        (("object",), ("object", "other"), "c" * 64),
        (("object",), ("object",), "e" * 64),
    ],
)
def test_catalogue_changes_cannot_skip_or_crash_incumbent_comparison(
    before: tuple[str, ...],
    after: tuple[str, ...],
    digest: str,
) -> None:
    first = replace(model(320), class_ids=before)
    second = replace(model(320, "second"), class_ids=after, class_map_sha256=digest)
    state = RegistryState("project-1", first.recipe_id, 1, first)
    with pytest.raises(ValueError, match="class catalogue"):
        state.promote(
            second,
            report(second, truth_classes=after),
            PromotionPolicy(1, 0),
            expected_revision=1,
            incumbent_report=report(first, truth_classes=before),
        )


def test_release_requires_complete_static_export_and_version_metadata() -> None:
    release = model(320)
    with pytest.raises(ValueError, match="static ONNX"):
        replace(release, static_input_signature=(1, 3, 640, 640))
    with pytest.raises(ValueError, match="SHA-256"):
        replace(release, weights_sha256="mutable-filename.pt")
    with pytest.raises(ValueError, match="Ultralytics version"):
        replace(release, ultralytics_version="latest")
    with pytest.raises(ValueError, match="native tiling"):
        replace(release, tiling_mode="native-tile")
