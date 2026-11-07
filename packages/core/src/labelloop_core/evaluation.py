"""Original-frame, non-crowd detection metrics independent of vendor val output."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from statistics import mean

from .geometry import AffineTransform, Box
from .training import EvaluationRole, InferencePolicy, require_digest

IOU_THRESHOLDS = tuple(0.5 + index * 0.05 for index in range(10))


@dataclass(frozen=True, slots=True)
class GroundTruth:
    object_id: str
    class_id: str
    box: Box
    occluded: bool = False
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class EvaluationImage:
    asset_id: str
    width: int
    height: int
    objects: tuple[GroundTruth, ...]

    def __post_init__(self) -> None:
        if not self.asset_id or min(self.width, self.height) < 1:
            raise ValueError("evaluation images require identity and dimensions")
        if len({item.object_id for item in self.objects}) != len(self.objects):
            raise ValueError("evaluation object identities must be unique per image")
        for obj in self.objects:
            obj.box.within(self.width, self.height)


@dataclass(frozen=True, slots=True)
class Detection:
    class_id: str
    box: Box
    score: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.score) or not 0 <= self.score <= 1:
            raise ValueError("detection scores must be finite probabilities")


@dataclass(frozen=True, slots=True)
class DerivedPredictions:
    asset_id: str
    transform: AffineTransform
    detections: tuple[Detection, ...]


@dataclass(frozen=True, slots=True)
class ClassMetrics:
    supported: bool
    support: int
    predictions: int
    true_positives: int
    false_positives: int
    map50: float | None
    map50_95: float | None
    precision: float | None
    recall: float | None


@dataclass(frozen=True, slots=True)
class Metrics:
    images: int
    support: int
    map50: float | None
    map50_95: float | None
    precision: float | None
    recall: float | None
    per_class: dict[str, ClassMetrics]


@dataclass(frozen=True, slots=True)
class Coverage:
    asset_id: str
    covered_fraction: float
    uncovered_targets: int
    partially_covered_targets: int


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    run_id: str
    dataset_release_id: str
    recipe_id: str
    weights_sha256: str
    class_map_sha256: str
    role: EvaluationRole
    policy: InferencePolicy
    overall: Metrics
    slices: dict[str, Metrics]
    coverage: tuple[Coverage, ...]


def iou(left: Box, right: Box) -> float:
    overlap = left.intersect(right)
    return 0.0 if overlap is None else overlap.area / (left.area + right.area - overlap.area)


def merge_detections(
    detections: tuple[Detection, ...], policy: InferencePolicy
) -> tuple[Detection, ...]:
    """Stable source-frame NMS also merges duplicates across application tiles."""
    retained: list[Detection] = []
    for detection in sorted(detections, key=lambda item: -item.score):
        if any(
            (policy.agnostic_nms or previous.class_id == detection.class_id)
            and iou(previous.box, detection.box) > policy.iou
            for previous in retained
        ):
            continue
        retained.append(detection)
        if len(retained) == policy.max_det:
            break
    return tuple(retained)


def _union_area(rectangles: tuple[Box, ...]) -> float:
    edges = sorted({edge for box in rectangles for edge in (box.x_min, box.x_max)})
    area = 0.0
    for left, right in zip(edges, edges[1:], strict=False):
        intervals = sorted(
            (box.y_min, box.y_max) for box in rectangles if box.x_min < right and box.x_max > left
        )
        bottom = 0.0
        covered = 0.0
        for start, end in intervals:
            covered += max(0.0, end - max(start, bottom))
            bottom = max(bottom, end)
        area += (right - left) * covered
    return area


Selector = Callable[[EvaluationImage, GroundTruth], bool]


def _matches(
    images: tuple[EvaluationImage, ...],
    predictions: dict[str, tuple[Detection, ...]],
    class_id: str,
    threshold: float,
    selected: Selector,
    max_det: int,
) -> list[tuple[float, bool]]:
    matches: list[tuple[float, bool]] = []
    for image in images:
        objects = [obj for obj in image.objects if obj.class_id == class_id]
        used: set[int] = set()
        candidates = sorted(
            (item for item in predictions[image.asset_id] if item.class_id == class_id),
            key=lambda item: -item.score,
        )[:max_det]
        for detection in candidates:
            # COCO prefers a regular GT over an ignored GT, even at a lower valid IoU.
            options = [
                (selected(image, obj), iou(detection.box, obj.box), index)
                for index, obj in enumerate(objects)
                if index not in used and iou(detection.box, obj.box) >= threshold
            ]
            if options:
                included, _, index = max(options)
                used.add(index)
                if included:
                    matches.append((detection.score, True))
            else:
                matches.append((detection.score, False))
    return sorted(matches, key=lambda item: -item[0])


def _average_precision(matches: list[tuple[float, bool]], support: int) -> float:
    if not matches:
        return 0.0
    true_positives = 0
    recalls: list[float] = []
    precisions: list[float] = []
    for index, (_, matched) in enumerate(matches, 1):
        true_positives += matched
        recalls.append(true_positives / support)
        precisions.append(true_positives / index)
    for index in range(len(precisions) - 2, -1, -1):
        precisions[index] = max(precisions[index], precisions[index + 1])
    return (
        sum(
            next(
                (
                    precision
                    for recall, precision in zip(recalls, precisions, strict=True)
                    if recall >= r
                ),
                0.0,
            )
            for r in (index / 100 for index in range(101))
        )
        / 101
    )


def score_predictions(
    images: tuple[EvaluationImage, ...],
    predictions: dict[str, tuple[Detection, ...]],
    class_ids: tuple[str, ...],
    policy: InferencePolicy,
    *,
    selected: Selector = lambda _image, _obj: True,
) -> Metrics:
    per_class: dict[str, ClassMetrics] = {}
    for class_id in class_ids:
        support = sum(
            obj.class_id == class_id and selected(image, obj)
            for image in images
            for obj in image.objects
        )
        matches = [
            _matches(images, predictions, class_id, threshold, selected, policy.max_det)
            for threshold in IOU_THRESHOLDS
        ]
        operating = [matched for score, matched in matches[0] if score >= policy.conf]
        tp = sum(operating)
        count = len(operating)
        aps = [_average_precision(result, support) for result in matches] if support else []
        per_class[class_id] = ClassMetrics(
            supported=support > 0,
            support=support,
            predictions=count,
            true_positives=tp,
            false_positives=count - tp,
            map50=aps[0] if aps else None,
            map50_95=mean(aps) if aps else None,
            precision=tp / count if count else None,
            recall=tp / support if support else None,
        )
    support = sum(item.support for item in per_class.values())
    tp = sum(item.true_positives for item in per_class.values())
    count = sum(item.predictions for item in per_class.values())
    ap50 = [item.map50 for item in per_class.values() if item.map50 is not None]
    ap = [item.map50_95 for item in per_class.values() if item.map50_95 is not None]
    return Metrics(
        images=len(images),
        support=support,
        map50=mean(ap50) if ap50 else None,
        map50_95=mean(ap) if ap else None,
        precision=tp / count if count else None,
        recall=tp / support if support else None,
        per_class=per_class,
    )


class EvaluationRunner:
    def __init__(self, class_ids: tuple[str, ...], policy: InferencePolicy) -> None:
        if not class_ids or len(set(class_ids)) != len(class_ids) or not all(class_ids):
            raise ValueError("evaluation requires a unique nonempty class catalog")
        self.class_ids = class_ids
        self.policy = policy

    def evaluate(
        self,
        *,
        run_id: str,
        dataset_release_id: str,
        recipe_id: str,
        weights_sha256: str,
        class_map_sha256: str,
        role: EvaluationRole,
        images: tuple[EvaluationImage, ...],
        derived: tuple[DerivedPredictions, ...],
    ) -> EvaluationReport:
        if not run_id or not dataset_release_id or not recipe_id or not images:
            raise ValueError("evaluation requires identified, nonempty frozen source membership")
        if role not in {"development", "calibration", "locked_final_test"}:
            raise ValueError("unknown evaluation role")
        require_digest(weights_sha256)
        require_digest(class_map_sha256)
        sources = {image.asset_id: image for image in images}
        if len(sources) != len(images):
            raise ValueError("duplicate evaluation source")
        grouped: dict[str, list[Detection]] = {identifier: [] for identifier in sources}
        transforms: dict[str, list[AffineTransform]] = {identifier: [] for identifier in sources}
        for image in images:
            if any(obj.class_id not in self.class_ids for obj in image.objects):
                raise ValueError("ground truth class outside the frozen catalog")
        for sample in derived:
            if sample.asset_id not in sources:
                raise ValueError("prediction source outside the frozen evaluation release")
            transform = sample.transform
            image = sources[sample.asset_id]
            if (transform.source_width, transform.source_height, transform.output_size) != (
                image.width,
                image.height,
                self.policy.imgsz,
            ):
                raise ValueError("prediction transform disagrees with source/recipe dimensions")
            transforms[sample.asset_id].append(transform)
            for detection in sample.detections:
                if detection.class_id not in self.class_ids:
                    raise ValueError("prediction class outside the frozen catalog")
                detection.box.within(transform.output_size, transform.output_size)
                restored = transform.restore(detection.box)
                if restored is not None:
                    restored = restored.clip(image.width, image.height)
                    if restored is not None:
                        grouped[sample.asset_id].append(
                            Detection(detection.class_id, restored, detection.score)
                        )
        predictions = {
            key: merge_detections(tuple(value), self.policy) for key, value in grouped.items()
        }
        coverage: list[Coverage] = []
        for image in images:
            crops = tuple(transform.crop.box for transform in transforms[image.asset_id])
            uncovered = partial = 0
            for obj in image.objects:
                intersections = tuple(
                    intersection
                    for crop in crops
                    if (intersection := crop.intersect(obj.box)) is not None
                )
                area = _union_area(intersections)
                uncovered += area == 0
                partial += 0 < area < obj.box.area - 1e-8
            coverage.append(
                Coverage(
                    image.asset_id,
                    _union_area(crops) / (image.width * image.height),
                    uncovered,
                    partial,
                )
            )

        def derived_size(image: EvaluationImage, obj: GroundTruth) -> float:
            boxes = [
                box
                for transform in transforms[image.asset_id]
                if (box := transform.apply(obj.box)) is not None
            ]
            return max((min(box.width, box.height) for box in boxes), default=0.0)

        selectors: dict[str, Selector] = {
            "occluded": lambda _image, obj: obj.occluded,
            "truncated": lambda _image, obj: obj.truncated,
            "source_small": lambda _image, obj: obj.box.area < 32**2,
            "source_medium": lambda _image, obj: 32**2 <= obj.box.area < 96**2,
            "source_large": lambda _image, obj: obj.box.area >= 96**2,
            "derived_at_or_below_6px": lambda image, obj: 0 < derived_size(image, obj) <= 6,
            "outside_coverage": lambda image, obj: derived_size(image, obj) == 0,
        }
        slices = {
            name: score_predictions(
                images, predictions, self.class_ids, self.policy, selected=select
            )
            for name, select in selectors.items()
        }
        negatives = tuple(image for image in images if not image.objects)
        slices["negative_images"] = score_predictions(
            negatives, predictions, self.class_ids, self.policy
        )
        return EvaluationReport(
            run_id,
            dataset_release_id,
            recipe_id,
            weights_sha256,
            class_map_sha256,
            role,
            self.policy,
            score_predictions(images, predictions, self.class_ids, self.policy),
            slices,
            tuple(coverage),
        )
