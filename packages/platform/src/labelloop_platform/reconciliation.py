"""Compiler-to-Platform reconciliation and frozen, traceable release mappings."""

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .sync_records import ImageSnapshot, Label, SyncConflict, canonical_json, same_labels, sha256


@dataclass(frozen=True, slots=True)
class ExpectedImage:
    filename: str
    source_id: str
    revision: int
    split: str
    width: int
    height: int
    labels: tuple[Label, ...]
    object_ids: tuple[str, ...]
    transform_json: str
    image_sha256: str


@dataclass(frozen=True, slots=True)
class ReleaseIntent:
    operation_id: str
    release_id: str
    recipe_id: str
    manifest_sha256: str
    archive_sha256: str
    class_map: tuple[tuple[str, int], ...]
    class_names: tuple[str, ...]
    images: tuple[ExpectedImage, ...]

    def __post_init__(self) -> None:
        if not all((self.operation_id, self.release_id, self.recipe_id, self.images)):
            raise SyncConflict("release intent needs identity and membership")
        if sorted(index for _, index in self.class_map) != list(range(len(self.class_names))):
            raise SyncConflict("class map must be dense and match catalog")
        if len({stable for stable, _ in self.class_map}) != len(self.class_map):
            raise SyncConflict("stable class IDs must be unique")
        if not self.class_names or len(set(self.class_names)) != len(self.class_names):
            raise SyncConflict("ingest class names must be unambiguous")
        filenames = [image.filename for image in self.images]
        if len(set(filenames)) != len(filenames):
            raise SyncConflict("release image names must be unique")
        if not any(image.split == "val" for image in self.images):
            raise SyncConflict("Platform release needs nonempty val before any mutation")
        for image in self.images:
            ingest_dimensions(image.width, image.height)
            if any(
                not label.detection_only or label.class_id >= len(self.class_names)
                for label in image.labels
            ):
                raise SyncConflict("compiled release contains unexpected task or class")

    @classmethod
    def from_compiler(
        cls,
        operation_id: str,
        manifest_bytes: bytes,
        archive: bytes,
        class_names: Mapping[str, str],
        read_file: Callable[[str], bytes],
    ) -> "ReleaseIntent":
        manifest = json.loads(manifest_bytes)
        mapping = manifest["class_map"]
        if set(mapping) != set(class_names):
            raise SyncConflict("approved catalog differs from compiler class map")
        images: list[ExpectedImage] = []
        for sample in manifest["samples"]:
            image_path, label_path = sample["image_path"], sample["label_path"]
            for path in (image_path, label_path):
                parsed = PurePosixPath(path)
                if parsed.is_absolute() or ".." in parsed.parts:
                    raise SyncConflict("unsafe compiler path")
                if sha256(read_file(path)) != manifest["file_hashes"][path]:
                    raise SyncConflict("compiled file changed after manifest freeze")
            labels = tuple(_yolo_label(row) for row in read_file(label_path).decode().splitlines())
            retained = tuple(obj for obj in sample["objects"] if obj.get("yolo_row") is not None)
            if labels != tuple(_yolo_label(obj["yolo_row"]) for obj in retained):
                raise SyncConflict("compiler object lineage does not match label file")
            if any(mapping[obj["class_id"]] != obj["class_index"] for obj in retained):
                raise SyncConflict("compiler class identity was remapped")
            if bool(sample["verified_negative"]) != (not labels):
                raise SyncConflict("missing explicit negative assessment")
            width, height = sample["image_size"]
            images.append(
                ExpectedImage(
                    PurePosixPath(image_path).name,
                    sample["asset_id"],
                    sample["revision"],
                    sample["split"],
                    width,
                    height,
                    labels,
                    tuple(obj["object_id"] for obj in retained),
                    canonical_json(sample["transform"]),
                    sample["image_sha256"],
                )
            )
        return cls(
            operation_id,
            manifest["release_id"],
            manifest["recipe"]["recipe_id"],
            sha256(manifest_bytes),
            sha256(archive),
            tuple(sorted(mapping.items(), key=lambda item: item[1])),
            tuple(class_names[stable] for stable, _ in sorted(mapping.items(), key=lambda i: i[1])),
            tuple(images),
        )

    def ingest_body(self, session_id: str) -> dict[str, Any]:
        if not session_id:
            raise SyncConflict("missing persisted upload session")
        return {
            "sessionId": session_id,
            "classMapping": {name: index for index, name in enumerate(self.class_names)},
            "conflictPolicy": "skip",
        }


