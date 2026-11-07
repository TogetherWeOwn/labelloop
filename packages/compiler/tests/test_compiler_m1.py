"""Compiler acceptance gates for unrelated source domains and edge cases."""

import hashlib
import io
import json
from dataclasses import replace
from decimal import Decimal

import pytest
from PIL import Image, ImageDraw
from support import canonical_release

from labelloop_compiler.compiler import CompilerFailure, _contact_sheet, compile_dataset
from labelloop_compiler.ingest import process_image
from labelloop_core.domain import DatasetObject, Recipe
from labelloop_core.geometry import Box, Crop, full_frame_transform, normalize_box


@pytest.mark.parametrize("recipe", ["full-letterbox-640-v1", "full-letterbox-320-v1"])
def test_byte_identical_both_fixture_domains(domain, recipe, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    manifest = compile_dataset(domain.release, recipe, first, domain.images)
    compile_dataset(domain.release, recipe, second, domain.images)
    files = sorted(path.relative_to(first) for path in first.rglob("*") if path.is_file())
    assert files == sorted(path.relative_to(second) for path in second.rglob("*") if path.is_file())
    assert all((first / f).read_bytes() == (second / f).read_bytes() for f in files)
    size = 640 if recipe.endswith("640-v1") else 320
    for sample in manifest["samples"]:
        path = first / sample["image_path"]
        identity_hash = hashlib.sha256(sample["asset_id"].encode("utf-8")).hexdigest()
        assert path.name == f"{sample['source_sha256']}-{identity_hash}.jpg"
        with Image.open(path) as image:
            assert image.size == (size, size) and image.mode == "RGB"
        assert domain.release.assignment.group_splits[sample["group_id"]] == sample["split"]
        assert sample["label_sha256"] == manifest["file_hashes"][sample["label_path"]]
        for item in sample["objects"]:
            assert item["class_index"] == domain.release.ontology.class_map[item["class_id"]]
    assert any(sample["split"] == "val" for sample in manifest["samples"])


@pytest.mark.parametrize("copy_count", [1, 64])
def test_identical_bytes_keep_distinct_asset_lineage_and_labels(tmp_path, copy_count):
    release, images = canonical_release()
    original = release.samples[0]
    copies = tuple(
        replace(
            original,
            asset=replace(original.asset, asset_id=f"copy-{index:03d}"),
            revision=replace(
                original.revision,
                objects=(DatasetObject("copy-object", "item", Box(20, 30, 160, 110)),),
            ),
        )
        for index in range(copy_count)
    )
    images.update({sample.asset.asset_id: images["a"] for sample in copies})
    release = replace(
        release,
        samples=(*release.samples, *copies),
        assignment=replace(
            release.assignment,
            asset_splits={
                **release.assignment.asset_splits,
                **{sample.asset.asset_id: "train" for sample in copies},
            },
            group_ids={
                **release.assignment.group_ids,
                **{sample.asset.asset_id: original.group_id for sample in copies},
            },
        ),
    )
    recipe = Recipe("small-regression", 32, "auto-area-linear")
    first = compile_dataset(release, recipe, tmp_path / "first", images)
    second = compile_dataset(
        replace(release, samples=tuple(reversed(release.samples))),
        recipe,
        tmp_path / "second",
        images,
    )
    assert first == second
    assert len(first["samples"]) == 2 + copy_count
    assert len({sample["image_path"] for sample in first["samples"]}) == 2 + copy_count
    assert len({sample["label_path"] for sample in first["samples"]}) == 2 + copy_count
    same_source = [sample for sample in first["samples"] if sample["asset_id"] != "b"]
    assert {sample["source_sha256"] for sample in same_source} == {original.asset.sha256}
    assert len({sample["image_sha256"] for sample in same_source}) == 1
    assert len({sample["label_sha256"] for sample in same_source}) == 2
    assert all(sample["group_id"] == original.group_id for sample in same_source)
    previews = sorted(path for path in first["file_hashes"] if path.startswith("preview-"))
    assert len(previews) == (2 + copy_count + 63) // 64
    for relative in first["file_hashes"]:
        assert (tmp_path / "first" / relative).read_bytes() == (
            tmp_path / "second" / relative
        ).read_bytes()


def test_identical_bytes_cannot_cross_split_groups(tmp_path):
    release, images = canonical_release()
    train, val = release.samples
    images["b"] = images["a"]
    with pytest.raises(ValueError, match="split|group"):
        release = replace(
            release,
            samples=(train, replace(val, asset=replace(train.asset, asset_id="b"))),
        )
        compile_dataset(release, "full-letterbox-320-v1", tmp_path / "out", images)
    assert not (tmp_path / "out").exists()


def test_contact_sheet_paginates_large_release_without_omitting_samples(tmp_path, monkeypatch):
    Image.new("RGB", (16, 16), "blue").save(tmp_path / "source.jpg")
    samples = [
        {"asset_id": f"asset-{index:04d}", "image_path": "source.jpg"} for index in range(2905)
    ]
    rendered = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(self, xy, text, *args, **kwargs):
        rendered.append(text)
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    paths = _contact_sheet(tmp_path, samples)
    assert len(paths) == 46
    assert paths[0] == "preview-contact-sheet.jpg"
    assert paths[-1] == "preview-contact-sheet-0046.jpg"
    assert rendered == [sample["asset_id"] for sample in samples]
    for relative in paths:
        with Image.open(tmp_path / relative) as image:
            image.load()
            assert image.width == 1280
            assert 0 < image.height <= 1440


def test_required_paths_and_full_membership(tmp_path):
    release, images = canonical_release()
    output = tmp_path / "compiled"
    manifest = compile_dataset(release, "full-letterbox-320-v1", output, images)
    for relative in (
        "data.yaml",
        "manifest.json",
        "class-map.json",
        "transforms.jsonl",
        "exclusions.jsonl",
        "validation-report.json",
        "preview-contact-sheet.jpg",
    ):
        assert (output / relative).exists()
    assert (output / "data.yaml").read_text() == (
        'train: images/train\nval: images/val\nnames:\n  0: "item"\n'
    )
    assert set(manifest["release_membership"]) == {"a", "b"}
    assert manifest["class_support"]["item"]["validation_supported"]


def test_crop_recording_discarded_extent_and_inverse(tmp_path):
    release, images = canonical_release()
    manifest = compile_dataset(
        release,
        "full-letterbox-320-v1",
        tmp_path / "crop",
        images,
        crop_policy=lambda _: Crop(0, 0, 80, 150),
    )
    first = manifest["samples"][0]
    assert first["transform"]["crop"] == {"x": 0.0, "y": 0.0, "width": 80.0, "height": 150.0}
    item = first["objects"][0]
    assert item["retained_fraction"] < 1
    assert "clipped" in item["flags"]
    assert item["retained_source_box"] == [10.0, 20.0, 80.0, 120.0]
    transform = full_frame_transform(200, 150, 320, crop=Crop(0, 0, 80, 150))
    assert transform.restore(transform.apply(Box(10, 20, 170, 120)) or Box(0, 0, 1, 1)) == Box(
        10, 20, 80, 120
    )


def test_verified_negative_empty_file_and_group_membership(tmp_path):
    release, images = canonical_release()
    output = io.BytesIO()
    Image.new("RGB", (200, 150), (200, 100, 30)).save(output, "JPEG", quality=95)
    negative_asset, negative = process_image(
        output.getvalue(), asset_id="negative", project_id="project"
    )
    images["negative"] = negative
    samples = list(release.samples) + [
        replace(
            release.samples[0],
            asset=negative_asset,
            revision=replace(release.samples[0].revision, objects=()),
        )
    ]
    release = replace(
        release,
        samples=tuple(samples),
        assignment=replace(
            release.assignment,
            asset_splits={**release.assignment.asset_splits, "negative": "train"},
            group_ids={**release.assignment.group_ids, "negative": "a-group"},
        ),
        excluded_asset_ids={"excluded-source": "unresolved target"},
    )
    manifest = compile_dataset(release, "full-letterbox-320-v1", tmp_path / "out", images)
    negative_sample = next(
        sample for sample in manifest["samples"] if sample["asset_id"] == "negative"
    )
    assert negative_sample["verified_negative"] is True
    assert (tmp_path / "out" / negative_sample["label_path"]).read_bytes() == b""
    assert manifest["excluded_count"] == 1


def test_source_hash_and_unreleased_samples_are_rejected(tmp_path):
    release, images = canonical_release()
    images["a"] = images["b"]
    with pytest.raises(CompilerFailure, match="recorded hash"):
        compile_dataset(release, "full-letterbox-320-v1", tmp_path / "out", images)
    with pytest.raises(ValueError, match="unresolved"):
        replace(
            release,
            samples=(
                replace(
                    release.samples[0],
                    revision=replace(release.samples[0].revision, training_eligible=False),
                ),
                release.samples[1],
            ),
        )


def test_duplicate_row_and_incomplete_canonical_state_gates(tmp_path):
    release, _ = canonical_release()
    first = release.samples[0]
    obj = first.revision.objects[0]
    with pytest.raises(ValueError, match="unique"):
        replace(first.revision, objects=(obj, obj))
    with pytest.raises(ValueError, match="exhaustive scope"):
        replace(first.revision, objects=(), scope_completeness="unreviewed")


def test_tiny_targets_and_exclusions_are_explicit(tmp_path):
    release, images = canonical_release()
    sample = release.samples[0]
    tiny = replace(
        sample,
        revision=replace(
            sample.revision,
            objects=(DatasetObject("tiny", "item", Box(10, 10, 10.1, 10.6)),),
        ),
    )
    release = replace(release, samples=(tiny, release.samples[1]))
    manifest = compile_dataset(release, "full-letterbox-320-v1", tmp_path / "small", images)
    assert manifest["release_membership"]["a"] == "excluded"
    assert manifest["excluded_count"] == 1
    clipped_train = replace(
        release.samples[0],
        revision=replace(
            release.samples[0].revision,
            objects=(DatasetObject("clipped", "item", Box(0, 10, 15, 20)),),
        ),
    )
    release = replace(release, samples=(clipped_train, release.samples[1]))
    recipe = Recipe("crop-retention", 320, "auto-area-linear", min_retained_fraction=0.5)
    manifest = compile_dataset(
        release,
        recipe,
        tmp_path / "excluded",
        images,
        crop_policy=lambda sample: Crop(10, 10, 30, 40) if sample.split == "train" else None,
    )
    assert manifest["excluded_count"] == 1
    assert manifest["release_membership"] == {"a": "excluded", "b": "compiled"}
    exclusion = json.loads((tmp_path / "excluded" / "exclusions.jsonl").read_text())
    assert exclusion["asset_id"] == "a"


def test_unsafe_ids_cannot_become_derivative_paths(tmp_path):
    release, images = canonical_release()
    replacement = replace(release.samples[0].asset, asset_id="../../escape")
    assignment = replace(
        release.assignment,
        asset_splits={**release.assignment.asset_splits, "../../escape": "train"},
        group_ids={**release.assignment.group_ids, "../../escape": "a-group"},
    )
    altered = replace(
        release,
        samples=(replace(release.samples[0], asset=replacement), release.samples[1]),
        assignment=assignment,
    )
    images["../../escape"] = images.pop("a")
    manifest = compile_dataset(altered, "full-letterbox-320-v1", tmp_path / "out", images)
    first = manifest["samples"][0]
    identity_hash = hashlib.sha256(replacement.asset_id.encode("utf-8")).hexdigest()
    assert first["image_path"] == f"images/train/{replacement.sha256}-{identity_hash}.jpg"
    assert not (tmp_path / "escape.jpg").exists()


@pytest.mark.parametrize("box", [Box(0, 0, 2, 2), Box(5, 5, 7, 7)])
def test_edge_boxes_remain_bounded_after_decimal_serialization(tmp_path, box):
    release, images = canonical_release()
    output = io.BytesIO()
    Image.new("RGB", (7, 7), "white").save(output, "JPEG")
    asset, images["a"] = process_image(output.getvalue(), asset_id="a", project_id="project")
    train = replace(
        release.samples[0],
        asset=asset,
        revision=replace(
            release.samples[0].revision, objects=(DatasetObject("edge", "item", box),)
        ),
    )
    manifest = compile_dataset(
        replace(release, samples=(train, release.samples[1])),
        "full-letterbox-320-v1",
        tmp_path / "edge",
        images,
    )
    sample = next(item for item in manifest["samples"] if item["asset_id"] == "a")
    row = (tmp_path / "edge" / sample["label_path"]).read_text().split()
    cx, cy, width, height = map(Decimal, row[1:])
    assert 0 <= cx - width / 2 < cx + width / 2 <= 1
    assert 0 <= cy - height / 2 < cy + height / 2 <= 1
    assert float(width) == pytest.approx(2 / 7, abs=1e-8)


def test_normalized_gate_does_not_accept_out_of_range_geometry():
    with pytest.raises(ValueError):
        normalize_box(Box(0, 0, 500, 500), 320, 320)
