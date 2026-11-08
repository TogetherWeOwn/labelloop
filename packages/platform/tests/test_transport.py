import asyncio
import copy
import json
import logging
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from labelloop_platform import (
    Operation,
    Outcome,
    PlatformAdapter,
    PlatformRequest,
    SignedStorage,
    StorageTarget,
    Throttle,
)
from labelloop_platform.transport import retry_delay

FIXTURES = json.loads((Path(__file__).parent / "fixtures/exchanges.json").read_text())
SECRET = "fixture-platform-bearer-not-a-credential"


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


class Replay:
    def __init__(self, *exchanges: str | Exception) -> None:
        self.exchanges = list(exchanges)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # Public synthetic marker, never a credential source. It matches the
        # fixture key to prove dependency wire diagnostics are suppressed.
        logging.getLogger("httpcore.http11").debug(
            "wire header fixture-platform-bearer-not-a-credential"
        )
        exchange = self.exchanges.pop(0)
        if isinstance(exchange, Exception):
            raise exchange
        fixture = copy.deepcopy(FIXTURES[exchange])
        status = fixture.pop("status")
        return httpx.Response(status, **fixture)


def adapter(replay: Replay, clock: Clock | None = None) -> PlatformAdapter:
    return PlatformAdapter(
        SECRET,
        transport=httpx.MockTransport(replay),
        throttle=Throttle(clock or Clock()),
        wall_clock=lambda: 0,
    )


def training(operation_id: str = "start-1") -> PlatformRequest:
    return PlatformRequest(
        Operation.START_TRAINING,
        operation_id,
        body={
            "modelId": "fixture-model",
            "gpuType": "rtx-4090",
            "captureDatasetVersion": True,
            "trainArgs": {
                "model": "ul://fixture/base/model",
                "data": "ul://fixture/datasets/release",
                "epochs": 1,
            },
        },
    )


def test_fixed_origin_owned_timeouts_no_wake_and_no_ambient_auth(monkeypatch) -> None:
    monkeypatch.setenv("ULTRALYTICS_API_KEY", "ambient-must-not-be-used")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    replay = Replay("gpu")
    client = adapter(replay)
    assert client.execute(PlatformRequest(Operation.GPU_AVAILABILITY)).outcome == Outcome.SUCCEEDED
    assert len(replay.requests) == 1
    request = replay.requests[0]
    assert str(request.url) == "https://platform.ultralytics.com/api/training/gpu-availability"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert request.extensions["timeout"] == {"connect": 10, "read": 60, "write": 60, "pool": 10}
    assert client._sdk._client._max_retries == 0
    assert "POST" not in client._sdk._client._retry_methods
    assert not client._http.event_hooks["response"]
    client.close()


@pytest.mark.parametrize(
    "wire_request",
    [
        PlatformRequest("https://untrusted.example"),  # type: ignore[arg-type]
        PlatformRequest("read_dataset"),  # type: ignore[arg-type]
        PlatformRequest(Operation.READ_DATASET, path={"owner": "..", "dataset": "release"}),
        PlatformRequest(Operation.READ_DATASET, path={"owner": "%2e%2e", "dataset": "release"}),
        PlatformRequest(Operation.READ_IMAGE, path={"imageId": "a/b"}),
        PlatformRequest(Operation.READ_IMAGE, path={"imageId": "x?url=https://evil.example"}),
        PlatformRequest(Operation.READ_IMAGE, path={"imageId": "☃"}),
        PlatformRequest(Operation.GPU_AVAILABILITY, query={"server": "https://untrusted.example"}),
        PlatformRequest(Operation.START_TRAINING, body={}),
        replace(training(), operation_id=None),
        replace(training(), body={"modelId": "x", "trainArgs": {}}),
        PlatformRequest(
            Operation.INGEST,
            "ingest-1",
            path={"owner": "fixture", "dataset": "release"},
            body={"sourceUrl": "https://untrusted.example/archive.zip"},
        ),
    ],
)
def test_invalid_endpoint_or_wire_input_never_dispatches(wire_request) -> None:
    replay = Replay()
    assert adapter(replay).execute(wire_request).outcome == Outcome.REJECTED
    assert replay.requests == []


