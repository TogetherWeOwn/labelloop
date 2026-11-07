from dataclasses import asdict, replace
from typing import Any

import numpy as np
import pytest
from pycocotools.coco import COCO  # type: ignore[import-untyped]
from pycocotools.cocoeval import COCOeval  # type: ignore[import-untyped]

from labelloop_core.evaluation import (
    DerivedPredictions,
    Detection,
    EvaluationImage,
    EvaluationReport,
    EvaluationRunner,
    GroundTruth,
    score_predictions,
)
from labelloop_core.geometry import Box, Crop, full_frame_transform
from labelloop_core.training import InferencePolicy


def evaluate(
    images: tuple[EvaluationImage, ...],
    derived: tuple[DerivedPredictions, ...],
    *,
    side: int = 320,
    classes: tuple[str, ...] = ("a", "absent"),
) -> EvaluationReport:
    return EvaluationRunner(classes, InferencePolicy(side)).evaluate(
        run_id="eval-1",
        dataset_release_id="data-1",
        recipe_id=f"full-letterbox-{side}-v1",
        weights_sha256="a" * 64,
        class_map_sha256="b" * 64,
        role="development",
        images=images,
        derived=derived,
    )


@pytest.mark.parametrize("side", [320, 640])
def test_tile_duplicates_count_once_and_objects_outside_crops_are_still_missed(side: int) -> None:
    images = (
        EvaluationImage(
            "source",
            100,
            50,
            (
                GroundTruth("one", "a", Box(10, 10, 20, 20)),
                GroundTruth("two", "a", Box(80, 10, 90, 20)),
            ),
        ),
    )
    transform = full_frame_transform(100, 50, side, crop=Crop(0, 0, 50, 50))
    box = transform.apply(images[0].objects[0].box)
    assert box is not None
    sample = DerivedPredictions("source", transform, (Detection("a", box, 0.9),))
    report = evaluate(images, (sample, sample), side=side)
    assert report.overall.support == 2
    assert report.overall.recall == 0.5
    assert report.overall.per_class["a"].predictions == 1
    assert report.overall.map50 == pytest.approx(51 / 101)
    assert report.coverage[0].covered_fraction == 0.5
    assert report.coverage[0].uncovered_targets == 1
    assert report.slices["outside_coverage"].support == 1
    assert report.slices["outside_coverage"].recall == 0
    assert not report.overall.per_class["absent"].supported
    assert report.overall.per_class["absent"].map50_95 is None


def test_padding_is_clipped_before_inverse_mapping_and_false_positives_on_negatives_count() -> None:
    transform = full_frame_transform(200, 100, 320)
    images = (
        EvaluationImage("positive", 200, 100, (GroundTruth("one", "a", Box(0, 0, 20, 20)),)),
        EvaluationImage("negative", 200, 100, ()),
    )
    # Top padding ends at y=80; the first box contains only padding, the second clips to y=0.
    sample = DerivedPredictions(
        "positive",
        transform,
        (Detection("a", Box(0, 0, 10, 30), 0.99), Detection("a", Box(0, 50, 32, 112), 0.9)),
    )
    negative = DerivedPredictions(
        "negative", transform, (Detection("a", Box(160, 100, 200, 140), 0.8),)
    )
    report = evaluate(images, (sample, negative))
    assert report.overall.recall == 1
    assert report.overall.precision == 0.5
    assert report.slices["negative_images"].images == 1
    assert report.slices["negative_images"].per_class["a"].false_positives == 1
    assert report.slices["negative_images"].map50 is None


def test_slices_track_source_scale_derived_scale_occlusion_and_truncation() -> None:
    image = EvaluationImage(
        "source",
        1000,
        1000,
        (GroundTruth("tiny", "a", Box(0, 0, 10, 10), occluded=True, truncated=True),),
    )
    transform = full_frame_transform(1000, 1000, 320)
    report = evaluate((image,), (DerivedPredictions("source", transform, ()),))
    for slice_id in ("source_small", "derived_at_or_below_6px", "occluded", "truncated"):
        assert report.slices[slice_id].support == 1
        assert report.slices[slice_id].recall == 0
    assert report.slices["source_large"].map50 is None


def test_frozen_truth_digest_includes_complete_source_truth_independent_of_object_order() -> None:
    image = EvaluationImage(
        "source",
        100,
        100,
        (
            GroundTruth("one", "a", Box(0, 0, 10, 10)),
            GroundTruth("outside-crop", "b", Box(80, 80, 90, 90), True, True),
        ),
        revision=2,
        source_sha256="a" * 64,
    )
    assert image.truth_digest() == replace(image, objects=image.objects[::-1]).truth_digest()
    for changed in (
        replace(image, asset_id="other"),
        replace(image, revision=1),
        replace(image, source_sha256="b" * 64),
        replace(image, width=101),
        replace(image, height=101),
        replace(image, objects=image.objects[:1]),
    ):
        assert changed.truth_digest() != image.truth_digest()
    for changed in (replace(image, revision=None), replace(image, source_sha256=None)):
        with pytest.raises(ValueError, match="requires revision and source digest"):
            changed.truth_digest()


