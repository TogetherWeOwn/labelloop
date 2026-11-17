from dataclasses import replace

import pytest
from test_experiment_training_evidence import arm_case

from labelloop_core.experiments import ArmEvidence, Cell, ExperimentManifest, _compare, validate_arm


def pair_case(
    side: int, *, fixture: bool = True, groups: int = 2, gain: float = 0.1
) -> tuple[ExperimentManifest, Cell, Cell, dict[Cell, ArmEvidence]]:
    frozen, arm = arm_case(side)
    extra = replace(frozen.sources[1], asset_id="dev2", group_id="dev2", source_sha256="9" * 64)
    frozen = replace(
        frozen,
        sources=(*frozen.sources, *((extra,) if groups == 2 else ())),
        critical_slices=("small",),
        fixture_only=fixture,
    )
    baseline = arm.cell
    candidate = replace(baseline, initialization="warm-start")
    arms: dict[Cell, ArmEvidence] = {}
    for cell in (replace(c, seed=s) for c in (baseline, candidate) for s in frozen.seeds):
        request = frozen.request(cell, f"{cell.initialization}-{cell.seed}")
        training = replace(arm.training, request=request, resolved_arguments=request.arguments())
        score = 0.5 + gain * (cell.initialization == "warm-start")
        per_class = {"object": replace(arm.evaluation.overall.per_class["object"], map50_95=score)}
        single = replace(arm.evaluation.overall, map50_95=score, per_class=per_class)
        reports = tuple(
            (
                source.group_id,
                replace(
                    arm.evaluation,
                    overall=single,
                    slices={"small": single},
                    coverage=(replace(arm.evaluation.coverage[0], asset_id=source.asset_id),),
                    source_truth_sha256=((source.asset_id, source.truth_sha256),),
                ),
            )
            for source in frozen.sources
            if source.role == "development"
        )
        total = replace(
            single,
            images=groups,
            support=groups,
            per_class={"object": replace(per_class["object"], support=groups)},
        )
        report = replace(
            arm.evaluation,
            overall=total,
            slices={"small": total},
            coverage=tuple(r.coverage[0] for _, r in reports),
            source_truth_sha256=tuple(r.source_truth_sha256[0] for _, r in reports),
        )
        arms[cell] = replace(
            arm,
            cell=cell,
            manifest_sha256=frozen.digest(),
            training=training,
            evaluation=report,
            group_reports=reports,
        )
        validate_arm(frozen, arms[cell])
    return frozen, baseline, candidate, arms


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize(
    "fixture,gain,outcome",
    [
        (True, 0.1, "fixture-wiring-only"),
        (False, 0.1, "observed-gain-unqualified"),
        (False, 0.0, "plateau-or-inconclusive"),
        (False, -0.1, "regression"),
    ],
)
def test_prevalidated_pair_reports_deterministic_uncertainty_and_honest_outcome(
    side: int,
    fixture: bool,
    gain: float,
    outcome: str,
) -> None:
    frozen, baseline, candidate, arms = pair_case(side, fixture=fixture, gain=gain)
    result = _compare(frozen, baseline, candidate, arms, "initialization")
    assert result == _compare(
        frozen, baseline, candidate, dict(reversed(arms.items())), "initialization"
    )
    assert result.mean_delta == pytest.approx(gain)
    assert result.bootstrap_95_interval == pytest.approx((gain, gain))
    assert (result.source_groups, result.images, result.support) == (2, 2, 2)
    assert result.per_class_support == (("object", 2),) and result.seeds == frozen.seeds
    assert result.outcome == outcome and not result.m6_exit_established
    assert bool(result.regression_reasons) is (gain < 0)


def test_single_source_group_cannot_establish_uncertainty() -> None:
    frozen, baseline, candidate, arms = pair_case(320, groups=1)
    result = _compare(frozen, baseline, candidate, arms, "initialization")
    assert result.bootstrap_95_interval is None and result.outcome == "insufficient-evidence"


@pytest.mark.parametrize("drift", ["overall", "group", "slice", "ultralytics", "torch"])
def test_prevalidated_pair_rejects_support_and_environment_drift(drift: str) -> None:
    frozen, baseline, candidate, arms = pair_case(640)
    arm = arms[replace(candidate, seed=frozen.seeds[-1])]
    report = arm.evaluation
    changed = replace(report.overall, images=report.overall.images + 1)
    if drift == "overall":
        arm = replace(arm, evaluation=replace(report, overall=changed))
    elif drift == "group":
        group, first = arm.group_reports[0]
        arm = replace(
            arm, group_reports=((group, replace(first, overall=changed)), *arm.group_reports[1:])
        )
    elif drift == "slice":
        arm = replace(arm, evaluation=replace(report, slices={"small": changed}))
    else:
        arm = replace(arm, training=replace(arm.training, **{f"{drift}_version": "different"}))
    arms[arm.cell] = arm
    with pytest.raises(ValueError, match="paired"):
        _compare(frozen, baseline, candidate, arms, "initialization")
