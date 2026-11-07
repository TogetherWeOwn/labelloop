"""Immutable sync evidence; wire labels follow the pinned Platform 0.1.107 contract."""

import hashlib
import json
import math
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any

# Independently rounded center and extent can move an edge by 0.0000075.
COORDINATE_TOLERANCE = 0.000005000001
EDGE_TOLERANCE = 0.000007500001


class SyncConflict(ValueError):
    """Evidence diverged or is incomplete; stop and adjudicate, never overwrite."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class Label:
    class_id: int
    bbox: tuple[float, float, float, float] | None = None
    # Keep all non-detection branches, including a bbox accompanying a mask or pose.
    extra_json: str = "{}"

    def __post_init__(self) -> None:
        if type(self.class_id) is not int or self.class_id < 0:
            raise SyncConflict("invalid numeric class index")
        extra = json.loads(self.extra_json)
        if not isinstance(extra, dict) or set(extra) - {
            "segments",
            "keypoints",
            "obb",
            "skeletonId",
        }:
            raise SyncConflict("unsupported label branch")
        for key, value in extra.items():
            if key == "skeletonId":
                if not isinstance(value, str) or len(value) > 128:
                    raise SyncConflict("malformed skeleton identity")
            elif (
                not isinstance(value, list)
                or len(value) > 10000
                or (key == "obb" and len(value) != 8)
                or any(type(v) not in {int, float} or not math.isfinite(v) for v in value)
            ):
                raise SyncConflict("malformed mixed-task label")
        object.__setattr__(self, "extra_json", canonical_json(extra))
        if self.bbox is not None:
            if len(self.bbox) != 4 or any(
                type(v) not in {int, float} or not math.isfinite(v) for v in self.bbox
            ):
                raise SyncConflict("malformed normalized box")
            x, y, w, h = self.bbox
            if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
                raise SyncConflict("invalid normalized box")
            if min(x - w / 2, y - h / 2, 1 - x - w / 2, 1 - y - h / 2) < -EDGE_TOLERANCE:
                raise SyncConflict("normalized box lies outside image")

    @property
    def detection_only(self) -> bool:
        return self.bbox is not None and self.extra_json == "{}"

    @classmethod
    def from_wire(cls, value: dict[str, Any]) -> "Label":
        if "classId" not in value or set(value) - {
            "classId",
            "bbox",
            "segments",
            "keypoints",
            "obb",
            "skeletonId",
        }:
            raise SyncConflict("malformed label")
        bbox = value.get("bbox")
        return cls(
            value["classId"],
            tuple(bbox) if bbox is not None else None,
            canonical_json({k: v for k, v in value.items() if k not in {"classId", "bbox"}}),
        )

    def wire(self) -> dict[str, Any]:
        value = {"classId": self.class_id, **json.loads(self.extra_json)}
        if self.bbox is not None:
            value["bbox"] = list(self.bbox)
        return value


def same_labels(expected: tuple[Label, ...], actual: tuple[Label, ...]) -> bool:
    """Order-independent multiset matching, with bounded saved-coordinate rounding."""
    if len(expected) != len(actual):
        return False

    def compatible(a: Label, b: Label) -> bool:
        return (
            a.class_id == b.class_id
            and a.extra_json == b.extra_json
            and (
                a.bbox == b.bbox
                or (
                    a.bbox is not None
                    and b.bbox is not None
                    and all(
                        abs(x - y) <= COORDINATE_TOLERANCE
                        for x, y in zip(a.bbox, b.bbox, strict=True)
                    )
                )
            )
        )

    if Counter(expected) == Counter(actual):
        return True
    # Iterative augmenting paths avoid both greedy ambiguity and recursion limits.
    matches: dict[int, int] = {}
    reverse: dict[int, int] = {}
    for root in range(len(expected)):
        queue = deque([root])
        parents: dict[int, int] = {}
        visited = {root}
        free: int | None = None
        while queue and free is None:
            index = queue.popleft()
            for candidate, label in enumerate(actual):
                if candidate in parents or not compatible(expected[index], label):
                    continue
                parents[candidate] = index
                if candidate not in matches:
                    free = candidate
                    break
                matched = matches[candidate]
                if matched not in visited:
                    visited.add(matched)
                    queue.append(matched)
        if free is None:
            return False
        while free is not None:
            index = parents[free]
            previous = reverse.get(index)
            matches[free] = index
            reverse[index] = free
            free = previous
    return True


@dataclass(frozen=True, slots=True)
class ImageSnapshot:
    image_id: str
    dataset_id: str
    filename: str
    split: str
    width: int
    height: int
    class_names: tuple[str, ...]
    labels: tuple[Label, ...]
    updated_at: str = ""
    labels_truncated: bool = False

    def __post_init__(self) -> None:
        if not self.image_id or not self.dataset_id or not self.filename:
            raise SyncConflict("missing remote image identity")
        if (
            self.split not in {"train", "val", "test"}
            or type(self.width) is not int
            or type(self.height) is not int
            or min(self.width, self.height) <= 0
        ):
            raise SyncConflict("invalid remote dimensions or split")
        if type(self.labels_truncated) is not bool or len(self.labels) > 10000:
            raise SyncConflict("invalid truncation evidence or label count")
        if not self.class_names or any(
            not isinstance(name, str) or not name for name in self.class_names
        ):
            raise SyncConflict("invalid dataset class names")
        if any(label.class_id >= len(self.class_names) for label in self.labels):
            raise SyncConflict("label class index is not in dataset catalog")

    @classmethod
    def from_wire(cls, value: dict[str, Any]) -> "ImageSnapshot":
        properties = value["properties"]
        labels = tuple(Label.from_wire(label) for label in value["labels"])
        if properties["annotationCount"] != len(labels) and not value.get("labelsTruncated"):
            raise SyncConflict("image response omitted labels")
        return cls(
            properties["id"],
            properties["datasetId"],
            properties["filename"],
            properties["split"],
            properties["width"],
            properties["height"],
            tuple(value["classNames"]),
            labels,
            properties.get("updatedAt", ""),
            value.get("labelsTruncated", False),
        )

    def fingerprint(self) -> str:
        return sha256(
            canonical_json(
                {
                    "identity": [self.image_id, self.dataset_id, self.filename],
                    "split": self.split,
                    "size": [self.width, self.height],
                    "classes": self.class_names,
                    "updated_at": self.updated_at,
                    "labels": sorted(canonical_json(label.wire()) for label in self.labels),
                    "truncated": self.labels_truncated,
                }
            ).encode()
        )


def require_complete_prediction(value: dict[str, Any]) -> None:
    """A proposal is not exhaustive evidence, even when partialImages is inconsistent."""
    partial_images = value.get("partialImages", [])
    if (
        value.get("partial", False) is not False
        or not isinstance(partial_images, list)
        or partial_images
    ):
        raise SyncConflict("partial Platform prediction requires completeness review")
    if value.get("labelsTruncated", False) is not False:
        raise SyncConflict("truncated Platform labels require completeness review")
