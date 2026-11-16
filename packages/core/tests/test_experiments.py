from dataclasses import replace

import pytest

from labelloop_core.experiments import BASELINE
from labelloop_core.training import AugmentationProfile, InferencePolicy, TrainingRequest


@pytest.mark.parametrize(
    "field,value",
    [
        ("fliplr", 0.5),
        ("scale", 0.5),
        ("translate", 0.1),
        ("hsv_s", 0.6),
    ],
)
def test_reserved_baseline_cannot_drift(field: str, value: float) -> None:
    from typing import Any

    change: dict[str, Any] = {field: value}
    with pytest.raises(ValueError, match="baseline augmentation drift"):
        replace(BASELINE, **change)


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf")])
def test_augmentation_bounds(value: float) -> None:
    with pytest.raises(ValueError, match="finite probabilities"):
        AugmentationProfile("invalid", translate=value)


def test_training_flip_requires_unique_mirror_safe_classes() -> None:
    request = TrainingRequest(
        "run",
        "project",
        "recipe",
        "release",
        "a" * 64,
        "b" * 64,
        "splits",
        "c" * 64,
        InferencePolicy(320),
    )
    profile = AugmentationProfile("flip-v1", fliplr=0.5)
    for safety in ((), (("object", False),), (("object", True), ("object", True))):
        with pytest.raises(ValueError, match="mirror_safe"):
            replace(request, augmentation=profile, class_mirror_safety=safety)
    flipped = replace(request, augmentation=profile, class_mirror_safety=(("object", True),))
    assert flipped.arguments()["fliplr"] == 0.5
