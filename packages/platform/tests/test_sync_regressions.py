"""Regression cases from the initial independent read of the synchronization diff."""

import json
from dataclasses import replace

import pytest
from test_label_sync import Coordinator, Journal, Transport
from test_label_sync import baseline as baseline
from test_label_sync import replacement as replacement
from test_sync_reconciliation import intent as intent
from test_sync_reconciliation import reconcile
from test_sync_reconciliation import remote as remote

from labelloop_platform.label_sync import inverse_edit, replace_once
from labelloop_platform.reconciliation import CapturedVersion, ContentEvidence
from labelloop_platform.release_sync import DatasetEvidence, Phase, ReleaseSync
from labelloop_platform.sync_records import Label, SyncConflict, same_labels


def test_already_satisfied_write_never_sends_patch(replacement):
    no_op = replace(replacement, intended=replacement.baseline.labels)
    journal = Journal(no_op)
    transport = Transport(journal, no_op.baseline, no_op.baseline)
    assert replace_once(journal, transport, Coordinator(), "write-1").phase == "verified"
    assert not transport.writes
    # Old persisted intents with no observable delta cannot prove a delayed PATCH finished.
    outstanding = replace(no_op, phase="uncertain")
    assert outstanding.recover(no_op.baseline).phase == "conflict"


def test_empty_task_branches_do_not_preserve_stale_detection(replacement):
    empty = Label.from_wire(
        {"classId": 0, "bbox": [0.5, 0.5, 0.2, 0.1], "segments": [], "keypoints": []}
    )
    assert empty.detection_only
    snapshot = replace(replacement.baseline, labels=(empty, replacement.baseline.labels[1]))
    prepared = type(replacement).prepare(
        operation_id="empty-task",
        source_id="source",
        accepted_revision=3,
        current_revision=3,
        baseline=snapshot,
        canonical_labels=(Label(0, (0.3, 0.4, 0.1, 0.2)),),
        target_classes=frozenset({0}),
        window_id="window",
        dataset_kind="source_working",
    )
    assert len(prepared.intended) == 2 and empty not in prepared.intended


def test_mixed_task_rounding_is_numeric_but_structure_and_skeleton_are_exact():
    before = Label.from_wire(
        {
            "classId": 1,
            "bbox": [0.5, 0.5, 0.2, 0.2],
            "segments": [0.123456, 0.2, 0.3, 0.4, 0.5, 0.6],
            "keypoints": [0, 0.234567],
            "skeletonId": "approved",
        }
    )
    rounded = Label.from_wire(
        {
            "classId": 1,
            "bbox": [0.5, 0.5, 0.2, 0.2],
            "segments": [0.12346, 0.2, 0.3, 0.4, 0.5, 0.6],
            "keypoints": [0.0, 0.23457],
            "skeletonId": "approved",
        }
    )
    assert same_labels((before,), (rounded,))
    wrong = rounded.wire()
    wrong["skeletonId"] = "different"
    assert not same_labels((before,), (Label.from_wire(wrong),))
    wrong = rounded.wire()
    wrong["segments"][0] += 0.00002
    assert not same_labels((before,), (Label.from_wire(wrong),))


def test_newly_internal_crop_edge_edit_requires_source_review():
    transform = json.dumps(
        {
            "output_size": 320,
            "valid_content_rectangle": [0, 0, 320, 320],
            "source_width": 200,
            "source_height": 200,
            "inverse": [[0.3125, 0, 50], [0, 0.3125, 20], [0, 0, 1]],
            "crop": {"x": 50, "y": 20, "width": 100, "height": 100},
        }
    )
    proposed = inverse_edit(Label(0, (0.1, 0.4, 0.2, 0.4)), transform, original_was_clipped=False)
    assert proposed.requires_source_review
    assert proposed.retained_source_box == pytest.approx((50, 40, 70, 80))


def dataset_evidence(intent, **changes):
    return DatasetEvidence(
        **{
            "dataset_id": "dataset-1",
            "owner": "fixture",
            "slug": "release-7-full320",
            "visibility": "private",
            "task": "detect",
            "class_names": intent.class_names,
            "operation_id": intent.operation_id,
            "manifest_sha256": intent.manifest_sha256,
            "archive_sha256": intent.archive_sha256,
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"visibility": "public"},
        {"task": "segment"},
        {"owner": "unrelated"},
        {"slug": "unrelated"},
        {"operation_id": "other-create"},
        {"manifest_sha256": "f" * 64},
        {"archive_sha256": "f" * 64},
    ],
)
def test_create_recovery_never_adopts_public_or_unrelated_dataset(intent, changes):
    state, _ = ReleaseSync(intent, "fixture", "release-7-full320").prepare(0)
    with pytest.raises(SyncConflict, match="identity"):
        state.recover_dataset(dataset_evidence(intent, **changes))
    assert state.recover_dataset(dataset_evidence(intent)).phase == Phase.DATASET


def test_wrong_remote_pixels_cannot_freeze_matching_filename_negative(intent, remote):
    wrong_pixels = replace(remote[1], content=ContentEvidence("f" * 64, 320, 320))
    with pytest.raises(SyncConflict, match="content"):
        reconcile(intent, (remote[0], wrong_pixels))


def test_content_digest_is_not_a_claim_about_other_raster_dimensions(intent, remote):
    wrong_dimensions = replace(
        remote[1], content=ContentEvidence(intent.images[1].image_sha256, 640, 640)
    )
    with pytest.raises(SyncConflict, match="content"):
        reconcile(intent, (remote[0], wrong_dimensions))


def test_live_snapshots_cannot_be_passed_as_a_saved_version(intent, remote):
    # The reconciler consumes a version-bound export capsule, not live ImageSnapshot arguments.
    with pytest.raises(SyncConflict, match="saved version"):
        reconcile(
            intent,
            remote,
            capture=CapturedVersion(
                "dataset-1",
                7,
                "captured-content",
                intent.class_names,
                remote,
                True,
                True,
                "",
                "live_listing",
            ),
        )
    capsule = CapturedVersion(
        "dataset-1",
        7,
        "captured-content",
        intent.class_names,
        remote,
        True,
        True,
        "e" * 64,
        "saved_version_export",
    )
    assert reconcile(intent, remote, capture=capsule).captured_export_sha256 == "e" * 64