def test_ingest_and_signed_upload_suppress_sdk_localhost_hooks() -> None:
    replay = Replay("signed_upload", "ingest")
    client = adapter(replay)
    result = client.execute(
        PlatformRequest(
            Operation.SIGNED_UPLOAD,
            "upload-1",
            body={
                "assetId": "fixture-dataset",
                "contentType": "application/zip",
                "totalBytes": 42,
                "assetType": "datasets",
                "filename": "dataset.zip",
            },
        )
    )
    assert result.outcome == Outcome.SUCCEEDED
    assert "uploadUrl" not in repr(result)
    ingest = PlatformRequest(
        Operation.INGEST,
        "ingest-1",
        path={"owner": "fixture", "dataset": "release"},
        body={"sessionId": "fixture-session", "classMapping": {"object": 0}},
    )
    assert client.execute(ingest).outcome == Outcome.SUCCEEDED
    assert len(replay.requests) == 2
    assert all(r.url.host == "platform.ultralytics.com" for r in replay.requests)
    for bad_field in ("targetSplit", "reference", "sourceUrl"):
        bad = replace(ingest, operation_id="bad-ingest", body={**ingest.body, bad_field: "bad"})
        assert client.execute(bad).outcome == Outcome.REJECTED
    assert len(replay.requests) == 2


