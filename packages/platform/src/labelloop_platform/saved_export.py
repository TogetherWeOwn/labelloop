"""Read saved detection NDJSON, measuring downloaded content rather than trusting CAS IDs."""

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Protocol

from .reconciliation import CapturedVersion
from .sync_records import ContentEvidence, ImageSnapshot, Label, SyncConflict, same_labels, sha256


@dataclass(frozen=True, slots=True)
class SavedExport:
    # Source reads the authorized dataset and GET export?v=N, never a live export.
    dataset: dict[str, Any]
    returned_version: int
    content: bytes


class SavedExportSource(Protocol):
    """Foundation binding uses PlatformPort and separately authorized StoragePort.

    Implementations must bound response bytes before buffering and refuse redirects.
    No URL obtained here is authority to bypass the storage host policy. Image lookup
    must be complete and unambiguous; it supplies IDs, not saved annotations, and
    measures current image bytes independently into ImageSnapshot.content.
    """

    def saved_export(self, owner: str, slug: str, version: int) -> SavedExport: ...
    def image_by_filename(self, dataset_id: str, filename: str) -> ImageSnapshot: ...
    def download_image(self, url: str) -> bytes: ...


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SyncConflict("duplicate export object key")
        result[key] = value
    return result


def _record(line: bytes) -> dict[str, Any]:
    try:
        value = json.loads(line, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise SyncConflict("malformed saved export record") from None
    if not isinstance(value, dict):
        raise SyncConflict("saved export record must be an object")
    return value


def _quiet_read[T](read: Callable[[], T]) -> T:
    try:
        return read()
    except Exception:
        # Suppress even domain exceptions raised by a provider/decoder implementation.
        raise SyncConflict("saved export source read failed; no release frozen") from None


def read_saved_export(
    source: SavedExportSource,
    *,
    owner: str,
    slug: str,
    dataset_id: str,
    version: int,
    content_hash: str,
    decode_dimensions: Callable[[bytes], tuple[int, int]],
    max_export_bytes: int = 16 * 1024 * 1024,
    max_image_bytes: int = 32 * 1024 * 1024,
    max_images: int = 10000,
) -> CapturedVersion:
    """Freeze only saved rows, exact version metadata and measured image bytes.

    decode_dimensions is a trusted pinned raster decoder, not remote metadata. The
    caller supplies a previously recorded version/contentHash. The opaque Platform
    version hash is compared for identity, never treated as the NDJSON SHA-256.
    This bounded reader deliberately rejects unsupported tasks instead of dropping
    annotations. It does not grant live access or create/capture another version.
    """
    if (
        not all((owner, slug, dataset_id, content_hash))
        or type(version) is not int
        or version < 1
        or any(type(n) is not int or n < 1 for n in (max_export_bytes, max_image_bytes, max_images))
    ):
        raise SyncConflict("invalid saved export identity or resource limit")
    try:
        downloaded = _quiet_read(lambda: source.saved_export(owner, slug, version))
        dataset = downloaded.dataset
        if (
            (dataset["id"], dataset["owner"], dataset["dataset"]) != (dataset_id, owner, slug)
            or dataset["visibility"] != "private"
            or dataset["task"] != "detect"
            or dataset["status"] != "ready"
            or type(downloaded.returned_version) is not int
            or downloaded.returned_version != version
        ):
            raise SyncConflict("saved export did not establish ready private dataset identity")
        versions = [v for v in dataset["versions"] if v["version"] == version]
        if len(versions) != 1 or versions[0].get("contentHash") != content_hash:
            raise SyncConflict("saved version content identity differs from recorded capture")
        capture = versions[0]
        data = downloaded.content
        if not isinstance(data, bytes) or not data or len(data) > max_export_bytes:
            raise SyncConflict("saved export exceeds bounded reader or is empty")
        lines = data.splitlines()
        if not lines or any(not line.strip() for line in lines):
            raise SyncConflict("empty or interrupted saved export record")
        header = _record(lines[0])
        # Header version semantics alone do not prove the requested saved API version.
        if header.get("type") != "dataset" or header.get("task") != "detect":
            raise SyncConflict("saved export has unsupported dataset task")
        names = header["class_names"]
        if not isinstance(names, dict) or set(names) != {str(i) for i in range(len(names))}:
            raise SyncConflict("saved export class indices must be dense")
        class_names = tuple(names[str(i)] for i in range(len(names)))
        if not class_names or class_names != tuple(dataset["classNames"]):
            raise SyncConflict("saved export class order differs from frozen dataset")
        if type(capture["imageCount"]) is not int or not (
            len(lines) - 1 == capture["imageCount"] <= max_images
        ):
            raise SyncConflict("saved export membership is incomplete or exceeds bounded reader")
        images = []
        filenames: set[str] = set()
        ids: set[str] = set()
        for line in lines[1:]:
            row = _record(line)
            if row.get("type") != "image" or set(row) - {
                "type",
                "file",
                "url",
                "width",
                "height",
                "split",
                "metadata",
                "annotations",
            }:
                raise SyncConflict("unsupported or partial saved image record")
            filename = row["file"]
            if not isinstance(filename, str) or not filename or filename in filenames:
                raise SyncConflict("duplicate or missing saved image filename")
            filenames.add(filename)
            annotations = row.get("annotations", {})
            if not isinstance(annotations, dict) or set(annotations) - {"boxes"}:
                raise SyncConflict("saved detection export includes unsupported annotation task")
            boxes = annotations.get("boxes", [])
            if not isinstance(boxes, list) or len(boxes) > 10000:
                raise SyncConflict("invalid saved detection count")
            labels = []
            for box in boxes:
                if not isinstance(box, list) or len(box) != 5:
                    raise SyncConflict("invalid saved detection tuple")
                labels.append(Label(box[0], tuple(box[1:])))
            current = _quiet_read(partial(source.image_by_filename, dataset_id, filename))
            # NDJSON omits image IDs. Bind a complete current ID lookup to every
            # saved field, but never substitute current labels for the saved rows.
            saved = ImageSnapshot(
                current.image_id,
                dataset_id,
                filename,
                row["split"],
                row["width"],
                row["height"],
                class_names,
                tuple(labels),
                storage_hash=current.storage_hash,
            )
            if (
                current.image_id in ids
                or current.dataset_id != dataset_id
                or current.filename != filename
                or current.labels_truncated
                or (current.split, current.width, current.height, current.class_names)
                != (saved.split, saved.width, saved.height, saved.class_names)
                or not same_labels(saved.labels, current.labels)
            ):
                raise SyncConflict("current image identity diverges from immutable saved row")
            ids.add(current.image_id)
            url = row["url"]
            if not isinstance(url, str) or not url:
                raise SyncConflict("saved image URL missing")
            pixels = _quiet_read(partial(source.download_image, url))
            if not isinstance(pixels, bytes) or not pixels or len(pixels) > max_image_bytes:
                raise SyncConflict("image exceeds bounded reader or is empty")
            width, height = _quiet_read(partial(decode_dimensions, pixels))
            if (width, height) != (saved.width, saved.height):
                raise SyncConflict("downloaded raster differs from saved image dimensions")
            measured = ContentEvidence(sha256(pixels), width, height)
            if current.content != measured:
                raise SyncConflict("current image bytes do not bind its ID to the saved export")
            images.append(replace(saved, content=measured))
        if (
            capture["classCount"] != len(class_names)
            or capture["annotationCount"] != sum(len(image.labels) for image in images)
            or any(type(capture[key]) is not int for key in ("classCount", "annotationCount"))
        ):
            raise SyncConflict("saved export counts differ from version record")
        splits = Counter(image.split for image in images)
        if any(capture["splits"][key] != splits[key] for key in ("train", "val", "test")):
            raise SyncConflict("saved export split membership differs from version record")
        return CapturedVersion(
            dataset_id,
            version,
            content_hash,
            class_names,
            tuple(images),
            True,
            True,
            sha256(data),
            "saved_version_export",
        )
    except SyncConflict:
        raise
    except Exception:
        # Provider bodies, signed URLs and decoder errors must not escape in logs.
        raise SyncConflict("saved export read failed; no release frozen") from None
