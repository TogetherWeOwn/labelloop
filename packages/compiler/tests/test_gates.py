"""Separate executable gate checks for malformed internal/compiled label data."""

import json
from dataclasses import replace

import pytest
from support import canonical_release

from labelloop_compiler.compiler import CompilerFailure, compile_dataset
from labelloop_core.domain import DatasetObject
from labelloop_core.geometry import Box, Crop


def test_two_objects_with_identical_geometry_fail_strict_duplicate_row(tmp_path):
    release, images = canonical_release()
    sample = release.samples[0]
    original = sample.revision.objects[0]
    duplicate = DatasetObject("other-object", "item", original.box)
    duplicate_sample = replace(
        sample, revision=replace(sample.revision, objects=(original, duplicate))
    )
    with pytest.raises(CompilerFailure, match="duplicate normalized YOLO row"):
        compile_dataset(
            replace(release, samples=(duplicate_sample, release.samples[1])),
            "full-letterbox-320-v1",
            tmp_path / "out",
            images,
        )


def test_unresolved_object_and_truncated_empty_samples_cannot_pass():
    release, _ = canonical_release()
    with pytest.raises(ValueError):
        replace(
            release.samples[0].revision,
            objects=(DatasetObject("1", "item", Box(0, 0, 1, 1), label_state="ambiguous"),),
        )


def test_small_image_edge_retained_policy(tmp_path):
    release, images = canonical_release()
    sample = release.samples[0]
    edge = replace(
        sample,
        revision=replace(
            sample.revision,
            objects=(DatasetObject("1", "item", Box(0, 0, 1, 1), truncated_at_image_edge=True),),
        ),
    )
    manifest = compile_dataset(
        replace(release, samples=(edge, release.samples[1])),
        "full-letterbox-320-v1",
        tmp_path / "edge",
        images,
    )
    assert manifest["samples"][0]["objects"][0]["policy"] == "retained"
    assert "image_edge" in manifest["samples"][0]["objects"][0]["flags"]


def test_crop_zero_area_objects_are_absent_not_zero_width_labels(tmp_path):
    release, images = canonical_release()
    samples = (
        release.samples[0],
        replace(release.samples[1], revision=replace(release.samples[1].revision, objects=())),
    )
    release = replace(release, samples=samples)
    manifest = compile_dataset(
        release,
        "full-letterbox-320-v1",
        tmp_path / "crop",
        images,
        crop_policy=lambda _: Crop(0, 0, 5, 5),
    )
    assert manifest["excluded_count"] == 1
    assert manifest["release_membership"] == {"a": "excluded", "b": "compiled"}
    assert manifest["samples"][0]["verified_negative"] is True
    assert manifest["samples"][0]["objects"] == []
    exclusion = json.loads((tmp_path / "crop" / "exclusions.jsonl").read_text())
    assert exclusion["asset_id"] == "a"
    assert (tmp_path / "crop" / manifest["samples"][0]["label_path"]).read_bytes() == b""
