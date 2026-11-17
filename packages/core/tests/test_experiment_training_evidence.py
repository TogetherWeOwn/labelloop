from dataclasses import replace
from typing import Any

import pytest
from test_experiment_reports import report_case

from labelloop_core.experiments import (
    ArmEvidence,
    ExperimentManifest,
    RetentionObservation,
    validate_training_evidence,
)


def arm_case(side: int = 320) -> tuple[ExperimentManifest, ArmEvidence]:
    frozen, training, report = report_case(side)
    arm = ArmEvidence(
        frozen.digest(),
        next(c for c in frozen.cells() if c.recipe_id == f"r{side}"),
        training,
        report,
        (("dev", report),),
        (RetentionObservation("train", 1, 1, 1, 1),),
        tuple((stage, ((1, 3, side, side),)) for stage in ("training", "validation", "inference")),
        "a" * 64,
        training.selected.sha256,
        (1, 3, side, side),
        training.request.policy,
        1,
        1,
    )
    return frozen, arm


@pytest.mark.parametrize("side", [320, 640])
def test_training_evidence_accepts_frozen_recipes_and_negative_samples(side: int) -> None:
    frozen, arm = arm_case(side)
    validate_training_evidence(frozen, arm)
    validate_training_evidence(
        frozen, replace(arm, retention=(RetentionObservation("train", 0, 0, 0, 0),))
    )


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("visits", [1, 2, 3])
def test_training_evidence_requires_every_multi_epoch_visit(side: int, visits: int) -> None:
    frozen, arm = arm_case(side)
    frozen = replace(frozen, epochs=2)
    request = frozen.request(arm.cell, arm.training.request.run_id)
    training = replace(
        arm.training,
        request=request,
        resolved_arguments=request.arguments(),
        checkpoints=tuple(replace(arm.training.selected, epoch=i) for i in range(request.epochs)),
    )
    arm = replace(
        arm, manifest_sha256=frozen.digest(), training=training, retention=arm.retention * visits
    )
    if visits == frozen.epochs:
        validate_training_evidence(frozen, arm)
        lost = replace(arm.retention[-1], after=0)
        with pytest.raises(ValueError, match="lost targets"):
            validate_training_evidence(frozen, replace(arm, retention=(*arm.retention[:-1], lost)))
    else:
        with pytest.raises(ValueError, match="incomplete dataloader"):
            validate_training_evidence(frozen, arm)


def test_training_evidence_cannot_substitute_another_assets_visits() -> None:
    frozen, arm = arm_case()
    source = replace(frozen.sources[0], asset_id="second-train")
    datasets = tuple(
        replace(d, training_asset_ids=(*d.training_asset_ids, source.asset_id))
        for d in frozen.datasets
    )
    frozen = replace(frozen, sources=(*frozen.sources, source), datasets=datasets, epochs=2)
    request = frozen.request(arm.cell, arm.training.request.run_id)
    training = replace(
        arm.training,
        request=request,
        resolved_arguments=request.arguments(),
        checkpoints=tuple(replace(arm.training.selected, epoch=i) for i in range(request.epochs)),
    )
    first = arm.retention[0]
    second = replace(first, asset_id=source.asset_id)
    arm = replace(
        arm, manifest_sha256=frozen.digest(), training=training, retention=(first, second) * 2
    )
    validate_training_evidence(frozen, arm)
    with pytest.raises(ValueError, match="incomplete dataloader"):
        validate_training_evidence(frozen, replace(arm, retention=(first, second, first, first)))


@pytest.mark.parametrize(
    "changes",
    [
        {"manifest_sha256": "b" * 64},
        {"export_sha256": "invalid"},
        {"export_weights_sha256": "b" * 64},
        {"export_signature": (1, 3, 640, 640)},
        {"export_signature": (True, 3, 320, 320)},
        *(
            {field: value}
            for field in ("export_samples", "export_detections")
            for value in (0, -1, True, 0.5, float("nan"))
        ),
    ],
)
def test_training_evidence_rejects_manifest_and_export_drift(changes: dict[str, Any]) -> None:
    frozen, arm = arm_case()
    with pytest.raises(ValueError):
        validate_training_evidence(frozen, replace(arm, **changes))


@pytest.mark.parametrize(
    "observations",
    [
        (),
        (RetentionObservation("other", 1, 1, 0, 0),),
        (RetentionObservation("train", 1, 1, 0, 0), RetentionObservation("train", 1, 0, 0, 0)),
        (RetentionObservation("train", 1, 0, 0, 0),),
        (RetentionObservation("train", 1, 1, 2, 0),),
        (RetentionObservation("train", 1, 1, 0, 2),),
    ],
)
def test_training_evidence_rejects_incomplete_or_invalid_retention(observations: Any) -> None:
    frozen, arm = arm_case()
    with pytest.raises(ValueError):
        validate_training_evidence(frozen, replace(arm, retention=observations))


@pytest.mark.parametrize("field", ["before", "after", "before_small", "after_small"])
@pytest.mark.parametrize("value", [-1, True, 0.5, float("nan")])
def test_training_evidence_counts_are_nonnegative_integers(field: str, value: Any) -> None:
    frozen, arm = arm_case()
    changes: dict[str, Any] = {field: value}
    observation = replace(arm.retention[0], **changes)
    with pytest.raises(ValueError):
        validate_training_evidence(frozen, replace(arm, retention=(observation,)))


@pytest.mark.parametrize("stage", ["training", "validation", "inference"])
@pytest.mark.parametrize(
    "values",
    [(), ((0, 3, 320, 320),), ((1, 3, 640, 640),), ((True, 3, 320, 320),), ((1.5, 3, 320, 320),)],
)
def test_training_evidence_requires_actual_shapes(stage: str, values: Any) -> None:
    frozen, arm = arm_case()
    shapes = tuple((key, values if key == stage else shapes) for key, shapes in arm.tensor_shapes)
    for changed in (shapes, shapes[:-1], shapes + shapes[:1]):
        with pytest.raises(ValueError):
            validate_training_evidence(frozen, replace(arm, tensor_shapes=changed))


def test_training_evidence_rechecks_request_and_mutable_resolved_arguments() -> None:
    frozen, arm = arm_case()
    request = replace(arm.training.request, epochs=arm.training.request.epochs + 1)
    training = replace(
        arm.training,
        request=request,
        resolved_arguments=request.arguments(),
        checkpoints=tuple(replace(arm.training.selected, epoch=i) for i in range(request.epochs)),
    )
    with pytest.raises(ValueError, match="training request"):
        validate_training_evidence(frozen, replace(arm, training=training))
    resolved = dict(arm.training.resolved_arguments)
    training = replace(arm.training, resolved_arguments=resolved)
    resolved["rect"] = True
    with pytest.raises(ValueError, match="args_mismatch"):
        validate_training_evidence(frozen, replace(arm, training=training))