@pytest.mark.parametrize(
    ("exchange", "outcome"),
    [
        ("paid_paused", Outcome.PAID_PAUSED),
        ("conflict", Outcome.RECONCILE_REQUIRED),
        ("unavailable", Outcome.RECONCILE_REQUIRED),
        ("malformed", Outcome.RECONCILE_REQUIRED),
        ("redirect", Outcome.RECONCILE_REQUIRED),
        ("gpu", Outcome.RECONCILE_REQUIRED),  # JSON object missing training response fields
        (httpx.ReadTimeout(SECRET), Outcome.RECONCILE_REQUIRED),
    ],
)
def test_mutations_never_blindly_repeat_and_never_log_raw_errors(exchange, outcome, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    replay = Replay(exchange)
    client = adapter(replay)
    request = training()
    result = client.execute(request)
    assert result.outcome == outcome
    assert client.execute(request) == result
    assert len(replay.requests) == 1
    assert SECRET not in caplog.text
    assert SECRET not in repr(result)
    different_body = replace(request, body={**request.body, "modelId": "different"})
    assert client.execute(different_body).outcome == Outcome.REJECTED
    assert len(replay.requests) == 1
    # Contextual filtering must not suppress unrelated application's dependency logs.
    logging.getLogger("httpcore.http11").debug("unrelated-wire-diagnostic")
    assert "unrelated-wire-diagnostic" in caplog.text


@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
def test_interrupted_mutation_keeps_predispatch_latch(error_type, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    calls = []
    latched_at_dispatch = []

    def interrupted(request):
        calls.append(request)
        latched_at_dispatch.append("start-1" in client._writes)
        raise error_type(SECRET)

    client = PlatformAdapter(
        SECRET, transport=httpx.MockTransport(interrupted), throttle=Throttle(Clock())
    )
    request = training()
    try:
        with pytest.raises(error_type):
            client.execute(request)
        assert client._guard.expected is None
        assert latched_at_dispatch == [True]
        assert client.execute(request).outcome == Outcome.RECONCILE_REQUIRED
        different_body = replace(request, body={**request.body, "modelId": "different"})
        assert client.execute(different_body).outcome == Outcome.REJECTED
        assert len(calls) == 1
        assert SECRET not in caplog.text
        logging.getLogger("httpcore.http11").debug("unrelated-after-interruption")
        assert "unrelated-after-interruption" in caplog.text
    finally:
        client.close()


def test_predispatch_throttle_does_not_latch_mutation() -> None:
    clock = Clock()
    replay = Replay("paid_paused")
    client = adapter(replay, clock)
    client._throttle.defer(client._key_id, "training", 120)
    assert client.execute(training()).outcome == Outcome.RATE_LIMITED
    assert client._writes == {}
    assert replay.requests == []
    clock.now = 120
    assert client.execute(training()).outcome == Outcome.PAID_PAUSED
    assert client.execute(training()).outcome == Outcome.PAID_PAUSED
    assert len(replay.requests) == 1
    client.close()


class BrokenBody(httpx.SyncByteStream):
    def __iter__(self):
        raise httpx.ReadTimeout(SECRET)
        yield b""  # pragma: no cover


@pytest.mark.parametrize("status", [402, 429])
@pytest.mark.parametrize("mutation", [False, True])
def test_known_rejection_status_survives_failed_body_read(status, mutation, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    calls = []

    def broken(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "120"}, stream=BrokenBody())

    clock = Clock()
    throttle = Throttle(clock)
    client = PlatformAdapter(SECRET, transport=httpx.MockTransport(broken), throttle=throttle)
    request = training() if mutation else PlatformRequest(Operation.GPU_AVAILABILITY)
    result = client.execute(request)
    assert result.status_code == status
    assert result.outcome == (Outcome.PAID_PAUSED if status == 402 else Outcome.RATE_LIMITED)
    if status == 429:
        assert result.retry_at == 120
        # A separate intent on the shared key/category cannot bypass the known backoff.
        following = training("start-2") if mutation else request
        assert client.execute(following).retry_at == 120
        assert len(calls) == 1
    assert SECRET not in caplog.text


def test_mutation_replay_preserves_original_remote_identity() -> None:
    replay = Replay("signed_upload")
    client = adapter(replay)
    request = PlatformRequest(
        Operation.SIGNED_UPLOAD,
        "upload-1",
        body={
            "assetId": "fixture-dataset",
            "contentType": "application/zip",
            "totalBytes": 42,
            "assetType": "datasets",
            "filename": "dataset.zip",
        },
    )
    result = client.execute(request)
    assert result.outcome == Outcome.SUCCEEDED
    result.data.pop("sessionId")
    result.data["headers"]["Content-Type"] = "tampered"
    replayed = client.execute(request)
    assert replayed.data["sessionId"] == "fixture-session"
    assert replayed.data["headers"]["Content-Type"] == "application/zip"
    replayed.data.clear()
    assert client.execute(request).data["sessionId"] == "fixture-session"
    assert len(replay.requests) == 1


def test_429_obeys_retry_after_without_sdk_post_retry() -> None:
    clock = Clock()
    replay = Replay("rate_limit", "paid_paused")
    client = adapter(replay, clock)
    result = client.execute(training())
    assert result.outcome == Outcome.RATE_LIMITED
    assert result.retry_at == 120
    assert client.execute(training()).outcome == Outcome.RATE_LIMITED
    clock.now = 119
    assert client.execute(training("start-2")).retry_at == 120
    assert len(replay.requests) == 1
    clock.now = 120
    assert client.execute(training()).outcome == Outcome.PAID_PAUSED
    assert len(replay.requests) == 2


def test_per_key_category_limits_and_shared_429_backoff() -> None:
    clock = Clock()
    throttle = Throttle(clock)
    for _ in range(10):
        assert throttle.reserve("key-a", "training") is None
    assert throttle.reserve("key-a", "training") == 60
    assert throttle.reserve("key-b", "training") is None
    assert throttle.reserve("key-a", "upload") is None
    assert throttle.defer("key-a", "upload", 120) == 120
    assert throttle.reserve("key-a", "upload") == 120
    clock.now = 60
    assert throttle.reserve("key-a", "training") is None
    clock.now = 120
    assert throttle.reserve("key-a", "upload") is None


@pytest.mark.parametrize(
    ("header", "expected"),
    [("120", 120), ("Thu, 01 Jan 1970 00:02:00 GMT", 120), ("garbage", 1), ("inf", 1)],
)
def test_retry_after_seconds_and_http_dates(header, expected) -> None:
    assert retry_delay(header, 0) == expected


def test_read_failures_are_typed_and_503_is_not_retried() -> None:
    replay = Replay("unavailable", "malformed", "conflict", httpx.ConnectError(SECRET))
    client = adapter(replay)
    request = PlatformRequest(Operation.GPU_AVAILABILITY)
    assert client.execute(request).outcome == Outcome.UNAVAILABLE
    assert client.execute(request).outcome == Outcome.MALFORMED
    assert client.execute(request).outcome == Outcome.RECONCILE_REQUIRED
    assert client.execute(request).outcome == Outcome.TRANSPORT_ERROR
    assert len(replay.requests) == 4


def test_storage_is_separate_no_bearer_cookies_redirects_or_secret_logs(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    replay = Replay("storage_created", "redirect", "storage_duplicate")
    storage = SignedStorage(
        frozenset({"fixture-storage.example"}), transport=httpx.MockTransport(replay)
    )
    target = StorageTarget(
        "https://fixture-storage.example/archive.zip?signature=fixture-storage-signature",
        {"Content-Type": "application/zip", "x-amz-checksum-sha256": "fixture-checksum"},
    )
    assert storage.put(target, b"licence-clean fixture").outcome == Outcome.SUCCEEDED
    assert storage.get(target).outcome == Outcome.REJECTED
    assert storage.put(target, b"fixture").outcome == Outcome.RECONCILE_REQUIRED
    assert len(replay.requests) == 3
    assert all(
        "authorization" not in r.headers and "cookie" not in r.headers for r in replay.requests
    )
    assert SECRET not in caplog.text
    assert "fixture-storage-signature" not in caplog.text
    assert "fixture-storage-signature" not in repr(target)
    for name in ("Authorization", "authorization", "Cookie", "Host", "Proxy-Authorization"):
        assert (
            storage.put(replace(target, headers={name: SECRET}), b"x").outcome == Outcome.REJECTED
        )
    for url in (
        "http://fixture-storage.example/archive.zip",
        "https://platform.ultralytics.com/api/training/start",
        "https://localhost/archive.zip",
        "https://untrusted.example/archive.zip",
        "https://user:password@fixture-storage.example/archive.zip",
        "https://fixture-storage.example:444/archive.zip",
    ):
        assert storage.get(StorageTarget(url)).outcome == Outcome.REJECTED
    assert len(replay.requests) == 3
    storage.close()


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.1",
        "0177.0.0.1",
        "0x7f.0.0.1",
        "0x7f.1",
        "2130706433",
        "0x7f000001",
        "127.0.0.01",
        "8.8.8.010",
        "platform.ultralytics.com",
        "localhost",
        "host.local",
        "-bucket.example",
        "bucket-.example",
    ],
)
def test_storage_policy_rejects_local_or_ambiguous_hosts(host) -> None:
    with pytest.raises(ValueError, match="Invalid trusted storage host policy"):
        SignedStorage(frozenset({host}))


@pytest.mark.parametrize(
    "host", ["fixture-storage.example", "bucket-123.storage.example", "8.8.8.8"]
)
def test_storage_policy_accepts_approved_public_hosts(host) -> None:
    replay = Replay("storage_created")
    storage = SignedStorage(frozenset({host}), transport=httpx.MockTransport(replay))
    assert storage.get(StorageTarget(f"https://{host}/artifact")).outcome == Outcome.SUCCEEDED
    assert len(replay.requests) == 1
    storage.close()


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
@pytest.mark.parametrize("mutation", [False, True])
def test_transient_platform_status_is_not_policy_rejection_or_automatic_retry(
    status, mutation
) -> None:
    replay = Replay(f"transient_{status}")
    client = adapter(replay)
    request = training() if mutation else PlatformRequest(Operation.GPU_AVAILABILITY)
    result = client.execute(request)
    assert result.status_code == status
    assert result.outcome == (Outcome.RECONCILE_REQUIRED if mutation else Outcome.UNAVAILABLE)
    if mutation:
        assert client.execute(request) == result
    assert len(replay.requests) == 1
    client.close()


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
@pytest.mark.parametrize("method", ["get", "put"])
def test_transient_storage_status_preserves_read_write_semantics_without_retry(
    status, method
) -> None:
    replay = Replay(f"transient_{status}")
    storage = SignedStorage(
        frozenset({"fixture-storage.example"}), transport=httpx.MockTransport(replay)
    )
    target = StorageTarget("https://fixture-storage.example/artifact")
    result = storage.put(target, b"fixture") if method == "put" else storage.get(target)
    assert result.status_code == status
    assert result.outcome == (
        Outcome.RECONCILE_REQUIRED if method == "put" else Outcome.UNAVAILABLE
    )
    assert len(replay.requests) == 1
    storage.close()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_request_errors_still_reject_for_platform_and_storage_reads(status) -> None:
    replay = Replay(f"rejected_{status}", f"rejected_{status}")
    client = adapter(replay)
    storage = SignedStorage(
        frozenset({"fixture-storage.example"}), transport=httpx.MockTransport(replay)
    )
    result = client.execute(PlatformRequest(Operation.GPU_AVAILABILITY))
    assert (result.outcome, result.status_code) == (Outcome.REJECTED, status)
    result = storage.get(StorageTarget("https://fixture-storage.example/artifact"))
    assert (result.outcome, result.status_code) == (Outcome.REJECTED, status)
    assert len(replay.requests) == 2
    client.close()
    storage.close()


@pytest.mark.parametrize("image_id", ["fixture-image", "a" * 23, "a" * 25, "g" * 24])
def test_invalid_path_does_not_consume_throttle_or_latch_mutation_id(image_id) -> None:
    replay = Replay("labels_updated")
    client = adapter(replay)
    request = PlatformRequest(
        Operation.REPLACE_LABELS, "replace-1", path={"imageId": image_id}, body={"labels": []}
    )
    assert client.execute(request).outcome == Outcome.REJECTED
    assert replay.requests == []
    assert client._throttle._calls == {}
    assert client._writes == {}
    corrected = replace(request, path={"imageId": "507f1f77bcf86cd799439011"})
    assert client.execute(corrected).outcome == Outcome.SUCCEEDED
    assert client.execute(corrected).outcome == Outcome.SUCCEEDED
    assert len(replay.requests) == 1
    client.close()
