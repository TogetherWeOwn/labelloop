"""Offline Platform acceptance checks 1, 8, 10, 20 and 27."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from labelloop_platform.reconciliation import (
    CapturedVersion,
    ContentEvidence,
    ExpectedImage,
    ReleaseIntent,
    ingest_dimensions,
    reconcile_release,
)
from labelloop_platform.sync_records import (
    ImageSnapshot,
    Label,
    SyncConflict,
    canonical_json,
    require_complete_prediction,
    same_labels,
    sha256,
)


@pytest.fixture
def intent():
    return ReleaseIntent(
        "sync-1",
        "release-7",
        "full-letterbox-320-v1",
        "a" * 64,
        "b" * 64,
        (("stable-a", 0), ("stable-b", 1)),
        ("Object A", "Object B"),
        (
            ExpectedImage(
                "train.jpg",
                "source-1",
                2,
                "train",
                320,
                320,
                (Label(1, (0.4556612, 0.4507234, 0.2969123, 0.2440432)),),
                ("obj-1",),
                "{}",
                "c" * 64,
            ),
            ExpectedImage("val.jpg", "source-2", 1, "val", 320, 320, (), (), "{}", "d" * 64),
        ),
    )


@pytest.fixture
def remote(intent):
    return tuple(
        ImageSnapshot(
            str(index),
            "dataset-1",
            image.filename,
            image.split,
            image.width,
            image.height,
            intent.class_names,
            image.labels,
            content=ContentEvidence(image.image_sha256, image.width, image.height),
        )
        for index, image in enumerate(intent.images)
    )


def reconcile(intent, remote, **changes):
    capsule = CapturedVersion(
        **{
            "dataset_id": "dataset-1",
            "version": 7,
            "content_hash": "captured-content",
            "class_names": intent.class_names,
            "images": remote,
            "listing_complete": True,
            "processing_complete": True,
            "export_sha256": "e" * 64,
            "origin": "saved_version_export",
            **{key: value for key, value in changes.items() if key != "capture"},
        }
    )
    return reconcile_release(
        intent,
        owner="fixture",
        dataset_slug="release-7-full320",
        capture=changes.get("capture", capsule),
    )


def test_frozen_release_maps_every_image_class_revision_and_transform(intent, remote):
    mapping = reconcile(intent, remote)
    assert mapping.class_map == intent.class_map
    assert mapping.manifest_sha256 == intent.manifest_sha256
    assert mapping.images[0].source_id == "source-1"
    assert mapping.images[0].revision == 2
    assert mapping.images[0].object_ids == ("obj-1",)
    assert mapping.images[0].transform_json == "{}"
    mapping.verify_model_capture(
        {
            "datasetId": "dataset-1",
            "datasetVersion": {
                "version": 7,
                "contentHash": "captured-content",
            },
        }
    )
    with pytest.raises(SyncConflict, match="different dataset version"):
        mapping.verify_model_capture(
            {
                "datasetId": "dataset-1",
                "datasetVersion": {
                    "version": 8,
                    "contentHash": "captured-content",
                },
            }
        )


@pytest.mark.parametrize(
    "change",
    [
        {"class_names": ("Object B", "Object A")},
        {"listing_complete": False},
        {"processing_complete": False},
        {"version": 0},
        {"content_hash": ""},
    ],
)
def test_unreconciled_or_reordered_release_fails(intent, remote, change):
    with pytest.raises(SyncConflict):
        reconcile(intent, remote, **change)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda images: images[:1],
        lambda images: (*images, images[0]),
        lambda images: (replace(images[0], split="val"), images[1]),
        lambda images: (replace(images[0], width=321), images[1]),
        lambda images: (replace(images[0], labels=()), images[1]),
        lambda images: (
            replace(images[0], labels=(Label(0, images[0].labels[0].bbox),)),
            images[1],
        ),
        lambda images: (replace(images[0], labels=(Label(1, (0.4, 0.4, 0.1, 0.1)),)), images[1]),
        lambda images: (replace(images[0], labels_truncated=True), images[1]),
        lambda images: (replace(images[0], dataset_id="other"), images[1]),
        lambda images: (replace(images[0], class_names=("Object B", "Object A")), images[1]),
    ],
)
def test_per_image_membership_split_label_class_geometry_fail(intent, remote, mutation):
    with pytest.raises(SyncConflict):
        reconcile(intent, mutation(remote))


def test_explicit_class_mapping_never_overrides_archive_splits(intent):
    assert intent.ingest_body("session-1") == {
        "sessionId": "session-1",
        "classMapping": {"Object A": 0, "Object B": 1},
        "conflictPolicy": "skip",
    }
    with pytest.raises(SyncConflict, match="nonempty val"):
        replace(intent, images=(intent.images[0],))


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (320, 320, ((320, 320),)),
        (4096, 28, ((4096, 28),)),
        (8192, 4096, ((4096, 2048),)),
        (3000, 9000, ((1365, 4096), (1366, 4096))),
    ],
)
def test_only_documented_ingest_resize_is_allowed(width, height, expected):
    assert ingest_dimensions(width, height) == expected


def test_unexpected_resize_and_tiny_inputs_fail(intent, remote):
    oversized = replace(
        intent, images=(replace(intent.images[0], width=9000, height=3000), intent.images[1])
    )
    resized = replace(
        remote[0],
        width=4096,
        height=1365,
        content=ContentEvidence(intent.images[0].image_sha256, 9000, 3000),
    )
    reconcile(oversized, (resized, remote[1]))
    with pytest.raises(SyncConflict, match="geometry"):
        reconcile(oversized, (replace(remote[0], width=4095, height=1365), remote[1]))
    with pytest.raises(SyncConflict, match="short side"):
        ingest_dimensions(100, 27)


@pytest.mark.parametrize(
    "prediction",
    [
        {"partial": True},
        {"partialImages": ["image-1"]},
        {"partial": False, "partialImages": ["image-1"]},
        {"labelsTruncated": True},
        {"partial": "false"},
    ],
)
def test_platform_partial_output_never_passes_completeness(prediction):
    with pytest.raises(SyncConflict, match="completeness"):
        require_complete_prediction(prediction)


def test_complete_prediction_is_only_proposal_evidence():
    assert require_complete_prediction({"partial": False, "partialImages": []}) is None


def test_asymmetric_roundtrip_tolerates_five_decimals_not_wrong_geometry(intent):
    label = intent.images[0].labels[0]
    rounded = Label(1, tuple(round(value, 5) for value in label.bbox))
    assert same_labels((label,), (rounded,))
    assert not same_labels((label,), (Label(1, (0.45566, 0.45072, 0.24404, 0.29691)),))
    assert not same_labels((label, label), (rounded,))
    # Greedy pairing would fail here: both may match the first actual box.
    assert same_labels(
        (Label(0, (0.5, 0.5, 0.2, 0.2)), Label(0, (0.500008, 0.5, 0.2, 0.2))),
        (Label(0, (0.500004, 0.5, 0.2, 0.2)), Label(0, (0.499996, 0.5, 0.2, 0.2))),
    )


def test_compiler_manifest_bridge_retains_source_and_class_identity():
    labels = b"1 0.45566120 0.45072340 0.29691230 0.24404320\n"
    files = {
        "images/val/source.jpg": b"fixture image bytes",
        "labels/val/source.txt": labels,
        "data.yaml": b'val: images/val\nnames:\n  0: "A"\n  1: "B"\n',
    }
    manifest = {
        "release_id": "release-1",
        "recipe": {"recipe_id": "full-letterbox-320-v1"},
        "class_map": {"stable-b": 1, "stable-a": 0},
        "file_hashes": {path: sha256(data) for path, data in files.items()},
        "samples": [
            {
                "image_path": "images/val/source.jpg",
                "label_path": "labels/val/source.txt",
                "asset_id": "source",
                "revision": 3,
                "split": "val",
                "image_size": [320, 320],
                "verified_negative": False,
                "transform": {"inverse": "recorded"},
                "image_sha256": sha256(files["images/val/source.jpg"]),
                "objects": [
                    {
                        "class_id": "stable-b",
                        "class_index": 1,
                        "object_id": "obj-7",
                        "yolo_row": labels.decode().strip(),
                    }
                ],
            }
        ],
    }
    release = ReleaseIntent.from_compiler(
        "op",
        canonical_json(manifest).encode(),
        b"zip",
        {"stable-a": "A", "stable-b": "B"},
        files.__getitem__,
    )
    assert release.class_names == ("A", "B")
    assert release.images[0].object_ids == ("obj-7",)
    assert release.images[0].revision == 3
    with pytest.raises(SyncConflict, match="frozen archive catalog"):
        ReleaseIntent.from_compiler(
            "op",
            canonical_json(manifest).encode(),
            b"zip",
            {"stable-a": "Renamed A", "stable-b": "B"},
            files.__getitem__,
        )
    wrong_lineage = json.loads(json.dumps(manifest))
    obj = wrong_lineage["samples"][0]["objects"][0]
    obj["class_id"], obj["class_index"] = "stable-a", 0
    with pytest.raises(SyncConflict, match="remapped"):
        ReleaseIntent.from_compiler(
            "op",
            canonical_json(wrong_lineage).encode(),
            b"zip",
            {"stable-a": "A", "stable-b": "B"},
            files.__getitem__,
        )
    files["labels/val/source.txt"] = b""
    with pytest.raises(SyncConflict, match="changed"):
        ReleaseIntent.from_compiler(
            "op",
            canonical_json(manifest).encode(),
            b"zip",
            {"stable-a": "A", "stable-b": "B"},
            files.__getitem__,
        )


def test_contract_fixture_image_decode_and_truncation():
    path = Path(__file__).parent / "fixtures" / "sync-http.json"
    fixture = json.loads(path.read_text())
    snapshot = ImageSnapshot.from_wire(fixture["image"]["response"])
    assert snapshot.labels[0].bbox == (0.45566, 0.45072, 0.29691, 0.24404)
    assert Label.from_wire(snapshot.labels[1].wire()) == snapshot.labels[1]
    assert "segments" in snapshot.labels[1].wire()
    bad = fixture["image"]["response"]
    bad["properties"]["annotationCount"] += 1
    with pytest.raises(SyncConflict, match="omitted labels"):
        ImageSnapshot.from_wire(bad)
    bad["labelsTruncated"] = True
    assert ImageSnapshot.from_wire(bad).labels_truncated


def test_mapping_serializes_without_losing_frozen_lineage(intent, remote):
    record = asdict(reconcile(intent, remote))
    assert json.loads(json.dumps(record))["images"][0]["revision"] == 2
