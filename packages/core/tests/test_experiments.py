import hashlib
from dataclasses import replace
from typing import cast

import pytest

from labelloop_core.evaluation import ClassMetrics, Coverage, EvaluationReport, Metrics
from labelloop_core.experiments import (
    BASELINE,
    ArmEvidence,
    Cohort,
    DatasetPin,
    ExperimentManifest,
    RecipePin,
    RetentionObservation,
    SourcePin,
    SourceRole,
    compare_experiment,
)
from labelloop_core.training import (
    AugmentationProfile,
    Checkpoint,
    InferencePolicy,
    TrainingResult,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def experiment(*, expanded: bool = False, fixture: bool = True) -> ExperimentManifest:
    sources = tuple(
        SourcePin(name, name, digest(name), digest(name + "truth"), cast(SourceRole, role))
        for name, role in (
            ("train", "curated"),
            ("dev-a", "development"),
            ("dev-b", "development"),
            ("cal", "calibration"),
            ("test", "locked_final_test"),
            *(([("extra", "expansion")]) if expanded else []),
        )
    )
    recipes = tuple(
        RecipePin(
            f"r{side}",
            digest(f"r{side}"),
            InferencePolicy(side),
            digest(f"warm{side}"),
            digest("base"),
            ("train",),
        )
        for side in (320, 640)
    )
    return ExperimentManifest(
        "experiment",
        "project",
        digest("classes"),
        (("object", True),),
        "splits-v1",
        digest("base"),
        sources,
        recipes,
        tuple(
            DatasetPin(
                r.recipe_id,
                cast(Cohort, cohort),
                "release-" + cohort,
                digest(r.recipe_id + cohort),
                ids,
            )
            for r in recipes
            for cohort, ids in (
                ("curated-only", ("train",)),
                *(([("expanded", ("train", "extra"))]) if expanded else []),
            )
        ),
        (BASELINE, AugmentationProfile("flip-v1", fliplr=0.5)),
        (0, 1),
        fixture_only=fixture,
        critical_slices=("source_small",),
    )


def metrics(score: float, count: int) -> Metrics:
    return Metrics(
        count,
        count,
        score,
        score,
        score,
        score,
        {"object": ClassMetrics(True, count, count, count, 0, score, score, score, score)},
    )


def evidence(manifest: ExperimentManifest) -> tuple[ArmEvidence, ...]:
    result = []
    for index, cell in enumerate(manifest.cells()):
        request = manifest.request(cell, f"run-{index}")
        score = (
            0.4
            + 0.1 * (cell.initialization == "warm-start")
            + 0.05 * (cell.augmentation_id != BASELINE.profile_id)
        )
        checkpoint = Checkpoint(0, digest(f"checkpoint-{index}"), score, f"eval-{index}")
        training = TrainingResult(
            request, (checkpoint,), checkpoint, request.arguments(), "8.4.204", "2.9.1+cpu", False
        )
        reports = []
        dev = [s for s in manifest.sources if s.role == "development"]
        for group in manifest.development_groups():
            members = [s for s in dev if s.group_id == group]
            reports.append(
                (
                    group,
                    EvaluationReport(
                        f"eval-{index}-{group}",
                        request.dataset_release_id,
                        request.recipe_id,
                        checkpoint.sha256,
                        request.class_map_sha256,
                        "development",
                        request.policy,
                        metrics(score, len(members)),
                        {"source_small": metrics(score, len(members))},
                        tuple(Coverage(s.asset_id, 1, 0, 0) for s in members),
                    ),
                )
            )
        full = replace(
            reports[0][1],
            run_id=checkpoint.evaluation_run_id,
            overall=metrics(score, len(dev)),
            slices={"source_small": metrics(score, len(dev))},
            coverage=tuple(Coverage(s.asset_id, 1, 0, 0) for s in dev),
        )
        ids = next(
            d.training_asset_ids
            for d in manifest.datasets
            if (d.recipe_id, d.cohort) == (cell.recipe_id, cell.cohort)
        )
        result.append(
            ArmEvidence(
                manifest.digest(),
                cell,
                training,
                full,
                tuple(reports),
                tuple(RetentionObservation(asset_id, 1, 1, 0, 0) for asset_id in ids),
                tuple(
                    (stage, ((1, 3, request.policy.imgsz, request.policy.imgsz),))
                    for stage in ("training", "validation", "inference")
                ),
                digest(f"export-{index}"),
                checkpoint.sha256,
                (1, 3, request.policy.imgsz, request.policy.imgsz),
                request.policy,
                len(dev),
                1,
            )
        )
    return tuple(result)


def test_paired_results_are_per_recipe_seed_group_and_never_establish_m6_exit() -> None:
    manifest = experiment(expanded=True)
    arms = evidence(manifest)
    results = compare_experiment(manifest, arms)
    assert results == compare_experiment(manifest, tuple(reversed(arms)))
    assert {r.axis for r in results} == {"initialization", "augmentation", "training-cohort"}
    assert {r.recipe_id for r in results} == {"r320", "r640"}
    assert all(r.source_groups == 2 and r.images == 2 and r.support == 2 for r in results)
    assert all(r.seeds == (0, 1) and not r.m6_exit_established for r in results)
    assert {r.outcome for r in results} == {"fixture-wiring-only", "plateau-or-inconclusive"}
    assert all(r.bootstrap_95_interval is not None for r in results)
    assert manifest.digest() == manifest.digest()
    assert replace(manifest, uncertainty_seed=2).digest() != manifest.digest()


def test_observed_gain_still_requires_external_adjudicated_quality_and_declared_metric() -> None:
    manifest = experiment(fixture=False)
    results = compare_experiment(manifest, evidence(manifest))
    assert all(
        r.outcome == "observed-gain-unqualified" and not r.m6_exit_established for r in results
    )


@pytest.mark.parametrize(
    "change",
    [
        {"fliplr": 0.5},
        {"scale": 0.5},
        {"translate": 0.1},
        {"hsv_s": 0.6},
    ],
)
def test_reserved_baseline_cannot_drift(change: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="baseline augmentation drift"):
        replace(BASELINE, **change)


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf")])
def test_augmentation_bounds(value: float) -> None:
    with pytest.raises(ValueError, match="finite probabilities"):
        AugmentationProfile("invalid", translate=value)


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


@pytest.mark.parametrize(
    "kind",
    [
        "missing-cell",
        "duplicate-cell",
        "missing-group",
        "wrong-checkpoint",
        "locked-final",
        "calibration",
        "retention",
        "small-count",
        "shape",
        "export",
        "manifest",
        "unsupported-class",
        "unsupported-slice",
        "coverage",
        "baseline-args",
        "wrong-init",
    ],
)
def test_invalid_comparison_evidence_fails_closed(kind: str) -> None:
    manifest = experiment()
    arms = list(evidence(manifest))
    arm = arms[0]
    if kind == "missing-cell":
        arms.pop()
    elif kind == "duplicate-cell":
        arms.append(arm)
    elif kind == "missing-group":
        arms[0] = replace(arm, group_reports=arm.group_reports[:1])
    elif kind == "wrong-checkpoint":
        arms[0] = replace(arm, evaluation=replace(arm.evaluation, weights_sha256=digest("wrong")))
    elif kind in {"locked-final", "calibration"}:
        arms[0] = replace(
            arm,
            evaluation=replace(
                arm.evaluation,
                role="locked_final_test" if kind == "locked-final" else "calibration",
            ),
        )
    elif kind == "retention":
        arms[0] = replace(arm, retention=(RetentionObservation("train", 1, 0, 1, 0),))
    elif kind == "small-count":
        arms[0] = replace(arm, retention=(RetentionObservation("train", 1, 1, 2, 1),))
    elif kind == "shape":
        arms[0] = replace(arm, tensor_shapes=(("training", ((1, 3, 352, 352),)),))
    elif kind == "export":
        arms[0] = replace(arm, export_weights_sha256=digest("wrong"))
    elif kind == "manifest":
        arms[0] = replace(arm, manifest_sha256=digest("wrong"))
    elif kind == "unsupported-class":
        unsupported = replace(
            arm.evaluation.overall.per_class["object"], supported=False, support=0, map50_95=None
        )
        arms[0] = replace(
            arm,
            evaluation=replace(
                arm.evaluation,
                overall=replace(arm.evaluation.overall, per_class={"object": unsupported}),
            ),
        )
    elif kind == "unsupported-slice":
        arms[0] = replace(arm, evaluation=replace(arm.evaluation, slices={}))
    elif kind == "coverage":
        arms[0] = replace(
            arm,
            evaluation=replace(
                arm.evaluation, coverage=(Coverage("dev-a", 0.5, 1, 0), Coverage("dev-b", 1, 0, 0))
            ),
        )
    elif kind == "baseline-args":
        # A frozen dataclass still contains mutable vendor dictionaries: re-audit at consumption.
        resolved = arm.training.resolved_arguments
        assert isinstance(resolved, dict)
        resolved["fliplr"] = 0.5
    elif kind == "wrong-init":
        request = replace(arm.training.request, initialization_sha256=digest("unapproved"))
        arms[0] = replace(arm, training=replace(arm.training, request=request))
    with pytest.raises(ValueError):
        compare_experiment(manifest, tuple(arms))


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
