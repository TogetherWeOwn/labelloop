from dataclasses import replace

import pytest
from test_experiment_training_evidence import arm_case

from labelloop_core.evaluation import Metrics
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
        score = 0.5 + gain * (cell.initialization == "warm-start")
        checkpoint = replace(arm.training.selected, validation_map=score)
        training = replace(
            arm.training,
            request=request,
            resolved_arguments=request.arguments(),
            checkpoints=(checkpoint,),
            selected=checkpoint,
        )
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


@pytest.mark.parametrize("drift", ["overall", "group", "slice", "torch"])
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
        arm = replace(arm, training=replace(arm.training, torch_version="different"))
    arms[arm.cell] = arm
    with pytest.raises(ValueError, match="paired"):
        _compare(frozen, baseline, candidate, arms, "initialization")


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("drift", ["group-slices", "joint-seeds", "joint-environment"])
def test_pair_rejects_drift_hidden_by_aggregate_or_within_seed_equality(
    side: int, drift: str
) -> None:
    frozen, baseline, candidate, arms = pair_case(side)

    def support(scores: Metrics, count: int) -> Metrics:
        return replace(
            scores,
            support=count,
            per_class={"object": replace(scores.per_class["object"], support=count)},
        )

    for cell, arm in tuple(arms.items()):
        reports = []
        for index, (group, report) in enumerate(arm.group_reports):
            count = 3 if drift == "group-slices" else 1 + cell.seed
            small = 1 + index
            if cell.initialization == "warm-start":
                small = 3 - small
            report = replace(
                report,
                overall=support(report.overall, count),
                slices={
                    "small": support(
                        report.slices["small"], small if drift == "group-slices" else count
                    )
                },
            )
            reports.append((group, report))
        evaluation = replace(
            arm.evaluation,
            overall=support(arm.evaluation.overall, sum(r.overall.support for _, r in reports)),
            slices={
                "small": support(
                    arm.evaluation.slices["small"],
                    sum(r.slices["small"].support for _, r in reports),
                )
            },
        )
        training = arm.training
        if drift == "joint-environment" and cell.seed:
            training = replace(training, torch_version="different")
        if drift == "joint-environment":
            reports, evaluation = list(arm.group_reports), arm.evaluation
        arms[cell] = replace(
            arm, evaluation=evaluation, group_reports=tuple(reports), training=training
        )
        validate_arm(frozen, arms[cell])
    with pytest.raises(ValueError, match="paired"):
        _compare(frozen, baseline, candidate, arms, "initialization")


def test_ultralytics_drift_is_rejected_at_result_construction() -> None:
    _, _, _, arms = pair_case(320)
    with pytest.raises(ValueError, match="pinned contract"):
        replace(next(iter(arms.values())).training, ultralytics_version="different")


def test_seed_deltas_are_averaged_within_group_before_bootstrap() -> None:
    frozen, baseline, candidate, arms = pair_case(320)
    for cell, arm in tuple(arms.items()):
        if cell.initialization != "warm-start":
            continue
        reports = tuple(
            (
                group,
                replace(
                    report,
                    overall=replace(
                        report.overall,
                        map50_95=0.5 + ((0.1 + 0.2 * cell.seed) if index == 0 else -0.1),
                    ),
                ),
            )
            for index, (group, report) in enumerate(arm.group_reports)
        )
        arms[cell] = replace(arm, group_reports=reports)
        validate_arm(frozen, arms[cell])
    result = _compare(frozen, baseline, candidate, arms, "initialization")
    assert result.group_deltas[0][1] == pytest.approx(0.2)
    assert result.group_deltas[1][1] == pytest.approx(-0.1)
    assert result.mean_delta == pytest.approx(0.05)
    assert result.bootstrap_95_interval == pytest.approx((-0.1, 0.2))


@pytest.mark.parametrize("scope", ["class", "slice"])
def test_critical_regression_blocks_improving_primary_group_metric(scope: str) -> None:
    frozen, baseline, candidate, arms = pair_case(320)
    arm = arms[candidate]
    report = arm.evaluation
    if scope == "class":
        scores = replace(
            report.overall,
            per_class={"object": replace(report.overall.per_class["object"], map50_95=0.4)},
        )
        report = replace(report, overall=scores)
    else:
        report = replace(report, slices={"small": replace(report.slices["small"], map50_95=0.4)})
    arms[candidate] = replace(arm, evaluation=report)
    validate_arm(frozen, arms[candidate])
    result = _compare(frozen, baseline, candidate, arms, "initialization")
    assert result.mean_delta > 0 and result.outcome == "regression"
    assert any(reason.startswith(scope + ":") for reason in result.regression_reasons)