def _yolo_label(row: str) -> Label:
    fields = row.split()
    if len(fields) != 5:
        raise SyncConflict("malformed compiler YOLO row")
    return Label(int(fields[0]), tuple(float(value) for value in fields[1:]))


def ingest_dimensions(width: int, height: int) -> tuple[tuple[int, int], ...]:
    """Only documented long-side resizing; integer raster rounding bounded to one pixel."""
    if type(width) is not int or type(height) is not int or min(width, height) < 28:
        raise SyncConflict("Platform rejects images with a short side below 28 pixels")
    if max(width, height) <= 4096:
        return ((width, height),)
    ratio = 4096 / max(width, height)
    # The contract specifies a 4096 cap but not floor versus nearest raster rounding.
    # Only the short side may vary by that rounding; no arbitrary dimension tolerance.
    if width >= height:
        return tuple(
            (4096, h) for h in sorted({math.floor(height * ratio), math.ceil(height * ratio)})
        )
    return tuple((w, 4096) for w in sorted({math.floor(width * ratio), math.ceil(width * ratio)}))


@dataclass(frozen=True, slots=True)
class ImageMapping:
    source_id: str
    revision: int
    filename: str
    image_id: str
    object_ids: tuple[str, ...]
    transform_json: str
    remote_width: int
    remote_height: int


@dataclass(frozen=True, slots=True)
class FrozenReleaseMapping:
    operation_id: str
    release_id: str
    recipe_id: str
    manifest_sha256: str
    archive_sha256: str
    owner: str
    dataset_slug: str
    dataset_id: str
    version: int
    content_hash: str
    class_map: tuple[tuple[str, int], ...]
    images: tuple[ImageMapping, ...]

    def verify_model_capture(self, model: dict[str, Any]) -> None:
        capture = model.get("datasetVersion", {})
        if (model.get("datasetId"), capture.get("version"), capture.get("contentHash")) != (
            self.dataset_id,
            self.version,
            self.content_hash,
        ):
            raise SyncConflict("model captured a different dataset version")


def reconcile_release(
    intent: ReleaseIntent,
    *,
    owner: str,
    dataset_slug: str,
    dataset_id: str,
    class_names: tuple[str, ...],
    images: tuple[ImageSnapshot, ...],
    listing_complete: bool,
    processing_complete: bool,
    version: int,
    content_hash: str,
) -> FrozenReleaseMapping:
    if not processing_complete or not listing_complete:
        raise SyncConflict("queued ingest or partial image listing is not a reconciled release")
    if not owner or not dataset_slug or not dataset_id or type(version) is not int or version < 1:
        raise SyncConflict("missing captured release identity/version")
    if not content_hash or class_names != intent.class_names:
        raise SyncConflict("class order changed or captured content hash is missing")
    actual = {image.filename: image for image in images}
    if len(actual) != len(images) or len({image.image_id for image in images}) != len(images):
        raise SyncConflict("duplicate remote image identity")
    if set(actual) != {image.filename for image in intent.images}:
        raise SyncConflict("ingested image membership differs from immutable manifest")
    mappings: list[ImageMapping] = []
    for expected in intent.images:
        image = actual[expected.filename]
        if image.dataset_id != dataset_id or image.class_names != intent.class_names:
            raise SyncConflict("image belongs to a different dataset or class order")
        if image.split != expected.split or image.labels_truncated:
            raise SyncConflict("image split changed or label response is truncated")
        if (image.width, image.height) not in ingest_dimensions(expected.width, expected.height):
            raise SyncConflict("unexpected ingest geometry")
        if not same_labels(expected.labels, image.labels):
            raise SyncConflict("ingested label membership, class or geometry changed")
        mappings.append(
            ImageMapping(
                expected.source_id,
                expected.revision,
                expected.filename,
                image.image_id,
                expected.object_ids,
                expected.transform_json,
                image.width,
                image.height,
            )
        )
    return FrozenReleaseMapping(
        intent.operation_id,
        intent.release_id,
        intent.recipe_id,
        intent.manifest_sha256,
        intent.archive_sha256,
        owner,
        dataset_slug,
        dataset_id,
        version,
        content_hash,
        intent.class_map,
        tuple(mappings),
    )
