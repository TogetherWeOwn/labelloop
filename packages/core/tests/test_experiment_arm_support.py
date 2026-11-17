from dataclasses import replace
from typing import Any

import pytest
from test_experiment_training_evidence import arm_case

from labelloop_core.experiments import validate_arm


@pytest.mark.parametrize("side", [320, 640])
def test_arm_accepts_supported_frozen_reports(side: int) -> None:
    frozen, arm = arm_case(side)
    frozen = replace(frozen, critical_slices=("small",))
    validate_arm(frozen, replace(arm, manifest_sha256=frozen.digest()))


@pytest.mark.parametrize("groups", [(), ("other",), ("dev", "dev")])
def test_arm_requires_each_development_group_exactly_once(groups: tuple[str, ...]) -> None:
    frozen, arm = arm_case()
    with pytest.raises(ValueError, match="unpaired source groups"):
        validate_arm(frozen, replace(arm, group_reports=tuple((g, arm.evaluation) for g in groups)))


@pytest.mark.parametrize("where", ["aggregate", "group"])
@pytest.mark.parametrize(
    "field,value", [("role", "locked_final_test"), ("source_truth_sha256", ())]
)
def test_arm_rechecks_aggregate_and_group_report_binding(
    where: str, field: str, value: Any
) -> None:
    frozen, arm = arm_case()
    changed = replace(arm.evaluation, **{field: value})
    arm = (
        replace(arm, evaluation=changed)
        if where == "aggregate"
        else replace(arm, group_reports=(("dev", changed),))
    )
    with pytest.raises(ValueError, match="pipeline mismatch|truth binding"):
        validate_arm(frozen, arm)


def test_group_report_must_cover_its_own_sources_and_declared_metric() -> None:
    frozen, arm = arm_case()
    second = replace(
        frozen.sources[1],
        asset_id="dev-second",
        group_id="second",
        source_sha256="8" * 64,
        truth_sha256="9" * 64,
    )
    frozen = replace(frozen, sources=(*frozen.sources, second))
    report = arm.evaluation
    aggregate = replace(
        report,
        overall=replace(report.overall, images=2),
        coverage=(*report.coverage, replace(report.coverage[0], asset_id=second.asset_id)),
        source_truth_sha256=(*report.source_truth_sha256, (second.asset_id, second.truth_sha256)),
    )
    arm = replace(arm, manifest_sha256=frozen.digest(), evaluation=aggregate)
    with pytest.raises(ValueError, match="membership"):
        validate_arm(frozen, replace(arm, group_reports=(*arm.group_reports, ("second", report))))
    frozen, arm = arm_case()
    report = replace(arm.evaluation, overall=replace(arm.evaluation.overall, map50_95=None))
    with pytest.raises(ValueError, match="source group lacks support"):
        validate_arm(frozen, replace(arm, group_reports=(("dev", report),)))


@pytest.mark.parametrize("kind", ["class", "slice"])
@pytest.mark.parametrize("support", [0, -1, True, 0.5, float("nan"), float("inf")])
def test_arm_support_must_meet_frozen_minimum_as_an_integer(kind: str, support: Any) -> None:
    frozen, arm = arm_case()
    frozen = replace(frozen, critical_slices=("small",))
    report = arm.evaluation
    if kind == "class":
        metrics = replace(report.overall.per_class["object"], support=support)
        report = replace(report, overall=replace(report.overall, per_class={"object": metrics}))
    else:
        report = replace(report, slices={"small": replace(report.overall, support=support)})
    with pytest.raises(ValueError, match=f"unsupported .*{kind}"):
        validate_arm(frozen, replace(arm, manifest_sha256=frozen.digest(), evaluation=report))


@pytest.mark.parametrize("kind", ["class", "slice"])
def test_arm_enforces_configured_support_thresholds(kind: str) -> None:
    frozen, arm = arm_case()
    frozen = replace(
        frozen, critical_slices=("small",), minimum_class_support=2, minimum_slice_support=2
    )
    if kind == "slice":
        frozen = replace(frozen, minimum_class_support=1)
    with pytest.raises(ValueError, match=f"unsupported .*{kind}"):
        validate_arm(frozen, replace(arm, manifest_sha256=frozen.digest()))


@pytest.mark.parametrize("changes", [{"supported": False}, {"supported": 1}, {"map50_95": None}])
def test_arm_requires_supported_class_metric(changes: dict[str, Any]) -> None:
    frozen, arm = arm_case()
    metrics = replace(arm.evaluation.overall.per_class["object"], **changes)
    report = replace(
        arm.evaluation, overall=replace(arm.evaluation.overall, per_class={"object": metrics})
    )
    with pytest.raises(ValueError, match="unsupported class"):
        validate_arm(frozen, replace(arm, evaluation=report))


@pytest.mark.parametrize("missing", [True, False])
def test_arm_requires_each_critical_slice_and_metric(missing: bool) -> None:
    frozen, arm = arm_case()
    frozen = replace(frozen, critical_slices=("small",))
    report = replace(
        arm.evaluation,
        slices={} if missing else {"small": replace(arm.evaluation.overall, map50_95=None)},
    )
    with pytest.raises(ValueError, match="unsupported critical slice"):
        validate_arm(frozen, replace(arm, manifest_sha256=frozen.digest(), evaluation=report))


