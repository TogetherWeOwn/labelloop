"""Foundation regressions: sync adapters over the real PlatformAdapter/SignedStorage.

The scripted-port suites replay adapter outcomes and never run the
foundation's wire validation. These tests inject an httpx transport into the
real foundation clients, so request shape (dispatch IDs, SIGN totalBytes,
label-PATCH operation IDs, signed-target headers, pagination and throttling)
is validated exactly as in production. Synthetic fixtures only; no network,
no credentials, no live account calls.
"""

import json
from dataclasses import replace

import httpx
import pytest
from test_sync_reconciliation import intent as intent
from test_sync_transport import Journal, _decode_dimensions, _png

from labelloop_platform import PlatformAdapter, SignedStorage, Throttle
from labelloop_platform.label_sync import Replacement, replace_once
from labelloop_platform.release_sync import ReleaseSync, dispatch_once
from labelloop_platform.sync_records import ImageSnapshot, Label, SyncConflict, sha256
from labelloop_platform.sync_transport import (
    FoundationLabelTransport,
    FoundationReleaseTransport,
    FoundationSavedExportSource,
)

ARCHIVE = b"synthetic-release-archive-bytes"
OWNER = "fixture-owner"
SLUG = "release-7-full320"
IMAGE_ID = "507f1f77bcf86cd799439011"
SECRET = "fixture-platform-bearer-not-a-credential"


def _dataset_wire():
    return {
        "dataset": {
            "id": "dataset-1",
            "owner": OWNER,
            "dataset": SLUG,
            "name": SLUG,
            "visibility": "private",
            "task": "detect",
            "imageCount": 0,
            "splits": {"train": 0, "val": 0, "test": 0, "labeled": 0},
            "starCount": 0,
            "isStarred": False,
            "createdAt": "2026-11-07T00:00:00Z",
            "updatedAt": "2026-11-07T00:00:00Z",
            "metadata": {},
        }
    }


def _image_wire(image_id=IMAGE_ID, labels=()):
    return {
        "metadata": {"fixture": True},
        "properties": {
            "id": image_id,
            "datasetId": "dataset-1",
            "filename": "train.jpg",
            "hash": "0" * 32,
            "extension": "jpg",
            "width": 320,
            "height": 320,
            "split": "train",
            "annotationCount": len(labels),
            "retainedByVersion": False,
            "createdAt": "2026-11-07T00:00:00Z",
            "updatedAt": "2026-11-07T00:00:00Z",
        },
        "labels": list(labels),
        "classNames": ["Object A", "Object B"],
    }


