"""Strict, atomic YOLO export with complete source-to-derived lineage."""

import hashlib
import io
import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from labelloop_core.domain import CanonicalDatasetRelease, Recipe, ReleasedSample
from labelloop_core.geometry import (
    AffineTransform,
    Box,
    Crop,
    full_frame_transform,
    normalize_box,
    target_flags,
)

from .ingest import imaging_toolchain

ImageReader = Callable[[str], bytes]
CropPolicy = Callable[[ReleasedSample], Crop | None]
INTERPOLATIONS = {"linear": cv2.INTER_LINEAR, "area": cv2.INTER_AREA}


class CompilerFailure(ValueError):
    """A deterministic gate failure, never a silently skipped YOLO label."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(document: Any) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode()


def _serialized_geometry(values: tuple[float, float, float, float]) -> str:
    cx, cy, width, height = (Decimal(f"{value:.8f}") for value in values)
    # Rounding the center and extent independently must not move an edge outside [0,1].
    width = min(width, 2 * cx, 2 * (1 - cx))
    height = min(height, 2 * cy, 2 * (1 - cy))
    return " ".join(f"{value:.8f}" for value in (cx, cy, width, height))


def resolve_recipe(recipe: str | Recipe) -> Recipe:
    if isinstance(recipe, Recipe):
        return recipe
    if recipe in {"full-letterbox-640-v1", "full-letterbox-320-v1"}:
        return Recipe(
            recipe_id=recipe,
            output_size=640 if recipe.endswith("640-v1") else 320,
            interpolation="auto-area-linear",
        )
    raise ValueError("unknown preprocessing recipe")


def compile_dataset(
    release: CanonicalDatasetRelease,
    recipe: str | Recipe,
    output: Path,
    read_image: Mapping[str, bytes] | ImageReader,
    *,
    crop_policy: CropPolicy | None = None,
) -> dict[str, Any]:
    reader = read_image if callable(read_image) else read_image.__getitem__
    selected_recipe = resolve_recipe(recipe)
    output = output.resolve()
    temporary = output.with_name(f".{output.name}.partial.{os.getpid()}")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    manifest_samples: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = [
        {"asset_id": asset_id, "reason": reason}
        for asset_id, reason in sorted(release.excluded_asset_ids.items())
    ]
    file_hashes: dict[str, str] = {}
    try:
        seen_names: set[str] = set()
        source_groups: dict[str, tuple[str, str]] = {}
        for sample in sorted(release.samples, key=lambda item: item.asset.asset_id):
            group = (sample.group_id, sample.split)
            if source_groups.setdefault(sample.asset.sha256, group) != group:
                raise CompilerFailure("identical sources must share one split group")
            compiled = _compile_sample(sample, selected_recipe, reader, release, crop_policy)
            if sample.asset.asset_id in seen_names:
                raise CompilerFailure("duplicate derivative basename")
            seen_names.add(sample.asset.asset_id)
            if compiled is None:
                exclusions.append(
                    {
                        "asset_id": sample.asset.asset_id,
                        "revision": sample.revision.revision,
                        "reason": "all targets outside or below the declared crop/size policy",
                    }
                )
                continue
            labels, image, record = compiled
            basename = f"{sample.asset.sha256}-{digest(sample.asset.asset_id.encode('utf-8'))}"
            image_path = temporary / "images" / sample.split / f"{basename}.jpg"
            label_path = temporary / "labels" / sample.split / f"{basename}.txt"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            label_path.parent.mkdir(parents=True, exist_ok=True)
            image_bytes = _encode(image, selected_recipe)
            image_path.write_bytes(image_bytes)
            label_path.write_text(labels, encoding="utf-8", newline="\n")
            file_hashes[str(image_path.relative_to(temporary))] = digest(image_bytes)
            file_hashes[str(label_path.relative_to(temporary))] = digest(labels.encode())
            record["image_sha256"] = digest(image_bytes)
            record["label_sha256"] = digest(labels.encode())
            manifest_samples.append(record)
            record["image_path"] = str(image_path.relative_to(temporary))
            record["label_path"] = str(label_path.relative_to(temporary))

        if not any(item["split"] == "val" for item in manifest_samples):
            raise CompilerFailure("every compiled release needs non-empty val membership")
        splits = sorted(set(sample["split"] for sample in manifest_samples))
        documents: dict[str, bytes] = {
            "data.yaml": _data_yaml(release, splits),
            "class-map.json": json_bytes(release.ontology.mapping()),
            "transforms.jsonl": _jsonl(manifest_samples),
            "exclusions.jsonl": _jsonl(exclusions),
        }
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "release_id": release.release_id,
            "recipe": json.loads(json.dumps(asdict(selected_recipe))),
            "ontology_id": release.ontology.ontology_id,
            "ontology_version": release.ontology.version,
            "ontology_hash": release.ontology_hash(),
            "class_map": release.ontology.mapping(),
            "split_assignment_version": release.assignment.assignment_version,
            "split_algorithm": release.assignment.algorithm,
            "split_seed": release.assignment.seed,
            "samples": manifest_samples,
            "release_membership": {
                asset_id: "compiled"
                if any(sample.asset.asset_id == asset_id for sample in release.samples)
                and any(item["asset_id"] == asset_id for item in manifest_samples)
                else "excluded"
                for asset_id in sorted(
                    {sample.asset.asset_id for sample in release.samples}
                    | set(release.excluded_asset_ids)
                )
            },
            "class_support": _class_support(release, manifest_samples),
            "file_hashes": file_hashes,
            "excluded_count": len(exclusions),
            "compiler_version": "labelloop-compiler-1.0.0",
            "toolchain": _toolchain(),
        }
        documents["validation-report.json"] = json_bytes(
            {
                "valid": True,
                "checks": {
                    "decoding": True,
                    "actual_raster_dimensions": True,
                    "rgb_channels": True,
                    "label_image_pairing": True,
                    "unique_membership": True,
                    "class_membership": True,
                    "finite_normalized_geometry": True,
                    "inverse_transforms": True,
                    "class_support": _class_support(release, manifest_samples),
                    "split_isolation": True,
                    "release_membership": True,
                    "verified_negatives": True,
                    "non_empty_val": True,
                },
                "sample_count": len(manifest_samples),
                "exclusion_count": len(exclusions),
            }
        )
        documents["manifest.json"] = json_bytes(manifest)
        for relative, data in documents.items():
            (temporary / relative).write_bytes(data)
        for relative in _contact_sheet(temporary, manifest_samples):
            file_hashes[relative] = digest((temporary / relative).read_bytes())
        for relative, data in documents.items():
            file_hashes[relative] = digest(data)
        file_hashes.pop("manifest.json")
        manifest["file_hashes"] = file_hashes
        (temporary / "manifest.json").write_bytes(json_bytes(manifest))
        _validate_output(temporary, release, manifest)
        if output.exists():
            raise CompilerFailure("output directory must not already exist")
        temporary.rename(output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def _compile_sample(
    sample: ReleasedSample,
    recipe: Recipe,
    read_image: ImageReader,
    release: CanonicalDatasetRelease,
    crop_policy: CropPolicy | None,
) -> tuple[str, Image.Image, dict[str, Any]] | None:
    asset, revision = sample.asset, sample.revision
    data = read_image(asset.asset_id)
    if digest(data) != asset.sha256:
        raise CompilerFailure(f"source bytes do not match recorded hash: {asset.asset_id}")
    with Image.open(io.BytesIO(data)) as opened:
        opened.load()
        if opened.mode != "RGB" or (opened.width, opened.height) != (
            asset.width,
            asset.height,
        ):
            raise CompilerFailure("source must be ingested RGB with recorded dimensions")
        original = opened.copy()
    crop = crop_policy(sample) if crop_policy else None
    transform = full_frame_transform(
        asset.width,
        asset.height,
        recipe.output_size,
        crop=crop,
        allow_upscale=recipe.allow_upscale,
    )
    crop_box = transform.crop.box
    for box in (crop_box.x_min, crop_box.y_min, crop_box.x_max, crop_box.y_max):
        if not box.is_integer():
            raise CompilerFailure("rasterization of fractional crops requires a versioned policy")
    cropped = original.crop(
        (
            int(crop_box.x_min),
            int(crop_box.y_min),
            int(crop_box.x_max),
            int(crop_box.y_max),
        )
    )
    mode = (
        INTERPOLATIONS["area" if transform.scale_x < 1 else "linear"]
        if recipe.interpolation == "auto-area-linear"
        else INTERPOLATIONS[recipe.interpolation]
    )
    resized = cv2.resize(
        np.asarray(cropped),
        (transform.resized_width, transform.resized_height),
        interpolation=mode,
    )
    canvas = Image.new("RGB", (recipe.output_size,) * 2, recipe.padding_color)
    canvas.paste(Image.fromarray(resized), (transform.left, transform.top))
    rows: list[str] = []
    objects: list[dict[str, Any]] = []
    seen_rows: set[str] = set()
    for item in sorted(revision.objects, key=lambda obj: obj.object_id):
        retained_source = item.box.intersect(transform.crop.box)
        mapped = transform.apply(item.box)
        flags = list(target_flags(transform, item.box, recipe.min_target_px))
        if retained_source is None or mapped is None:
            objects.append(
                {
                    "object_id": item.object_id,
                    "class_id": item.class_id,
                    "status": "absent_after_crop",
                    "retained_fraction": 0,
                }
            )
            continue
        if (
            item.box.x_min == 0
            or item.box.y_min == 0
            or item.box.x_max == asset.width
            or item.box.y_max == asset.height
        ):
            flags.append("image_edge")
        fraction = retained_source.area / item.box.area
        excluded = fraction < recipe.min_retained_fraction or "below_min_target_px" in flags
        row: str | None = None
        if not excluded:
            normalized = normalize_box(mapped, recipe.output_size, recipe.output_size)
            class_index = release.ontology.class_map[item.class_id]
            row = f"{class_index} " + _serialized_geometry(normalized)
            if row in seen_rows:
                raise CompilerFailure("duplicate normalized YOLO row")
            seen_rows.add(row)
            restored = transform.restore(mapped)
            if not _near_box(restored, retained_source):
                raise CompilerFailure("inverse transform failed to recover the retained source box")
            rows.append(row)
        objects.append(
            {
                "object_id": item.object_id,
                "class_id": item.class_id,
                "class_index": release.ontology.class_map[item.class_id],
                "source_box": list(asdict(item.box).values()),
                "retained_source_box": list(asdict(retained_source).values()),
                "retained_fraction": fraction,
                "policy": "excluded" if excluded else "retained",
                "flags": flags,
                "occluded": item.occluded,
                "truncated_at_image_edge": item.truncated_at_image_edge,
                "yolo_row": row,
            }
        )
    discarded = [item for item in objects if item.get("policy") == "excluded"]
    if discarded and rows:
        raise CompilerFailure(
            "a visible target fails crop-retention/size policy; recrop or explicitly exclude "
            "the complete sample instead of leaving a fragment unlabeled"
        )
    if revision.objects and not rows:
        return None
    labels = "".join(f"{row}\n" for row in rows)
    record = {
        "asset_id": asset.asset_id,
        "source_sha256": asset.sha256,
        "revision": revision.revision,
        "parent_revision": revision.parent_revision,
        "review_tier": revision.review_tier,
        "group_id": sample.group_id,
        "split": sample.split,
        "verified_negative": not rows,
        "image_size": [recipe.output_size, recipe.output_size],
        "transform": transform_document(transform),
        "objects": objects,
    }
    return labels, canvas, record


def transform_document(transform: AffineTransform) -> dict[str, Any]:
    return json.loads(
        json.dumps(
            asdict(transform)
            | {"valid_content_rectangle": list(asdict(transform.valid_content).values())}
        )
    )


def _near_box(left: Box | None, right: Box) -> bool:
    return left is not None and all(
        abs(a - b) <= 1e-9
        for a, b in zip(asdict(left).values(), asdict(right).values(), strict=True)
    )


def _data_yaml(release: CanonicalDatasetRelease, splits: list[str]) -> bytes:
    lines = [f"{split}: images/{split}" for split in splits]
    lines.append("names:")
    classes = sorted(
        release.ontology.classes,
        key=lambda item: release.ontology.class_map[item.stable_id],
    )
    lines.extend(
        f"  {release.ontology.class_map[item.stable_id]}: {json.dumps(item.name)}"
        for item in classes
    )
    return ("\n".join(lines) + "\n").encode()


def _jsonl(rows: Any) -> bytes:
    return "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows
    ).encode()


def _encode(image: Image.Image, recipe: Recipe) -> bytes:
    output = io.BytesIO()
    if recipe.encoder != "JPEG":
        raise CompilerFailure("only the pinned JPEG derivative encoder is supported")
    image.save(output, format="JPEG", **recipe.encoder_options)
    return output.getvalue()


def _toolchain() -> dict[str, str | int | float | bool]:
    return {
        **imaging_toolchain(),
        "derivative_interpolation": "OpenCV literal area/linear by direction",
        "derivative_encoder": "Pillow JPEG; settings in manifest recipe",
        "opencv": cv2.__version__,
        "numpy": np.__version__,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def _class_support(
    release: CanonicalDatasetRelease, samples: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    counts = {
        class_id: {"train": 0, "val": 0, "test": 0} for class_id in release.ontology.class_map
    }
    for sample in samples:
        for item in sample["objects"]:
            if item.get("class_id") and item.get("policy") == "retained":
                counts[item["class_id"]][sample["split"]] += 1
    return {
        class_id: {**support, "validation_supported": support["val"] > 0}
        for class_id, support in counts.items()
    }


def _contact_sheet(root: Path, samples: list[dict[str, Any]]) -> list[str]:
    # Bound each audit page to 8 x 8 thumbnails, independently of release size.
    width, height, page_size = 160, 180, 64
    paths = []
    for start in range(0, len(samples), page_size):
        page = samples[start : start + page_size]
        sheet = Image.new("RGB", (width * 8, height * ((len(page) + 7) // 8)), "white")
        draw = ImageDraw.Draw(sheet)
        for index, sample in enumerate(page):
            with Image.open(root / sample["image_path"]) as image:
                thumbnail = image.copy().convert("RGB")
            thumbnail.thumbnail((width, 160))
            x, y = index % 8 * width, index // 8 * height
            sheet.paste(thumbnail, (x, y))
            draw.text((x, y + 162), sample["asset_id"][:24], fill="black")
        relative = (
            "preview-contact-sheet.jpg"
            if start == 0
            else f"preview-contact-sheet-{start // page_size + 1:04d}.jpg"
        )
        sheet.save(root / relative, format="JPEG", quality=90, optimize=False)
        paths.append(relative)
    return paths


def _validate_output(
    root: Path, release: CanonicalDatasetRelease, manifest: dict[str, Any]
) -> None:
    if json_bytes(manifest) != (root / "manifest.json").read_bytes():
        raise CompilerFailure("serialized manifest differs from the compiled release")
    expected_membership = {
        sample.asset.asset_id: (
            "compiled"
            if any(item["asset_id"] == sample.asset.asset_id for item in manifest["samples"])
            else "excluded"
        )
        for sample in release.samples
    } | {asset_id: "excluded" for asset_id in release.excluded_asset_ids}
    if manifest["release_membership"] != expected_membership:
        raise CompilerFailure("compiled release membership is incomplete or ambiguous")
    exclusions = [json.loads(line) for line in (root / "exclusions.jsonl").read_text().splitlines()]
    if len(exclusions) != len(expected_membership) - len(manifest["samples"]):
        raise CompilerFailure("every omitted source requires an exclusion")
    declared_pairs = {
        (sample["image_path"], sample["label_path"]) for sample in manifest["samples"]
    }
    actual_pairs: set[tuple[str, str]] = set()
    for folder, suffix in (("images", ".jpg"), ("labels", ".txt")):
        actual = {
            str(path.relative_to(root))
            for path in (root / folder).rglob(f"*{suffix}")
            if path.is_file()
        }
        if actual != {sample[f"{folder[:-1]}_path"] for sample in manifest["samples"]}:
            raise CompilerFailure("actual image and label paths differ from the manifest")
    for image_path, label_path in declared_pairs:
        actual_pairs.add((image_path, label_path))
    if len(actual_pairs) != len(manifest["samples"]):
        raise CompilerFailure("image and label pairs must be unique and one-to-one")
    expected_files = set(manifest["file_hashes"]) | {"manifest.json"}
    actual_files = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    if actual_files != expected_files:
        raise CompilerFailure("output contains missing or undeclared files")
    for relative, recorded_hash in manifest["file_hashes"].items():
        if digest((root / relative).read_bytes()) != recorded_hash:
            raise CompilerFailure("output hash differs from manifest")
    for sample in manifest["samples"]:
        with Image.open(root / sample["image_path"]) as image:
            image.load()
            if image.size != tuple(manifest["recipe"]["output_size"] for _ in range(2)):
                raise CompilerFailure("actual raster size differs from recipe")
            if image.mode != "RGB":
                raise CompilerFailure("derivative channels are not RGB")
        rows = (root / sample["label_path"]).read_text().splitlines()
        if not rows and not sample["verified_negative"]:
            raise CompilerFailure("only explicit verified negatives may be empty")
        if len(rows) != len(set(rows)):
            raise CompilerFailure("duplicate row")
        for row in rows:
            fields = row.split()
            if len(fields) != 5 or not fields[0].isdigit():
                raise CompilerFailure("strict YOLO rows contain five fields and integer class")
            class_index = int(fields[0])
            if class_index not in set(manifest["class_map"].values()):
                raise CompilerFailure("unknown class index")
            try:
                values = [Decimal(value) for value in fields[1:]]
            except InvalidOperation as error:
                raise CompilerFailure("invalid normalized geometry") from error
            cx, cy, width, height = values
            if (
                any(not value.is_finite() for value in values)
                or any(not 0 <= value <= 1 for value in values)
                or min(width, height) <= 0
            ):
                raise CompilerFailure("invalid normalized geometry")
            if (
                cx - width / 2 < 0
                or cx + width / 2 > 1
                or cy - height / 2 < 0
                or cy + height / 2 > 1
            ):
                raise CompilerFailure("label exceeds derived image bounds")
