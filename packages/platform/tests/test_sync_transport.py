"""Exact-head adapter integration: sync state machines over foundation ports.

Scripted `PlatformPort`/`StoragePort` fakes replay synthetic recorded
exchanges. No network, no credentials, no provider text in durable records.
"""

import json
from dataclasses import replace

import pytest
from test_sync_reconciliation import intent as intent
from test_sync_reconciliation import remote as remote

from labelloop_platform.label_sync import Replacement, replace_once
from labelloop_platform.ports import (
    Operation,
    Outcome,
    PlatformResult,
    StorageResult,
)
from labelloop_platform.release_sync import ReleaseSync, dispatch_once
from labelloop_platform.saved_export import read_saved_export
from labelloop_platform.sync_records import ImageSnapshot, Label, SyncConflict, sha256
from labelloop_platform.sync_transport import (
    FoundationLabelTransport,
    FoundationReleaseTransport,
    FoundationSavedExportSource,
    find_dataset_evidence,
)

ARCHIVE = b"synthetic-release-archive-bytes"
OWNER = "fixture-owner"
SLUG = "release-7-full320"
IMAGE_ID = "507f1f77bcf86cd799439011"


class ScriptedPort:
    """Replays foundation results in order and records every request."""

    def __init__(self, results):
        self.results = list(results)
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        assert self.results, f"no scripted result for {request.operation}"
        return self.results.pop(0)


class ScriptedStorage:
    """Map from (method, url) to results; records transfers."""

    def __init__(self, transfers):
        self.transfers = dict(transfers)
        self.transfers_calls = []

    def put(self, target, content):
        self.transfers_calls.append(("PUT", target, content))
        return self.transfers.pop(("PUT", target.url))

    def get(self, target):
        self.transfers_calls.append(("GET", target, None))
        return self.transfers.pop(("GET", target.url))


@pytest.fixture
def bound_intent(intent):
    return replace(intent, archive_sha256=sha256(ARCHIVE))


def drive_release_sync(journal, port, storage):
    """Dispatch until the state machine stops accepting commands.

    One adapter instance per run: the ephemeral signed target lives on the
    adapter between SIGN and PUT, just as it lives on the worker in
    production, and is never persisted.
    """
    transport = _adapter(port, storage)
    while True:
        try:
            state = dispatch_once(
                journal,
                transport,
                journal.state.intent.operation_id,
                now=0,
            )
        except SyncConflict:
            return journal.state
        journal.state = state


def _adapter(port, storage):
    return FoundationReleaseTransport(port, storage, ARCHIVE)


class Journal:
    def __init__(self, state):
        self.state = state

    def load(self, operation_id):
        assert operation_id == self.state.intent.operation_id
        return self.state

    def compare_and_set(self, expected, updated):
        if expected != self.state:
            return False
        self.state = updated
        return True


def sign_result(session_id="session-1", seconds_into_future=3600):
    return PlatformResult(
        Outcome.SUCCEEDED,
        200,
        {
            "sessionId": session_id,
            "uploadUrl": "https://storage.example/fixture-upload",
            "expiresAt": "2030-01-01T00:00:00Z",
            "headers": {"Content-Type": "application/zip"},
        },
    )


def test_full_release_sync_over_foundation_ports(bound_intent):
    journal = Journal(ReleaseSync(bound_intent, OWNER, SLUG))
    port = ScriptedPort(
        [
            PlatformResult(
                Outcome.SUCCEEDED,
                201,
                {"id": "dataset-1", "owner": OWNER, "dataset": SLUG, "region": "us"},
            ),
            sign_result(),
            PlatformResult(Outcome.SUCCEEDED, 201, {"jobId": "job-1", "status": "queued"}),
        ]
    )
    storage = ScriptedStorage(
        {("PUT", "https://storage.example/fixture-upload"): StorageResult(Outcome.SUCCEEDED, 200)}
    )
    final = drive_release_sync(journal, port, storage)
    assert final.phase.value == "processing"
    assert final.dataset_id == "dataset-1"
    assert final.job_id == "job-1"
    operations = [request.operation for request in port.requests]
    assert operations == [Operation.CREATE_DATASET, Operation.SIGNED_UPLOAD, Operation.INGEST]
    # Persisted intent precedes every dispatch, and IDs are unique per mutation.
    assert [request.operation_id for request in port.requests] == [
        "sync-1:create_dataset:1",
        "sync-1:sign_archive:3",
        "sync-1:ingest_archive:7",
    ]
    # Storage transfer carries only returned headers, never a Platform bearer.
    method, target, content = storage.transfers_calls[0]
    assert method == "PUT"
    assert target.headers == {"Content-Type": "application/zip"}
    assert content == ARCHIVE
    ingest_request = port.requests[-1]
    assert ingest_request.path == {"owner": OWNER, "dataset": SLUG}
    body = dict(ingest_request.body)
    assert body["classMapping"] == {"Object A": 0, "Object B": 1}
    assert body["conflictPolicy"] == "skip"
    assert "targetSplit" not in body


