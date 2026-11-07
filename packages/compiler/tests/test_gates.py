"""Separate executable gate checks for malformed internal/compiled label data."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import build_fixtures
import load_canonical
import pytest
from support import canonical_release

from labelloop_compiler.compiler import CompilerFailure, compile_dataset
from labelloop_core.domain import DatasetObject
from labelloop_core.geometry import Box, Crop


@pytest.mark.parametrize("optimization", [[], ["-O"]])
def test_fixture_digest_check_survives_python_optimization(optimization):
    script = """
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root / path) for path in (
    'fixtures', 'packages/compiler/src', 'packages/core/src'
)]
import load_canonical
labels = load_canonical.load_labels('cc0-interface')
labels['images'][0]['normalized_sha256'] = '0' * 64
load_canonical.load_labels = lambda domain: labels
load_canonical.FixtureDomain('cc0-interface')
"""
    result = subprocess.run(
        [sys.executable, *optimization, "-c", script, str(load_canonical.ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "ValueError: fixture normalized digest mismatch:" in result.stderr


def test_fixture_metadata_pins_text_io_and_serializes_actual_seed(tmp_path, monkeypatch):
    images = tmp_path / "fixtures" / "cc0-interface" / "images"
    images.mkdir(parents=True)
    for source in (load_canonical.ROOT / "fixtures" / "cc0-interface" / "images").glob("*.jpg"):
        (images / source.name).write_bytes(source.read_bytes())
    original_assign = build_fixtures.assign_splits
    monkeypatch.setattr(build_fixtures, "ROOT", tmp_path)
    monkeypatch.setattr(
        build_fixtures, "assign_splits", lambda evidence: original_assign(evidence, seed=17)
    )
    original_write = Path.write_text
    writes = []

    def write_text(path, text, *, encoding=None, newline=None):
        assert encoding == "utf-8"
        assert newline == "\n"
        writes.append(path)
        return original_write(path, text, encoding=encoding, newline=newline)

    monkeypatch.setattr(Path, "write_text", write_text)
    build_fixtures.build("cc0-interface")
    assert len(writes) == 1
    assert writes[0].read_bytes().endswith(b"\n")
    assert b"\r" not in writes[0].read_bytes()
    original_read = Path.read_text
    reads = []

    def read_text(path, *, encoding=None):
        assert encoding == "utf-8"
        reads.append(path)
        return original_read(path, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(load_canonical, "GOLDEN", writes[0].parent)
    labels = load_canonical.load_labels("cc0-interface")
    assert reads == writes
    assert labels["split_assignment"]["seed"] == 17


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


def test_unresolved_object_cannot_pass_training_eligibility():
    release, _ = canonical_release()
    with pytest.raises(ValueError, match="unresolved"):
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
            objects=(DatasetObject("1", "item", Box(0, 0, 5, 5), truncated_at_image_edge=True),),
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


def test_absent_crop_object_does_not_create_class_support(tmp_path):
    release, images = canonical_release()
    val = release.samples[1]
    outside = DatasetObject("outside", "item", Box(100, 20, 180, 120))
    sample = replace(
        val,
        revision=replace(val.revision, objects=(val.revision.objects[0], outside)),
    )
    manifest = compile_dataset(
        replace(release, samples=(release.samples[0], sample)),
        "full-letterbox-320-v1",
        tmp_path / "outside",
        images,
        crop_policy=lambda sample_: Crop(0, 0, 80, 150) if sample_.split == "val" else None,
    )
    compiled = next(sample_ for sample_ in manifest["samples"] if sample_["split"] == "val")
    absent = next(item for item in compiled["objects"] if item["object_id"] == "outside")
    assert absent["status"] == "absent_after_crop"
    assert manifest["class_support"]["item"]["val"] == 1
    assert manifest["class_support"]["item"]["validation_supported"] is True


def test_tiny_visible_target_cannot_leave_partial_labels(tmp_path):
    release, images = canonical_release()
    train = release.samples[0]
    tiny = DatasetObject("tiny", "item", Box(10, 10, 10.1, 10.6))
    sample = replace(
        train,
        revision=replace(train.revision, objects=(train.revision.objects[0], tiny)),
    )
    with pytest.raises(CompilerFailure, match="recrop or explicitly exclude"):
        compile_dataset(
            replace(release, samples=(sample, release.samples[1])),
            "full-letterbox-320-v1",
            tmp_path / "partial",
            images,
        )
    assert not (tmp_path / "partial").exists()