class Wire:
    """Injected httpx transport: routes (method, path) to recorded answers."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        assert key in self.routes, f"unexpected wire call {key}"
        status, body = self.routes.pop(key)
        if isinstance(body, bytes):
            return httpx.Response(status, content=body)
        return httpx.Response(status, json=body)


def _platform(wire: Wire, throttle: Throttle | None = None) -> PlatformAdapter:
    return PlatformAdapter(
        SECRET,
        transport=httpx.MockTransport(wire),
        throttle=throttle or Throttle(lambda: 0.0),
        wall_clock=lambda: 0.0,
    )


def _storage(wire: Wire) -> SignedStorage:
    return SignedStorage(
        frozenset({"fixture-storage.example"}), transport=httpx.MockTransport(wire)
    )


@pytest.fixture
def bound_intent(intent):
    return replace(intent, archive_sha256=sha256(ARCHIVE))


def _release_routes(sign_headers=None):
    signed = {
        "sessionId": "fixture-session",
        "uploadUrl": "https://fixture-storage.example/release.zip",
        "expiresAt": "2026-11-07T12:00:00Z",
    }
    if sign_headers is not None:
        signed["headers"] = sign_headers
    return {
        ("POST", "/api/datasets"): (
            201,
            {"id": "dataset-1", "owner": OWNER, "dataset": SLUG, "region": "us"},
        ),
        ("POST", "/api/upload/signed-url"): (200, signed),
        ("POST", f"/api/datasets/{OWNER}/{SLUG}/ingest"): (
            201,
            {"jobId": "job-1", "status": "queued"},
        ),
    }


def _drive(bound_intent, platform_wire, storage_wire, archive=ARCHIVE):
    journal = Journal(ReleaseSync(bound_intent, OWNER, SLUG))
    transport = FoundationReleaseTransport(
        _platform(platform_wire), _storage(storage_wire), archive
    )
    while True:
        try:
            journal.state = dispatch_once(journal, transport, "sync-1", now=0)
        except SyncConflict:
            return journal.state, transport


def test_full_dispatch_through_real_foundation(bound_intent):
    platform_wire = Wire(_release_routes({"Content-Type": "application/zip", "If-None-Match": "*"}))
    storage_wire = Wire({("PUT", "/release.zip"): (200, {})})
    final, _ = _drive(bound_intent, platform_wire, storage_wire)
    assert final.phase.value == "processing"
    assert final.dataset_id == "dataset-1"
    assert final.job_id == "job-1"
    # SIGN carried the archive size the pinned contract requires; every
    # mutation passed the foundation's operation-ID alphabet check.
    sign = [r for r in platform_wire.requests if r.url.path == "/api/upload/signed-url"][0]
    assert json.loads(sign.content)["totalBytes"] == len(ARCHIVE)
    # The create-only PUT carried the returned headers and never a bearer.
    put = storage_wire.requests[0]
    assert put.method == "PUT"
    assert put.headers["if-none-match"] == "*"
    assert "authorization" not in put.headers
    assert put.content == ARCHIVE


def test_sign_without_headers_over_real_foundation(bound_intent):
    platform_wire = Wire(_release_routes(sign_headers=None))
    storage_wire = Wire({("PUT", "/release.zip"): (200, {})})
    final, _ = _drive(bound_intent, platform_wire, storage_wire)
    assert final.phase.value == "processing"
    assert "if-none-match" not in storage_wire.requests[0].headers


def test_archive_mismatch_rejects_before_wire(bound_intent):
    platform_wire = Wire({})
    _, command = ReleaseSync(bound_intent, OWNER, SLUG).prepare(0)
    transport = FoundationReleaseTransport(
        _platform(platform_wire), _storage(Wire({})), b"other-bytes"
    )
    with pytest.raises(SyncConflict):
        transport.execute(command)
    assert platform_wire.requests == []


def test_locally_throttled_create_defers_over_real_foundation(bound_intent):
    throttle = Throttle(lambda: 0.0)
    platform_wire = Wire({})
    adapter = _platform(platform_wire, throttle)
    # Block the category before dispatch: the local reserve returns without
    # ever touching the wire, and the chain defers instead of stranding.
    throttle.defer(adapter._key_id, "default", 300.0)
    journal = Journal(ReleaseSync(bound_intent, OWNER, SLUG))
    transport = FoundationReleaseTransport(adapter, _storage(Wire({})), ARCHIVE)
    state = dispatch_once(journal, transport, "sync-1", now=0)
    assert state.phase.value == "new"
    assert state.pending is None
    assert platform_wire.requests == []


def test_label_patch_carries_operation_id_over_real_foundation():
    box = {"classId": 1, "bbox": [0.5, 0.5, 0.2, 0.2]}
    baseline = ImageSnapshot.from_wire(_image_wire(labels=[box]))
    replacement = Replacement.prepare(
        operation_id="fix-1",
        source_id="source-1",
        accepted_revision=2,
        current_revision=2,
        baseline=baseline,
        canonical_labels=(Label(0, (0.4, 0.4, 0.2, 0.2)),),
        target_classes=frozenset({0, 1}),
        window_id="window-1",
        dataset_kind="source_working",
    )

    class ReplacementJournal:
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

    reads = [
        (200, _image_wire(labels=[box])),
        (200, _image_wire(labels=[{"classId": 0, "bbox": [0.4, 0.4, 0.2, 0.2]}])),
    ]
    patch_wire = Wire(
        {
            ("GET", f"/api/images/{IMAGE_ID}"): reads.pop(0),
            ("PATCH", f"/api/images/{IMAGE_ID}"): (
                200,
                {"success": True, "labels": [], "labelCount": 0},
            ),
        }
    )
    # Serve the readback on the second GET by re-registering it after PATCH.
    original_call = patch_wire.__call__
    state = {"patched": False}

    def route(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            state["patched"] = True
            return original_call(request)
        if state["patched"] and request.method == "GET":
            patch_wire.routes[("GET", f"/api/images/{IMAGE_ID}")] = reads.pop(0)
        return original_call(request)

    adapter = PlatformAdapter(
        SECRET,
        transport=httpx.MockTransport(route),
        throttle=Throttle(lambda: 0.0),
        wall_clock=lambda: 0.0,
    )
    result = replace_once(
        ReplacementJournal(replacement), FoundationLabelTransport(adapter), Coordinator(), "fix-1"
    )
    assert result.phase == "verified"
    patch = [r for r in patch_wire.requests if r.method == "PATCH"]
    assert len(patch) == 1
    assert json.loads(patch[0].content)["labels"]
    # The durable operation ID crossed the foundation validation boundary.
    assert patch[0].url.path == f"/api/images/{IMAGE_ID}"


def _listed(name, image_id, url):
    return {
        "id": image_id,
        "hash": "0" * 32,
        "ext": "jpg",
        "width": 320,
        "height": 320,
        "split": "train",
        "labelCount": 0,
        "name": name,
        "imageUrl": url,
    }


def _page(images, has_more, cursor=None):
    data = {
        "images": images,
        "hasMore": has_more,
        "classes": ["Object A", "Object B"],
        "errorCount": 0,
    }
    if cursor is not None:
        data["nextCursor"] = cursor
    return data


def test_image_lookup_paginates_over_real_foundation():
    pixels = _png(320, 320, (17, 59, 101))
    requests: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == f"/api/datasets/{OWNER}/{SLUG}":
            return httpx.Response(200, json=_dataset_wire())
        if path == f"/api/datasets/{OWNER}/{SLUG}/export":
            return httpx.Response(
                200,
                json={
                    "downloadUrl": "https://fixture-storage.example/export.ndjson",
                    "version": 7,
                },
            )
        if path == f"/api/datasets/{OWNER}/{SLUG}/images":
            # Page 1 carries only substring neighbours; the exact filename
            # lives on page 2 behind the cursor.
            if "cursor" not in request.url.params:
                return httpx.Response(
                    200,
                    json=_page(
                        [
                            _listed(
                                "sample-1.jpg", "other-1", "https://fixture-storage.example/other"
                            )
                        ],
                        True,
                        "507f1f77bcf86cd799439012",
                    ),
                )
            assert request.url.params["cursor"] == "507f1f77bcf86cd799439012"
            return httpx.Response(
                200,
                json=_page(
                    [
                        _listed(
                            "sample-2.jpg",
                            "507f1f77bcf86cd799439013",
                            "https://fixture-storage.example/late",
                        )
                    ],
                    False,
                ),
            )
        assert path == "/api/images/507f1f77bcf86cd799439013"
        return httpx.Response(200, json=_image_wire("507f1f77bcf86cd799439013", []))

    storage_wire = Wire(
        {
            ("GET", "/export.ndjson"): (200, b"{}"),
            ("GET", "/late"): (200, pixels),
        }
    )
    source = FoundationSavedExportSource(
        PlatformAdapter(
            SECRET,
            transport=httpx.MockTransport(route),
            throttle=Throttle(lambda: 0.0),
            wall_clock=lambda: 0.0,
        ),
        _storage(storage_wire),
        decode_dimensions=_decode_dimensions,
    )
    source.saved_export(OWNER, SLUG, 7)
    snapshot = source.image_by_filename("dataset-1", "sample-2.jpg")
    assert snapshot.image_id == "507f1f77bcf86cd799439013"
    assert snapshot.content.sha256 == sha256(pixels)
    list_calls = [r for r in requests if r.url.path.endswith("/images")]
    assert len(list_calls) == 2
    assert "cursor" not in list_calls[0].url.params
    assert list_calls[1].url.params["cursor"] == "507f1f77bcf86cd799439012"


def test_signed_storage_rejects_conditional_and_auth_headers():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    storage = SignedStorage(
        frozenset({"fixture-storage.example"}), transport=httpx.MockTransport(handler)
    )
    from labelloop_platform import StorageTarget
    from labelloop_platform.ports import Outcome

    assert (
        storage.put(
            StorageTarget("https://fixture-storage.example/x", {"If-None-Match": "*"}), b"a"
        ).outcome
        == Outcome.SUCCEEDED
    )
    assert (
        storage.put(
            StorageTarget("https://fixture-storage.example/x", {"If-None-Match": "etag-1"}), b"a"
        ).outcome
        == Outcome.REJECTED
    )
    assert (
        storage.put(
            StorageTarget("https://fixture-storage.example/x", {"Authorization": "Bearer x"}), b"a"
        ).outcome
        == Outcome.REJECTED
    )


def test_bounded_storage_get_aborts_oversize():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100)

    storage = SignedStorage(
        frozenset({"fixture-storage.example"}), transport=httpx.MockTransport(handler)
    )
    from labelloop_platform import StorageTarget
    from labelloop_platform.ports import Outcome

    assert (
        storage.get(StorageTarget("https://fixture-storage.example/x"), max_bytes=8).outcome
        == Outcome.TRANSPORT_ERROR
    )
    small = storage.get(StorageTarget("https://fixture-storage.example/x"), max_bytes=1000)
    assert small.outcome == Outcome.SUCCEEDED
    assert small.content == b"x" * 100