@pytest.mark.parametrize("status_code", [412, 500])
def test_put_412_retires_session_and_5xx_stays_uncertain(bound_intent, status_code):
    from labelloop_platform.release_sync import Phase

    put_result = StorageResult(Outcome.RECONCILE_REQUIRED, status_code)
    journal = Journal(ReleaseSync(bound_intent, OWNER, SLUG))
    port = ScriptedPort(
        [
            PlatformResult(
                Outcome.SUCCEEDED,
                201,
                {"id": "dataset-1", "owner": OWNER, "dataset": SLUG, "region": "us"},
            ),
            sign_result(),
        ]
    )
    storage = ScriptedStorage({("PUT", "https://storage.example/fixture-upload"): put_result})
    transport = _adapter(port, storage)

    # CREATE, SIGN, then PUT against the scripted 412/5xx through the real
    # state machine and adapter wiring.
    journal.state = dispatch_once(journal, transport, "sync-1", now=0)
    assert journal.state.phase == Phase.DATASET
    journal.state = dispatch_once(journal, transport, "sync-1", now=0)
    assert journal.state.phase == Phase.SIGNED
    journal.state = dispatch_once(journal, transport, "sync-1", now=0)
    if status_code == 412:
        assert journal.state.phase == Phase.DATASET
        assert journal.state.retired_sessions == ("session-1",)
        # The retired target is discarded; the adapter holds no live PUT
        # authority until a fresh SIGN response is captured.
        assert not transport._targets
    else:
        assert journal.state.phase == Phase.UNCERTAIN


def test_rejected_create_is_terminal_and_never_replayed(bound_intent):
    journal = Journal(ReleaseSync(bound_intent, OWNER, SLUG))
    port = ScriptedPort([PlatformResult(Outcome.REJECTED, 400)])
    storage = ScriptedStorage({})
    final = drive_release_sync(journal, port, storage)
    assert final.phase.value == "failed"
    assert len(port.requests) == 1


def test_rate_limited_create_is_uncertain_never_replayed(bound_intent):
    journal = Journal(ReleaseSync(bound_intent, OWNER, SLUG))
    port = ScriptedPort([PlatformResult(Outcome.RATE_LIMITED, retry_at=60.0)])
    storage = ScriptedStorage({})
    final = drive_release_sync(journal, port, storage)
    assert final.phase.value == "uncertain"
    assert len(port.requests) == 1


def test_recovery_lookup_matches_frozen_metadata_only(bound_intent):
    port = ScriptedPort(
        [
            PlatformResult(
                Outcome.SUCCEEDED,
                200,
                {
                    "dataset": {
                        "id": "dataset-1",
                        "owner": OWNER,
                        "dataset": SLUG,
                        "visibility": "private",
                        "task": "detect",
                        "classNames": list(bound_intent.class_names),
                        "metadata": {
                            "labelloopOperation": bound_intent.operation_id,
                            "labelloopManifest": bound_intent.manifest_sha256,
                            "labelloopArchive": bound_intent.archive_sha256,
                        },
                    }
                },
            )
        ]
    )
    evidence = find_dataset_evidence(port, OWNER, SLUG, operation_id=bound_intent.operation_id)
    assert evidence is not None
    state = ReleaseSync(bound_intent, OWNER, SLUG)
    persisted, _ = state.prepare(0)
    recovered = (
        persisted.recover_dataset(evidence) if persisted.phase.value == "in_flight" else None
    )
    # prepare() already moves to in_flight; recovery binds the returned ID.
    assert persisted.phase.value == "in_flight"
    assert recovered is not None and recovered.dataset_id == "dataset-1"


def test_absent_dataset_is_none_not_proof_of_failed_create(bound_intent):
    port = ScriptedPort([PlatformResult(Outcome.REJECTED, 404)])
    assert find_dataset_evidence(port, OWNER, SLUG, operation_id=bound_intent.operation_id) is None


def _wire_image(image_id=IMAGE_ID):
    return {
        "properties": {
            "id": image_id,
            "datasetId": "dataset-1",
            "filename": "train.jpg",
            "split": "train",
            "width": 320,
            "height": 320,
            "hash": "opaque-cas-identity",
            "annotationCount": 1,
        },
        "labels": [
            {"classId": 1, "bbox": [0.4556612, 0.4507234, 0.2969123, 0.2440432]},
        ],
        "classNames": ["Object A", "Object B"],
        "labelsTruncated": False,
    }


