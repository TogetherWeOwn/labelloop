from dataclasses import replace

import pytest

from labelloop_core.evaluation import (
    DerivedPredictions,
    EvaluationImage,
    EvaluationReport,
    EvaluationRunner,
    GroundTruth,
)
from labelloop_core.geometry import Box, Crop, full_frame_transform
from labelloop_core.training import InferencePolicy


def evaluate(
    images: tuple[EvaluationImage, ...], side: int, *, cropped: bool = False
) -> EvaluationReport:
    return EvaluationRunner(("object",), InferencePolicy(side)).evaluate(
        run_id="evaluation",
        dataset_release_id="release",
        recipe_id=f"r{side}",
        weights_sha256="a" * 64,
        class_map_sha256="b" * 64,
        role="development",
        images=images,
        derived=tuple(
            DerivedPredictions(
                image.asset_id,
                full_frame_transform(100, 100, side, crop=Crop(0, 0, 50, 50) if cropped else None),
                (),
            )
            for image in images
        ),
    )


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("metadata", ["frozen", "revision-only", "digest-only", "mixed", "legacy"])
def test_truth_pins_require_complete_metadata_for_every_source(side: int, metadata: str) -> None:
    images = tuple(
        EvaluationImage(name, 100, 100, (), revision=2, source_sha256="c" * 64)
        for name in ("z-source", "a-source")
    )
    if metadata in {"digest-only", "legacy"}:
        images = tuple(replace(image, revision=None) for image in images)
    if metadata in {"revision-only", "legacy"}:
        images = tuple(replace(image, source_sha256=None) for image in images)
    if metadata == "mixed":
        images = (images[0], replace(images[1], revision=None))
    report = evaluate(images, side)
    expected = (
        tuple(sorted((image.asset_id, image.truth_digest()) for image in images))
        if metadata == "frozen"
        else ()
    )
    assert report.source_truth_sha256 == expected
    assert evaluate(images[::-1], side).source_truth_sha256 == expected
    assert report.overall.images == 2


def test_truth_pins_bind_complete_original_truth_even_outside_crop() -> None:
    image = EvaluationImage(
        "source",
        100,
        100,
        (GroundTruth("outside", "object", Box(80, 80, 90, 90)),),
        revision=1,
        source_sha256="c" * 64,
    )
    report = evaluate((image,), 320, cropped=True)
    assert report.coverage[0].uncovered_targets == 1
    assert report.source_truth_sha256 == ((image.asset_id, image.truth_digest()),)
    assert evaluate((image,), 640).source_truth_sha256 == report.source_truth_sha256
    for changed in (
        replace(image, revision=2),
        replace(image, source_sha256="d" * 64),
        replace(image, objects=()),
    ):
        assert (
            evaluate((changed,), 320, cropped=True).source_truth_sha256
            != report.source_truth_sha256
        )


@pytest.mark.parametrize("change", [{"revision": 0}, {"source_sha256": "bad"}])
def test_present_but_invalid_truth_metadata_is_not_silently_unpinned(
    change: dict[str, object],
) -> None:
    from typing import Any, cast

    image = EvaluationImage("source", 100, 100, (), revision=1, source_sha256="c" * 64)
    with pytest.raises(ValueError):
        evaluate((replace(image, **cast(Any, change)),), 320)
