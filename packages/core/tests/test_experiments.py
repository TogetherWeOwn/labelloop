from dataclasses import replace
from typing import cast

import pytest
from test_experiment_complete_matrix import digest, evidence, experiment, metrics

from labelloop_core.experiments import BASELINE, compare_experiment
from labelloop_core.training import AugmentationProfile, InferencePolicy, TrainingRequest


@pytest.mark.parametrize(
    "field,value",
    [
        ("fliplr", 0.5),
        ("scale", 0.5),
        ("translate", 0.1),
        ("hsv_s", 0.6),
    ],
)
def test_reserved_baseline_cannot_drift(field: str, value: float) -> None:
    from typing import Any

    change: dict[str, Any] = {field: value}
    with pytest.raises(ValueError, match="baseline augmentation drift"):
        replace(BASELINE, **change)


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf")])
def test_augmentation_bounds(value: float) -> None:
    with pytest.raises(ValueError, match="finite probabilities"):
        AugmentationProfile("invalid", translate=value)


def test_training_flip_requires_unique_mirror_safe_classes() -> None:
    request = TrainingRequest(
        "run",
        "project",
        "recipe",
        "release",
        "a" * 64,
        "b" * 64,
        "splits",
        "c" * 64,
        InferencePolicy(320),
    )
    profile = AugmentationProfile("flip-v1", fliplr=0.5)
    for safety in ((), (("object", False),), (("object", True), ("object", True))):
        with pytest.raises(ValueError, match="mirror_safe"):
            replace(request, augmentation=profile, class_mirror_safety=safety)
    flipped = replace(request, augmentation=profile, class_mirror_safety=(("object", True),))
    assert flipped.arguments()["fliplr"] == 0.5


def test_flip_requires_the_complete_mirror_safe_catalog() -> None:
    manifest = experiment()
    with pytest.raises(ValueError, match="mirror_safe"):
        replace(manifest, class_mirror_safety=(("object", False),))
    cell = next(c for c in manifest.cells() if c.augmentation_id == "flip-v1")
    request = manifest.request(cell, "run")
    for safety in ((), (("object", False),), (("object", True), ("object", True))):
        with pytest.raises(ValueError, match="mirror_safe"):
            replace(request, class_mirror_safety=safety)


@pytest.mark.parametrize("role", ["development", "calibration", "locked_final_test"])
def test_source_group_and_exact_pixel_leakage_fail(role: str) -> None:
    manifest = experiment()
    source = next(s for s in manifest.sources if s.role == role)
    for changed in (
        replace(source, group_id="train"),
        replace(source, source_sha256=manifest.sources[0].source_sha256),
    ):
        with pytest.raises(ValueError, match="leakage"):
            replace(
                manifest, sources=tuple(changed if s == source else s for s in manifest.sources)
            )


def test_checkpoint_ancestry_and_curated_anchor_cannot_change_silently() -> None:
    manifest = experiment(expanded=True)
    recipe = manifest.recipes[0]
    for changed in (
        replace(recipe, warm_start_base_sha256=digest("wrong-base")),
        replace(recipe, warm_start_training_groups=("test",)),
        replace(recipe, warm_start_training_groups=("extra",)),
        replace(recipe, warm_start_training_groups=()),
    ):
        with pytest.raises(ValueError, match="base checkpoint|ancestry"):
            replace(manifest, recipes=(changed, manifest.recipes[1]))
    with pytest.raises(ValueError, match="frozen cohort"):
        replace(
            manifest,
            datasets=(replace(manifest.datasets[0], training_asset_ids=("extra",)),)
            + manifest.datasets[1:],
        )


def test_critical_slice_regression_is_not_hidden_by_aggregate_gain() -> None:
    manifest = experiment()
    arms = list(evidence(manifest))
    index = next(i for i, a in enumerate(arms) if a.cell.initialization == "warm-start")
    arm = arms[index]
    arms[index] = replace(
        arm, evaluation=replace(arm.evaluation, slices={"source_small": metrics(0.1, 2)})
    )
    results = compare_experiment(manifest, tuple(arms))
    assert any(
        r.outcome == "regression" and any("slice:" in reason for reason in r.regression_reasons)
        for r in results
    )


def test_one_group_does_not_create_a_spurious_uncertainty_interval() -> None:
    original = experiment()
    manifest = replace(
        original,
        sources=tuple(
            replace(s, group_id="dev-a") if s.role == "development" else s for s in original.sources
        ),
    )
    results = compare_experiment(manifest, evidence(manifest))
    assert all(
        r.bootstrap_95_interval is None and r.outcome == "insufficient-evidence" for r in results
    )


def test_support_drift_between_paired_arms_is_rejected() -> None:
    manifest = experiment()
    arms = list(evidence(manifest))
    arm = arms[0]
    arms[0] = replace(
        arm, evaluation=replace(arm.evaluation, overall=replace(arm.evaluation.overall, support=99))
    )
    with pytest.raises(ValueError, match="support drift"):
        compare_experiment(manifest, tuple(arms))


@pytest.mark.parametrize("seed", [True, 1.0])
def test_request_rejects_noninteger_cell_seed(seed: object) -> None:
    from typing import Any

    manifest = experiment()
    cell = next(c for c in manifest.cells() if c.seed == 1)
    changed = replace(cell, seed=cast(Any, seed))
    with pytest.raises(ValueError, match="outside"):
        manifest.request(changed, "run")
