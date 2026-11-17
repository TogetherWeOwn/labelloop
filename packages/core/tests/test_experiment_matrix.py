from dataclasses import replace

import pytest
from test_experiment_manifest import manifest
from test_experiment_training_evidence import arm_case

from labelloop_core.experiments import BASELINE, ArmEvidence, ExperimentManifest, compare_experiment


def matrix_case(expanded: bool) -> tuple[ExperimentManifest, tuple[ArmEvidence, ...]]:
    frozen = manifest(expanded=expanded)
    arms = []
    for index, cell in enumerate(frozen.cells()):
        side = next(r.policy.imgsz for r in frozen.recipes if r.recipe_id == cell.recipe_id)
        _, template = arm_case(side)
        request = frozen.request(cell, f"training-{index}")
        report = template.evaluation
        arms.append(
            replace(
                template,
                cell=cell,
                manifest_sha256=frozen.digest(),
                training=replace(
                    template.training, request=request, resolved_arguments=request.arguments()
                ),
                retention=template.retention
                + (
                    (replace(template.retention[0], asset_id="extra"),)
                    if cell.cohort == "expanded"
                    else ()
                ),
                evaluation=report,
            )
        )
    return frozen, tuple(arms)


@pytest.mark.parametrize("expanded,count", [(False, 8), (True, 24)])
def test_complete_matrix_compares_each_axis_once_per_recipe_not_per_seed(
    expanded: bool, count: int
) -> None:
    frozen, arms = matrix_case(expanded)
    results = compare_experiment(frozen, arms)
    assert results == compare_experiment(frozen, tuple(reversed(arms)))
    assert len(results) == count
    assert len({(r.axis, r.baseline, r.candidate) for r in results}) == count
    assert {r.recipe_id for r in results} == {"r320", "r640"}
    assert {r.axis for r in results} == (
        {"initialization", "augmentation", "training-cohort"}
        if expanded
        else {"initialization", "augmentation"}
    )
    for result in results:
        candidate, baseline = result.candidate, result.baseline
        assert candidate.seed == baseline.seed == frozen.seeds[0]
        assert result.seeds == frozen.seeds and not result.m6_exit_established
        if result.axis == "initialization":
            assert candidate.initialization == "warm-start"
            assert baseline == replace(candidate, initialization="base")
        elif result.axis == "augmentation":
            assert candidate.augmentation_id != BASELINE.profile_id
            assert baseline == replace(candidate, augmentation_id=BASELINE.profile_id)
        else:
            assert candidate.cohort == "expanded"
            assert baseline == replace(candidate, cohort="curated-only")


@pytest.mark.parametrize("case", ["empty", "missing", "duplicate", "unexpected", "run-id"])
def test_matrix_rejects_missing_duplicate_extra_cells_and_reused_runs(case: str) -> None:
    frozen, arms = matrix_case(False)
    if case == "empty":
        arms = ()
    elif case == "missing":
        arms = arms[:-1]
    elif case == "duplicate":
        arms = (*arms[:-1], arms[0])
    elif case == "unexpected":
        arms = (*arms[:-1], replace(arms[-1], cell=replace(arms[-1].cell, seed=9)))
    else:
        last = arms[-1]
        request = replace(last.training.request, run_id=arms[0].training.request.run_id)
        arms = (*arms[:-1], replace(last, training=replace(last.training, request=request)))
    with pytest.raises(ValueError, match="every paired cell|run identities"):
        compare_experiment(frozen, arms)


@pytest.mark.parametrize("case", ["manifest", "final-test", "retention", "checkpoint"])
def test_matrix_validates_every_arm_before_calculating_any_pair(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen, arms = matrix_case(False)
    last = arms[-1]
    if case == "manifest":
        last = replace(last, manifest_sha256="0" * 64)
    elif case == "final-test":
        last = replace(last, evaluation=replace(last.evaluation, role="locked_final_test"))
    elif case == "retention":
        last = replace(last, retention=())
    else:
        last = replace(last, export_weights_sha256="0" * 64)

    def premature_compare(*args: object) -> None:
        pytest.fail("comparison began before all arms were validated")

    monkeypatch.setattr("labelloop_core.experiments._compare", premature_compare)
    with pytest.raises(ValueError):
        compare_experiment(frozen, (*arms[:-1], last))
