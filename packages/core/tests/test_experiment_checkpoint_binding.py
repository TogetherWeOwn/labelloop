from dataclasses import replace
from typing import Any

import pytest
from test_experiment_complete_matrix import evidence, experiment

from labelloop_core.experiments import compare_experiment


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("epochs", [(0,), (0, 2), (1, 2), (False, 1), (0.0, 1)])
def test_public_comparison_requires_complete_integer_checkpoint_domain(
    side: int, epochs: tuple[Any, ...]
) -> None:
    manifest = replace(experiment(), epochs=2)
    arms = evidence(manifest)
    arm = next(a for a in arms if a.cell.recipe_id == f"r{side}")
    checkpoints = tuple(replace(arm.training.selected, epoch=epoch) for epoch in epochs)
    training = replace(arm.training, checkpoints=checkpoints, selected=checkpoints[0])
    changed = replace(arm, training=training)
    with pytest.raises(ValueError, match="checkpoint epoch domain"):
        compare_experiment(manifest, tuple(changed if a.cell == arm.cell else a for a in arms))


@pytest.mark.parametrize("later_best", [False, True])
def test_public_comparison_accepts_complete_unordered_checkpoint_history(later_best: bool) -> None:
    manifest = replace(experiment(), epochs=2)
    arms = []
    for arm in evidence(manifest):
        first = arm.training.selected
        second = replace(
            first,
            epoch=1,
            sha256="f" * 64,
            validation_map=first.validation_map + (0.1 if later_best else 0),
            evaluation_run_id=first.evaluation_run_id + "-later",
        )
        selected = second if later_best else first
        training = replace(arm.training, checkpoints=(second, first), selected=selected)
        report = replace(
            arm.evaluation,
            run_id=selected.evaluation_run_id,
            weights_sha256=selected.sha256,
            overall=replace(arm.evaluation.overall, map50_95=selected.validation_map),
        )
        groups = tuple(
            (group, replace(report, weights_sha256=selected.sha256))
            for group, report in arm.group_reports
        )
        arms.append(
            replace(
                arm,
                training=training,
                evaluation=report,
                group_reports=groups,
                export_weights_sha256=selected.sha256,
            )
        )
    results = compare_experiment(manifest, tuple(arms))
    assert len(results) == 8
    assert {result.recipe_id for result in results} == {"r320", "r640"}
    assert all(not result.m6_exit_established for result in results)


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("field", ["run_id", "score"])
def test_public_comparison_binds_aggregate_selected_evaluation(side: int, field: str) -> None:
    manifest = experiment()
    arms = evidence(manifest)
    arm = next(a for a in arms if a.cell.recipe_id == f"r{side}")
    report = arm.evaluation
    if field == "run_id":
        report = replace(report, run_id=report.run_id + "-other")
    else:
        report = replace(report, overall=replace(report.overall, map50_95=0.9))
    changed = replace(arm, evaluation=report)
    with pytest.raises(ValueError, match="selected checkpoint evaluation"):
        compare_experiment(manifest, tuple(changed if a.cell == arm.cell else a for a in arms))


def test_public_comparison_keeps_independent_group_evaluation_scores() -> None:
    manifest = experiment()
    arms = tuple(
        replace(
            arm,
            group_reports=tuple(
                (group, replace(report, overall=replace(report.overall, map50_95=0.2 + 0.1 * i)))
                for i, (group, report) in enumerate(arm.group_reports)
            ),
        )
        for arm in evidence(manifest)
    )
    results = compare_experiment(manifest, arms)
    assert len(results) == 8
    assert all(result.mean_delta == 0 for result in results)
