import hashlib
from dataclasses import replace
from typing import cast

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
        1.0,
        1.0,
        {"object": ClassMetrics(True, count, count, count, 0, score, score, 1.0, 1.0)},
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
                        tuple((s.asset_id, s.truth_sha256) for s in members),
                    ),
                )
            )
        full = replace(
            reports[0][1],
            run_id=checkpoint.evaluation_run_id,
            overall=metrics(score, len(dev)),
            slices={"source_small": metrics(score, len(dev))},
            coverage=tuple(Coverage(s.asset_id, 1, 0, 0) for s in dev),
            source_truth_sha256=tuple((s.asset_id, s.truth_sha256) for s in dev),
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
    for arm in arms:
        observed = arm.evaluation.overall.per_class["object"]
        assert observed.precision == observed.true_positives / observed.predictions
        assert observed.recall == observed.true_positives / observed.support
        assert arm.evaluation.overall.precision == arm.evaluation.overall.recall == 1.0
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
