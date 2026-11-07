"""Vendor-independent local training and fixed-pipeline release contracts."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

NMSMode = Literal["external", "end2end"]
EvaluationRole = Literal["development", "calibration", "locked_final_test"]
ULTRALYTICS_VERSION = "8.4.204"


def require_digest(value: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("expected a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class InferencePolicy:
    imgsz: int
    nms: NMSMode = "external"
    iou: float = 0.7
    conf: float = 0.25
    max_det: int = 300
    agnostic_nms: bool = False

    def __post_init__(self) -> None:
        if self.imgsz not in {320, 640}:
            raise ValueError("v1 controlled recipes require 320 or 640")
        if self.nms not in {"external", "end2end"}:
            raise ValueError("unknown NMS mode")
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in (self.iou, self.conf)):
            raise ValueError("postprocessing thresholds must be finite probabilities")
        if self.max_det < 1:
            raise ValueError("max_det must be positive")

    def arguments(self) -> dict[str, int | float | bool]:
        return {
            "imgsz": self.imgsz,
            "rect": False,
            "tile": False,
            "nms": self.nms == "external",
            "iou": self.iou,
            "conf": self.conf,
            "max_det": self.max_det,
            "agnostic_nms": self.agnostic_nms,
        }

    def assert_shape(self, shape: tuple[int, ...], *, stride: int) -> None:
        if stride < 1 or self.imgsz % stride:
            raise ValueError("recipe size must be divisible by the actual model stride")
        if len(shape) != 4 or shape[0] < 1 or shape[1:] != (3, self.imgsz, self.imgsz):
            raise ValueError(f"actual tensor shape {shape} violates the recipe")


@dataclass(frozen=True, slots=True)
class TrainingRequest:
    run_id: str
    project_id: str
    recipe_id: str
    dataset_release_id: str
    dataset_manifest_sha256: str
    class_map_sha256: str
    split_assignment_version: str
    initialization_sha256: str
    policy: InferencePolicy
    epochs: int = 1
    batch: int = 2
    seed: int = 0
    validation_role: EvaluationRole = "development"

    def __post_init__(self) -> None:
        if not all(
            (
                self.run_id,
                self.project_id,
                self.recipe_id,
                self.dataset_release_id,
                self.split_assignment_version,
            )
        ):
            raise ValueError("training lineage must be fully identified")
        for value in (
            self.dataset_manifest_sha256,
            self.class_map_sha256,
            self.initialization_sha256,
        ):
            require_digest(value)
        if self.epochs < 1 or self.batch < 1:
            raise ValueError("epochs and batch must be positive")
        if self.validation_role != "development":
            raise ValueError(
                "checkpoint selection requires development validation, never final test"
            )

    def arguments(self) -> dict[str, int | float | bool | str]:
        """Pin the baseline; worker-owned paths cannot be selected by model output."""
        return {
            **self.policy.arguments(),
            "epochs": self.epochs,
            "batch": self.batch,
            "seed": self.seed,
            "device": "cpu",
            "amp": False,
            "workers": 0,
            "deterministic": True,
            "multi_scale": 0.0,
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
            "save": True,
            "save_period": 1,
            "plots": False,
        }


def argument_diff(
    requested: Mapping[str, object], resolved: Mapping[str, object]
) -> dict[str, tuple[object, object]]:
    """Missing keys and type changes are mismatches, not vendor defaults to accept."""
    return {
        key: (expected, resolved.get(key))
        for key, expected in requested.items()
        if key not in resolved
        or type(expected) is not type(resolved[key])
        or resolved[key] != expected
    }


@dataclass(frozen=True, slots=True)
class Checkpoint:
    epoch: int
    sha256: str
    validation_map: float
    evaluation_run_id: str
    role: EvaluationRole = "development"

    def __post_init__(self) -> None:
        require_digest(self.sha256)
        if self.epoch < 0 or not self.evaluation_run_id:
            raise ValueError("checkpoint needs epoch and evaluation lineage")
        if not math.isfinite(self.validation_map) or not 0 <= self.validation_map <= 1:
            raise ValueError("checkpoint score must be a finite probability")
        if self.role != "development":
            raise ValueError("checkpoint selection cannot use a locked final test or calibration")


def select_checkpoint(checkpoints: tuple[Checkpoint, ...]) -> Checkpoint:
    if not checkpoints:
        raise ValueError("no fixed-shape checkpoints available")
    if len({item.epoch for item in checkpoints}) != len(checkpoints):
        raise ValueError("checkpoint epochs must be unique")
    return max(checkpoints, key=lambda item: (item.validation_map, -item.epoch))


@dataclass(frozen=True, slots=True)
class TrainingResult:
    request: TrainingRequest
    checkpoints: tuple[Checkpoint, ...]
    selected: Checkpoint
    resolved_arguments: Mapping[str, object]
    ultralytics_version: str
    torch_version: str
    albumentations_installed: bool

    def __post_init__(self) -> None:
        if self.ultralytics_version != ULTRALYTICS_VERSION or not self.torch_version:
            raise ValueError("training environment does not match the pinned contract")
        if self.albumentations_installed:
            raise ValueError("baseline forbids implicit Albumentations transforms")
        if argument_diff(self.request.arguments(), self.resolved_arguments):
            raise ValueError("args_mismatch")
        if self.selected != select_checkpoint(self.checkpoints):
            raise ValueError("selected checkpoint disagrees with fixed-shape validation")


ResultT_co = TypeVar("ResultT_co", covariant=True)


class TrainingBackend(Protocol[ResultT_co]):
    """Cloud progress must not masquerade as a fixed-shape local TrainingResult."""

    def train(self, request: TrainingRequest) -> ResultT_co: ...
