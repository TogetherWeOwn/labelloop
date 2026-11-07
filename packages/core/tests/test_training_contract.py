from dataclasses import replace

import pytest

from labelloop_core.training import (
    Checkpoint,
    InferencePolicy,
    TrainingRequest,
    TrainingResult,
    argument_diff,
    select_checkpoint,
)


def request(side: int = 320) -> TrainingRequest:
    return TrainingRequest(
        run_id="run-1",
        project_id="project-1",
        recipe_id=f"full-letterbox-{side}-v1",
        dataset_release_id="data-1",
        dataset_manifest_sha256="a" * 64,
        class_map_sha256="b" * 64,
        split_assignment_version="splits-1",
        initialization_sha256="c" * 64,
        policy=InferencePolicy(side),
    )


@pytest.mark.parametrize("side", [320, 640])
def test_every_baseline_augmentation_and_pipeline_control_is_explicit(side: int) -> None:
    args = request(side).arguments()
    expected = {
        "imgsz": side,
        "rect": False,
        "multi_scale": 0.0,
        "tile": False,
        "nms": True,
        "mosaic": 0.0,
        "mixup": 0.0,
        "cutmix": 0.0,
        "close_mosaic": 0,
        "scale": 0.0,
        "translate": 0.0,
        "degrees": 0.0,
        "shear": 0.0,
        "perspective": 0.0,
        "fliplr": 0.0,
        "flipud": 0.0,
        "hsv_h": 0.015,
        "hsv_s": 0.7,
        "hsv_v": 0.4,
        "erasing": 0.0,
        "save_period": 1,
        "seed": 0,
        "deterministic": True,
    }
    assert all(key in args and args[key] == value for key, value in expected.items())
    policy = request(side).policy
    assert all(args[key] == value for key, value in policy.arguments().items())
    assert replace(policy, nms="end2end").arguments()["nms"] is False


def test_missing_changed_and_type_coerced_resolved_args_fail_closed() -> None:
    args: dict[str, object] = dict(request().arguments())
    args.pop("tile")
    args["nms"] = False
    args["scale"] = 0
    diff = argument_diff(request().arguments(), args)
    assert set(diff) == {"tile", "nms", "scale"}
    assert diff["tile"] == (False, None)


@pytest.mark.parametrize("side", [320, 640])
def test_actual_tensor_shape_and_stride_are_asserted(side: int) -> None:
    policy = InferencePolicy(side)
    policy.assert_shape((2, 3, side, side), stride=32)
    for shape in ((1, 3, side + 32, side + 32), (1, 3, side, side // 2), (0, 3, side, side)):
        with pytest.raises(ValueError, match="tensor shape"):
            policy.assert_shape(shape, stride=32)
    with pytest.raises(ValueError, match="stride"):
        policy.assert_shape((1, 3, side, side), stride=96)


def test_checkpoint_selection_uses_only_development_evidence_and_stable_ties() -> None:
    first = Checkpoint(0, "a" * 64, 0.5, "evaluation-0")
    second = Checkpoint(1, "b" * 64, 0.5, "evaluation-1")
    assert select_checkpoint((second, first)) == first
    with pytest.raises(ValueError, match="no fixed-shape"):
        select_checkpoint(())
    with pytest.raises(ValueError, match="unique"):
        select_checkpoint((first, first))
    with pytest.raises(ValueError, match="final test"):
        replace(first, role="locked_final_test")
    with pytest.raises(ValueError, match="final test"):
        replace(request(), validation_role="locked_final_test")
    with pytest.raises(ValueError, match="final test"):
        replace(request(), validation_role="calibration")


def test_result_requires_matching_resolved_args_and_environment() -> None:
    run = request()
    checkpoint = Checkpoint(0, "a" * 64, 0.5, "evaluation-0")
    result = TrainingResult(
        run, (checkpoint,), checkpoint, run.arguments(), "8.4.204", "2.9.1+cpu", False
    )
    with pytest.raises(ValueError, match="args_mismatch"):
        replace(result, resolved_arguments={})
    with pytest.raises(ValueError, match="Albumentations"):
        replace(result, albumentations_installed=True)
    with pytest.raises(ValueError, match="environment"):
        replace(result, ultralytics_version="latest")
    with pytest.raises(ValueError, match="selected checkpoint"):
        replace(result, selected=replace(checkpoint, epoch=1))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_nonfinite_and_out_of_range_thresholds_are_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="thresholds"):
        InferencePolicy(320, conf=value)
    with pytest.raises(ValueError, match="score"):
        Checkpoint(0, "a" * 64, value, "eval")