@pytest.mark.parametrize("where", ["group", "slice", "aggregate"])
@pytest.mark.parametrize("total", [0, 1, 2])
def test_arm_rejects_inconsistent_metric_support(where: str, total: int) -> None:
    frozen, arm = arm_case()
    frozen = replace(frozen, critical_slices=("small",))
    unsupported = replace(
        arm.evaluation.overall.per_class["object"], supported=False, support=0, map50_95=None
    )
    metrics = replace(arm.evaluation.overall, support=total, per_class={"object": unsupported})
    report = replace(arm.evaluation, overall=metrics)
    arm = (
        replace(arm, group_reports=(("dev", report),))
        if where == "group"
        else replace(arm, evaluation=report)
        if where == "aggregate"
        else replace(arm, evaluation=replace(arm.evaluation, slices={"small": metrics}))
    )
    with pytest.raises(ValueError, match="support|unsupported"):
        validate_arm(frozen, replace(arm, manifest_sha256=frozen.digest()))


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("aggregate_support,group_support", [(2, 1), (1, 2)])
def test_arm_reconciles_aggregate_and_group_class_support(
    side: int, aggregate_support: int, group_support: int
) -> None:
    frozen, arm = arm_case(side)
    frozen = replace(frozen, minimum_class_support=aggregate_support)
    report = arm.evaluation
    aggregate = replace(
        report.overall,
        support=aggregate_support,
        per_class={
            "object": replace(report.overall.per_class["object"], support=aggregate_support)
        },
    )
    group = replace(
        report.overall,
        support=group_support,
        per_class={"object": replace(report.overall.per_class["object"], support=group_support)},
    )
    arm = replace(
        arm,
        manifest_sha256=frozen.digest(),
        evaluation=replace(report, overall=aggregate),
        group_reports=(("dev", replace(report, overall=group)),),
    )
    with pytest.raises(ValueError, match="aggregate class support differs"):
        validate_arm(frozen, arm)


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("contradictory", [False, True])
def test_arm_preserves_mixed_group_support_and_checks_each_class(
    side: int, contradictory: bool
) -> None:
    frozen, arm = arm_case(side)
    second = replace(
        frozen.sources[1],
        asset_id="second",
        group_id="second",
        source_sha256="8" * 64,
        truth_sha256="9" * 64,
    )
    frozen = replace(
        frozen,
        sources=(*frozen.sources, second),
        class_mirror_safety=(*frozen.class_mirror_safety, ("other", True)),
    )
    request = frozen.request(arm.cell, arm.training.request.run_id)
    arm = replace(
        arm, training=replace(arm.training, request=request, resolved_arguments=request.arguments())
    )
    report = arm.evaluation
    supported = replace(report.overall.per_class["object"], support=2)
    absent = replace(
        supported,
        supported=False,
        support=0,
        predictions=0,
        true_positives=0,
        false_positives=0,
        map50=None,
        map50_95=None,
        precision=None,
        recall=None,
    )
    left = replace(report.overall, support=2, per_class={"object": supported, "other": absent})
    right = replace(left, per_class={"object": absent, "other": supported})
    aggregate = replace(
        left,
        images=2,
        support=4,
        per_class={
            "object": replace(supported, support=3) if contradictory else supported,
            "other": replace(supported, support=1) if contradictory else supported,
        },
    )
    second_report = replace(
        report,
        overall=right,
        slices={"small": right},
        coverage=(replace(report.coverage[0], asset_id=second.asset_id),),
        source_truth_sha256=((second.asset_id, second.truth_sha256),),
    )
    arm = replace(
        arm,
        manifest_sha256=frozen.digest(),
        evaluation=replace(
            report,
            overall=aggregate,
            slices={},
            coverage=(*report.coverage, *second_report.coverage),
            source_truth_sha256=(*report.source_truth_sha256, *second_report.source_truth_sha256),
        ),
        group_reports=(
            ("dev", replace(report, overall=left, slices={"small": left})),
            ("second", second_report),
        ),
    )
    if contradictory:
        with pytest.raises(ValueError, match="aggregate class support differs"):
            validate_arm(frozen, arm)
    else:
        validate_arm(frozen, arm)


@pytest.mark.parametrize("where", ["aggregate", "group"])
def test_arm_bounds_slice_class_support_by_its_report(where: str) -> None:
    frozen, arm = arm_case()
    frozen = replace(frozen, critical_slices=("small",), minimum_slice_support=2)
    report = arm.evaluation
    inflated = replace(
        report.overall,
        support=2,
        per_class={"object": replace(report.overall.per_class["object"], support=2)},
    )
    changed = replace(report, slices={"small": inflated})
    arm = replace(arm, manifest_sha256=frozen.digest(), evaluation=changed)
    if where == "group":
        arm = replace(
            arm,
            evaluation=replace(report, overall=inflated, slices={"small": inflated}),
            group_reports=(("dev", changed),),
        )
    with pytest.raises(ValueError, match="slice class support exceeds"):
        validate_arm(frozen, arm)


def test_arm_keeps_training_evidence_gate() -> None:
    frozen, arm = arm_case()
    with pytest.raises(ValueError, match="incomplete dataloader"):
        validate_arm(frozen, replace(arm, retention=()))