def test_label_replacement_end_to_end_over_foundation():
    baseline = ImageSnapshot.from_wire(_wire_image())
    canonical = (Label(0, (0.4, 0.4, 0.2, 0.2)),)
    replacement = Replacement.prepare(
        operation_id="fix-1",
        source_id="source-1",
        accepted_revision=2,
        current_revision=2,
        baseline=baseline,
        canonical_labels=canonical,
        target_classes=frozenset({0, 1}),
        window_id="window-1",
        dataset_kind="source_working",
    )

    class Journal:
        def __init__(self, state):
            self.state = state

        def load(self, operation_id):
            return self.state

        def compare_and_set(self, expected, updated):
            self.state = updated
            return True

    class Coordinator:
        def permits(self, replacement):
            return True

    journal = Journal(replacement)
    readback = ImageSnapshot.from_wire(_wire_image())
    port = ScriptedPort(
        [
            PlatformResult(Outcome.SUCCEEDED, 200, _wire_image()),
            PlatformResult(Outcome.SUCCEEDED, 200, None),
            PlatformResult(Outcome.SUCCEEDED, 200, readback_wire(replacement, readback)),
        ]
    )

    # First read observes the baseline; second read after PATCH must equal
    # the intended set, so the readback differs from baseline by design.
    port.data_calls = 0
    result = replace_once(journal, FoundationLabelTransport(port), Coordinator(), "fix-1")
    assert result.phase == "verified"
    assert [request.operation for request in port.requests] == [
        Operation.READ_IMAGE,
        Operation.REPLACE_LABELS,
        Operation.READ_IMAGE,
    ]
    patch = port.requests[1]
    assert patch.path == {"imageId": IMAGE_ID}
    assert set(patch.body) == {"labels"}


def readback_wire(replacement, observed):
    wire = _wire_image()
    wire["labels"] = [label.wire() for label in replacement.intended]
    wire["properties"]["hash"] = observed.storage_hash
    return wire


def _decode_dimensions(data):
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image.load()
        return image.size


def _png(width, height, color):
    import io

    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


def _catalogue_wire(filename, image_id, split, labels):
    return {
        "properties": {
            "id": image_id,
            "datasetId": "dataset-1",
            "filename": filename,
            "split": split,
            "width": 320,
            "height": 320,
            "hash": "opaque-cas-identity",
            "annotationCount": len(labels),
        },
        "labels": labels,
        "classNames": ["Object A", "Object B"],
        "labelsTruncated": False,
    }


