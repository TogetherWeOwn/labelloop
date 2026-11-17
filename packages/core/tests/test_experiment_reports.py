from dataclasses import replace
from typing import Any

import pytest
from test_experiment_manifest import manifest

from labelloop_core.evaluation import ClassMetrics, Coverage, EvaluationReport, Metrics
from labelloop_core.experiments import ExperimentManifest, validate_report
from labelloop_core.training import Checkpoint, InferencePolicy, TrainingResult


def report_case(side: int = 320) -> tuple[ExperimentManifest, TrainingResult, EvaluationReport]:
    frozen = manifest()
    cell = next(c for c in frozen.cells() if c.recipe_id == f"r{side}")
    request = frozen.request(cell, "training")
    checkpoint = Checkpoint(0, "f" * 64, 0.5, "evaluation")
    training = TrainingResult(
        request, (checkpoint,), checkpoint, request.arguments(), "8.4.204", "2.9.1+cpu", False
    )
    metrics = Metrics(
        1, 1, 0.5, 0.5, 0.5, 0.5, {"object": ClassMetrics(True, 1, 1, 1, 0, 0.5, 0.5, 0.5, 0.5)}
    )
    report = EvaluationReport(
        "evaluation",
        request.dataset_release_id,
        request.recipe_id,
        checkpoint.sha256,
        request.class_map_sha256,
        "development",
        request.policy,
        metrics,
        {"small": metrics},
        (Coverage("dev", 1, 0, 0),),
        (("dev", frozen.sources[1].truth_sha256),),
    )
    return frozen, training, report


@pytest.mark.parametrize("side", [320, 640])
def test_report_binding_accepts_both_frozen_recipes(side: int) -> None:
    frozen, training, report = report_case(side)
    validate_report(frozen, training, report, {"dev"})


@pytest.mark.parametrize(
    "changes",
    [
        {"recipe_id": "other"},
        {"dataset_release_id": "other"},
        {"class_map_sha256": "a" * 64},
        {"weights_sha256": "a" * 64},
        {"role": "calibration"},
        {"role": "locked_final_test"},
        {"policy": InferencePolicy(640)},
    ],
)
def test_report_identity_must_match_training(changes: dict[str, Any]) -> None:
    frozen, training, report = report_case()
    with pytest.raises(ValueError, match="pipeline mismatch"):
        validate_report(frozen, training, replace(report, **changes), {"dev"})


@pytest.mark.parametrize("ids", [set(), {"train"}, {"test"}, {"unknown"}, {"dev", "test"}])
def test_report_cannot_select_non_development_sources(ids: set[str]) -> None:
    frozen, training, report = report_case()
    with pytest.raises(ValueError, match="frozen development"):
        validate_report(frozen, training, report, ids)


@pytest.mark.parametrize(
    "changes",
    [
        {"coverage": ()},
        {"coverage": (Coverage("other", 1, 0, 0),)},
        {"coverage": (Coverage("dev", 1, 0, 0),) * 2},
        {"source_truth_sha256": ()},
        {"source_truth_sha256": (("dev", "a" * 64),)},
        {"source_truth_sha256": (("other", "2" * 64),)},
        {"source_truth_sha256": (("dev", "2" * 64),) * 2},
    ],
)
def test_report_membership_and_truth_cannot_drift(changes: dict[str, Any]) -> None:
    frozen, training, report = report_case()
    with pytest.raises(ValueError, match="membership|truth binding"):
        validate_report(frozen, training, replace(report, **changes), {"dev"})


@pytest.mark.parametrize(
    "coverage",
    [
        Coverage("dev", 0.5, 0, 0),
        Coverage("dev", 1, 1, 0),
        Coverage("dev", 1, 0, 1),
    ],
)
def test_report_cannot_hide_targets(coverage: Coverage) -> None:
    frozen, training, report = report_case()
    with pytest.raises(ValueError, match="reduced coverage"):
        validate_report(frozen, training, replace(report, coverage=(coverage,)), {"dev"})


def test_report_image_count_and_catalog_must_match() -> None:
    frozen, training, report = report_case()
    with pytest.raises(ValueError, match="membership"):
        validate_report(
            frozen, training, replace(report, overall=replace(report.overall, images=2)), {"dev"}
        )
    for catalog in ({}, {"other": report.overall.per_class["object"]}):
        metrics = replace(report.overall, per_class=catalog)
        for changed in (
            replace(report, overall=metrics),
            replace(report, slices={"small": metrics}),
        ):
            with pytest.raises(ValueError, match="catalog"):
                validate_report(frozen, training, changed, {"dev"})


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), -0.1, 1.1])
@pytest.mark.parametrize("field", ["map50", "map50_95", "precision", "recall"])
@pytest.mark.parametrize("where", ["overall", "class", "slice", "slice-class"])
def test_report_scores_must_be_finite_probabilities(value: float, field: str, where: str) -> None:
    frozen, training, report = report_case()
    metrics = report.overall
    changes: dict[str, Any] = {field: value}
    if where.endswith("class"):
        metrics = replace(
            metrics, per_class={"object": replace(metrics.per_class["object"], **changes)}
        )
    else:
        metrics = replace(metrics, **changes)
    report = (
        replace(report, slices={"small": metrics})
        if where.startswith("slice")
        else replace(report, overall=metrics)
    )
    with pytest.raises(ValueError, match="nonfinite or invalid"):
        validate_report(frozen, training, report, {"dev"})