def test_excluded_source_remains_in_evaluation_denominator() -> None:
    image = EvaluationImage("excluded", 100, 100, (GroundTruth("one", "a", Box(0, 0, 10, 10)),))
    report = evaluate((image,), ())
    assert report.overall.recall == 0
    assert report.coverage[0].covered_fraction == 0
    assert report.coverage[0].uncovered_targets == 1


def test_unknown_prediction_source_class_and_transform_fail_closed() -> None:
    image = EvaluationImage("source", 100, 100, ())
    sample = DerivedPredictions("other", full_frame_transform(100, 100, 320), ())
    with pytest.raises(ValueError, match="outside the frozen evaluation"):
        evaluate((image,), (sample,))
    with pytest.raises(ValueError, match="source/recipe dimensions"):
        evaluate((image,), (replace(sample, asset_id="source"),), side=640)
    with pytest.raises(ValueError, match="prediction class"):
        evaluate(
            (image,),
            (
                replace(
                    sample,
                    asset_id="source",
                    detections=(Detection("unknown", Box(0, 0, 10, 10), 0.5),),
                ),
            ),
        )
    with pytest.raises(ValueError, match="ground truth class"):
        evaluate((replace(image, objects=(GroundTruth("one", "unknown", Box(0, 0, 10, 10)),)),), ())


def _coco_metrics(
    images: tuple[EvaluationImage, ...],
    predictions: dict[str, tuple[Detection, ...]],
) -> tuple[float, float]:
    annotations: list[dict[str, Any]] = []
    detected: list[dict[str, Any]] = []
    for index, image in enumerate(images, 1):
        for obj in image.objects:
            annotations.append(
                {
                    "id": len(annotations) + 1,
                    "image_id": index,
                    "category_id": 1 if obj.class_id == "a" else 2,
                    "bbox": [obj.box.x_min, obj.box.y_min, obj.box.width, obj.box.height],
                    "area": obj.box.area,
                    "iscrowd": 0,
                }
            )
        for item in predictions[image.asset_id]:
            detected.append(
                {
                    "image_id": index,
                    "category_id": 1 if item.class_id == "a" else 2,
                    "bbox": [item.box.x_min, item.box.y_min, item.box.width, item.box.height],
                    "score": item.score,
                }
            )
    coco = COCO()
    coco.dataset = {
        "images": [{"id": i} for i in range(1, len(images) + 1)],
        "annotations": annotations,
        "categories": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
        "info": {},
    }
    coco.createIndex()
    evaluator = COCOeval(coco, coco.loadRes(detected), "bbox")
    evaluator.params.maxDets = [1, 10, 300]
    evaluator.evaluate()
    evaluator.accumulate()
    precision = evaluator.eval["precision"][:, :, :, 0, -1]
    ap50 = precision[0]
    return float(np.mean(ap50[ap50 > -1])), float(np.mean(precision[precision > -1]))


@pytest.mark.parametrize("case", ["perfect", "missed", "duplicate", "localization", "negative"])
def test_coco_style_metrics_match_pycocotools_golden_cases(case: str) -> None:
    images = (
        EvaluationImage(
            "first",
            320,
            320,
            (
                GroundTruth("one", "a", Box(10, 10, 30, 30)),
                GroundTruth("two", "a", Box(100, 100, 150, 150)),
                GroundTruth("three", "b", Box(200, 200, 250, 250)),
            ),
        ),
        EvaluationImage("negative", 320, 320, ()),
    )
    detections = tuple(
        Detection(obj.class_id, obj.box, 0.9 - index * 0.1)
        for index, obj in enumerate(images[0].objects)
    )
    negative: tuple[Detection, ...] = ()
    if case == "missed":
        detections = detections[:1]
    elif case == "duplicate":
        detections = (detections[0], replace(detections[0], score=0.85), *detections[1:])
    elif case == "localization":
        detections = (replace(detections[0], box=Box(12, 10, 32, 30)), *detections[1:])
    elif case == "negative":
        negative = (Detection("a", Box(0, 0, 10, 10), 0.99),)
    predictions = {"first": detections, "negative": negative}
    ap50, ap = _coco_metrics(images, predictions)
    actual = score_predictions(images, predictions, ("a", "b", "absent"), InferencePolicy(320))
    assert actual.map50 == pytest.approx(ap50, abs=1e-12)
    assert actual.map50_95 == pytest.approx(ap, abs=1e-12)
    assert actual.per_class["absent"].map50 is None
    assert asdict(actual)["support"] == 3