def test_saved_export_capture_reconcile_and_freeze_over_foundation(bound_intent):
    """Full chain: dispatch → processing_finished → saved export → reconcile → freeze."""
    from labelloop_platform.reconciliation import (
        ExpectedImage,
        reconcile_release,
    )
    from labelloop_platform.release_sync import Phase

    train_pixels = _png(320, 320, (17, 59, 101))
    val_pixels = _png(320, 320, (101, 59, 17))
    box = [0.4556612, 0.4507234, 0.2969123, 0.2440432]
    export_intent = replace(
        bound_intent,
        images=(
            ExpectedImage(
                "train.jpg",
                "source-1",
                2,
                "train",
                320,
                320,
                (Label(1, tuple(box)),),
                ("obj-1",),
                "{}",
                sha256(train_pixels),
            ),
            ExpectedImage(
                "val.jpg",
                "source-2",
                1,
                "val",
                320,
                320,
                (),
                (),
                "{}",
                sha256(val_pixels),
            ),
        ),
    )

    # Dispatch phase through the release adapter.
    journal = Journal(ReleaseSync(export_intent, OWNER, SLUG))
    port = ScriptedPort(
        [
            PlatformResult(
                Outcome.SUCCEEDED,
                201,
                {"id": "dataset-1", "owner": OWNER, "dataset": SLUG, "region": "us"},
            ),
            sign_result(),
            PlatformResult(Outcome.SUCCEEDED, 201, {"jobId": "job-1", "status": "queued"}),
        ]
    )
    storage = ScriptedStorage(
        {("PUT", "https://storage.example/fixture-upload"): StorageResult(Outcome.SUCCEEDED, 200)}
    )
    final = drive_release_sync(journal, port, storage)
    assert final.phase == Phase.PROCESSING
    journal.state = journal.state.processing_finished("job-1", succeeded=True)

    # Saved-export phase through the foundation adapters.
    record = {
        "id": "dataset-1",
        "owner": OWNER,
        "dataset": SLUG,
        "visibility": "private",
        "task": "detect",
        "status": "ready",
        "classNames": ["Object A", "Object B"],
        "versions": [
            {
                "version": 7,
                "contentHash": "captured-content",
                "imageCount": 2,
                "classCount": 2,
                "annotationCount": 1,
                "splits": {"train": 1, "val": 1, "test": 0},
            }
        ],
    }
    header = {
        "type": "dataset",
        "task": "detect",
        "version": 0,
        "class_names": {"0": "Object A", "1": "Object B"},
    }
    train_row = {
        "type": "image",
        "file": "train.jpg",
        "url": "https://storage.example/train-row",
        "width": 320,
        "height": 320,
        "split": "train",
        "annotations": {"boxes": [[1, *box]]},
    }
    val_row = {
        "type": "image",
        "file": "val.jpg",
        "url": "https://storage.example/val-row",
        "width": 320,
        "height": 320,
        "split": "val",
        "annotations": {"boxes": []},
    }
    ndjson = "\n".join(json.dumps(line) for line in (header, train_row, val_row)).encode() + b"\n"
    export_port = ScriptedPort(
        [
            PlatformResult(Outcome.SUCCEEDED, 200, {"dataset": record}),
            PlatformResult(
                Outcome.SUCCEEDED,
                200,
                {"downloadUrl": "https://storage.example/export.ndjson", "version": 7},
            ),
            PlatformResult(
                Outcome.SUCCEEDED,
                200,
                {
                    "images": [
                        {
                            "id": "image-train",
                            "name": "train.jpg",
                            "imageUrl": "https://storage.example/train-catalog",
                        }
                    ]
                },
            ),
            PlatformResult(
                Outcome.SUCCEEDED,
                200,
                _catalogue_wire("train.jpg", "image-train", "train", [{"classId": 1, "bbox": box}]),
            ),
            PlatformResult(
                Outcome.SUCCEEDED,
                200,
                {
                    "images": [
                        {
                            "id": "image-val",
                            "name": "val.jpg",
                            "imageUrl": "https://storage.example/val-catalog",
                        }
                    ]
                },
            ),
            PlatformResult(
                Outcome.SUCCEEDED, 200, _catalogue_wire("val.jpg", "image-val", "val", [])
            ),
        ]
    )
    export_storage = ScriptedStorage(
        {
            ("GET", "https://storage.example/export.ndjson"): StorageResult(
                Outcome.SUCCEEDED, 200, content=ndjson
            ),
            ("GET", "https://storage.example/train-catalog"): StorageResult(
                Outcome.SUCCEEDED, 200, content=train_pixels
            ),
            ("GET", "https://storage.example/train-row"): StorageResult(
                Outcome.SUCCEEDED, 200, content=train_pixels
            ),
            ("GET", "https://storage.example/val-catalog"): StorageResult(
                Outcome.SUCCEEDED, 200, content=val_pixels
            ),
            ("GET", "https://storage.example/val-row"): StorageResult(
                Outcome.SUCCEEDED, 200, content=val_pixels
            ),
        }
    )
    source = FoundationSavedExportSource(
        export_port, export_storage, decode_dimensions=_decode_dimensions
    )
    capture = read_saved_export(
        source,
        owner=OWNER,
        slug=SLUG,
        dataset_id="dataset-1",
        version=7,
        content_hash="captured-content",
        decode_dimensions=_decode_dimensions,
    )
    assert capture.version == 7
    assert capture.export_sha256 == sha256(ndjson)
    assert capture.origin == "saved_version_export"
    operations = [request.operation for request in export_port.requests]
    assert operations == [
        Operation.READ_DATASET,
        Operation.READ_EXPORT,
        Operation.LIST_IMAGES,
        Operation.READ_IMAGE,
        Operation.LIST_IMAGES,
        Operation.READ_IMAGE,
    ]
    list_request = export_port.requests[2]
    assert list_request.path == {"owner": OWNER, "dataset": SLUG}
    assert list_request.query == {"search": "train.jpg", "includeImageUrls": "true", "limit": 5000}

    mapping = reconcile_release(export_intent, owner=OWNER, dataset_slug=SLUG, capture=capture)
    assert mapping.captured_export_sha256 == sha256(ndjson)
    assert [image.filename for image in mapping.images] == ["train.jpg", "val.jpg"]
    journal.state = journal.state.freeze(mapping)
    assert journal.state.phase == Phase.FROZEN
    assert journal.state.frozen == mapping
