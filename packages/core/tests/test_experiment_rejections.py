from dataclasses import replace

import pytest
from test_experiment_complete_matrix import digest, evidence, experiment

from labelloop_core.evaluation import Coverage
from labelloop_core.experiments import RetentionObservation, compare_experiment


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


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
@pytest.mark.parametrize("where", ["overall", "group", "slice"])
def test_nonfinite_per_class_scores_cannot_bypass_regression(value: float, where: str) -> None:
    manifest = experiment()
    arms = list(evidence(manifest))
    arm = arms[0]
    if where == "group":
        report = arm.group_reports[0][1]
    else:
        report = arm.evaluation
    invalid = replace(report.overall.per_class["object"], map50_95=value)
    if where == "slice":
        report = replace(
            report,
            slices={
                "source_small": replace(
                    report.slices["source_small"], per_class={"object": invalid}
                )
            },
        )
    else:
        report = replace(report, overall=replace(report.overall, per_class={"object": invalid}))
    arms[0] = (
        replace(arm, group_reports=((arm.group_reports[0][0], report),) + arm.group_reports[1:])
        if where == "group"
        else replace(arm, evaluation=report)
    )
    with pytest.raises(ValueError, match="nonfinite or invalid"):
        compare_experiment(manifest, tuple(arms))


@pytest.mark.parametrize("where", ["overall", "group"])
def test_report_truth_binding_must_match_frozen_source_digests(where: str) -> None:
    manifest = experiment()
    arms = list(evidence(manifest))
    arm = arms[0]
    report = arm.evaluation if where == "overall" else arm.group_reports[0][1]
    changed = replace(
        report,
        source_truth_sha256=tuple(
            (asset_id, digest("changed-boxes")) for asset_id, _ in report.source_truth_sha256
        ),
    )
    arms[0] = (
        replace(arm, evaluation=changed)
        if where == "overall"
        else replace(
            arm, group_reports=((arm.group_reports[0][0], changed),) + arm.group_reports[1:]
        )
    )
    with pytest.raises(ValueError, match="truth binding"):
        compare_experiment(manifest, tuple(arms))
