"""Foundation adapters for the release-sync ports.

These bind the offline sync state machines (`ReleaseSync`, `Replacement`,
`read_saved_export`) to the foundation's `PlatformPort`/`StoragePort`. The
foundation owns origin, throttle and wire validation; these adapters own
outcome mapping, archive-hash binding and bounded reads. They never choose an
endpoint from model output, never forward the Platform bearer to storage,
never retry a mutation, and never copy provider text into durable records.

The signed-upload target is ephemeral: it is captured from the SIGN response,
used for the single create-only PUT, and discarded. Expiry, a 412, or a lost
PUT result retires the session in the state machine, which then requests a
fresh target; a retired session is never reloaded.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any

from .label_sync import LabelTransport
from .ports import (
    Operation,
    Outcome,
    PlatformPort,
    PlatformRequest,
    PlatformResult,
    StoragePort,
    StorageTarget,
)
from .release_sync import (
    Action,
    DatasetEvidence,
    MutationOutcome,
    ReleaseTransport,
    SyncCommand,
    UploadSession,
)
from .saved_export import SavedExport, SavedExportSource
from .sync_records import ContentEvidence, ImageSnapshot, SyncConflict, sha256

_CREATE = Operation.CREATE_DATASET
_SIGN = Operation.SIGNED_UPLOAD
_INGEST = Operation.INGEST

_OPERATIONS: dict[Action, Operation] = {
    Action.CREATE: _CREATE,
    Action.SIGN: _SIGN,
    Action.INGEST: _INGEST,
}


def _expires_at(value: Any) -> float:
    if not isinstance(value, str):
        raise SyncConflict("signed session lacks an expiry")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError):
        raise SyncConflict("signed session expiry is malformed") from None


def _failure(
    result: PlatformResult,
    monotonic: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
) -> MutationOutcome:
    if result.outcome in {Outcome.REJECTED, Outcome.MALFORMED}:
        return MutationOutcome("failed")
    if result.outcome == Outcome.RATE_LIMITED and result.status_code is None:
        # The foundation's local throttle reserve returns before wire dispatch
        # (no HTTP status exists): proven undispatched, so the state machine
        # may prepare the same action again. A remote 429 carries a status and
        # stays uncertain below; it may already have been processed. The
        # throttle deadline is monotonic; convert it to the wall domain the
        # state machine's prepare(now) already uses for session expiry, so a
        # persisted deadline stays meaningful across a process restart.
        retry_at = None
        if result.retry_at is not None:
            retry_at = wall_clock() + max(0.0, result.retry_at - monotonic())
        return MutationOutcome("deferred", retry_at=retry_at)
    # UNAVAILABLE/TRANSPORT_ERROR/PAID_PAUSED and remote RATE_LIMITED all mean
    # the mutation must not be replayed; uncertain forces reconciliation.
    return MutationOutcome("uncertain")


class FoundationReleaseTransport(ReleaseTransport):
    """Maps `SyncCommand` to foundation requests and a create-only storage PUT."""

    def __init__(
        self,
        port: PlatformPort,
        storage: StoragePort,
        archive: bytes,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if not archive:
            raise SyncConflict("release adapter needs the archive bytes")
        self._port = port
        self._storage = storage
        self._archive = archive
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self.archive_sha256 = sha256(archive)
        self._targets: dict[str, StorageTarget] = {}

    def execute(self, command: SyncCommand) -> MutationOutcome:
        payload = json.loads(command.payload_json)
        if not isinstance(payload, dict):
            raise SyncConflict("mutation payload is not an object")
        if command.action == Action.PUT:
            if payload.get("archiveSha256") != self.archive_sha256:
                raise SyncConflict("upload target is for different archive bytes")
            return self._put(command)
        # Bind the archive identity before the FIRST mutation: a mismatched
        # CREATE is rejected before dispatch, so SIGN/INGEST never mutate on
        # bytes the intent does not name. Later actions need no archive field.
        if command.action == Action.CREATE:
            metadata = payload.get("metadata")
            archive = metadata.get("labelloopArchive") if isinstance(metadata, dict) else None
            if archive != self.archive_sha256:
                raise SyncConflict("release archive identity differs from upload bytes")
        body = payload
        if command.action == Action.SIGN:
            # Both pinned signed-upload branches require a positive totalBytes.
            body = {**payload, "totalBytes": len(self._archive)}
        request = PlatformRequest(
            _OPERATIONS[command.action],
            operation_id=command.dispatch_id,
            path={"owner": command.owner, "dataset": command.slug}
            if command.action == Action.INGEST
            else {},
            body=body,
        )
        result = self._port.execute(request)
        if result.outcome != Outcome.SUCCEEDED:
            return _failure(result, self._monotonic, self._wall_clock)
        return self._success(command, result)

    def _success(self, command: SyncCommand, result: PlatformResult) -> MutationOutcome:
        data = result.data
        if not isinstance(data, dict):
            raise SyncConflict("mutation succeeded without a response record")
        if command.action == Action.CREATE:
            dataset_id = data.get("id")
            if not isinstance(dataset_id, str) or not dataset_id:
                raise SyncConflict("created dataset lacks an ID")
            return MutationOutcome("ok", dataset_id=dataset_id)
        if command.action == Action.SIGN:
            session_id = data.get("sessionId")
            upload_url = data.get("uploadUrl")
            # The pinned response requires only sessionId/uploadUrl/expiresAt;
            # headers are optional, so absence means "no extra instructions".
            headers = data.get("headers", {})
            if not isinstance(session_id, str) or not session_id:
                raise SyncConflict("signed session lacks an ID")
            if not isinstance(upload_url, str) or not upload_url or not isinstance(headers, dict):
                raise SyncConflict("signed session lacks a storage target")
            forwarded: dict[str, str] = {}
            for key, value in headers.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    raise SyncConflict("signed session headers are malformed")
                forwarded[key] = value
            self._targets[session_id] = StorageTarget(upload_url, forwarded)
            return MutationOutcome(
                "ok", session=UploadSession(session_id, _expires_at(data.get("expiresAt")))
            )
        if command.action == Action.INGEST:
            job_id = data.get("jobId")
            if not isinstance(job_id, str) or not job_id or data.get("status") != "queued":
                raise SyncConflict("ingest did not return a queued job")
            return MutationOutcome("queued", job_id=job_id)
        raise SyncConflict("unsupported mutation action")

    def _put(self, command: SyncCommand) -> MutationOutcome:
        payload = json.loads(command.payload_json)
        session_id = payload.get("sessionId")
        if not isinstance(session_id, str) or session_id not in self._targets:
            raise SyncConflict("signed PUT has no live target; sign a fresh session")
        target = self._targets[session_id]
        received = self._storage.put(target, self._archive)
        if received.outcome == Outcome.SUCCEEDED:
            del self._targets[session_id]
            return MutationOutcome("ok")
        # Check 412 before the REJECTED mapping: some StoragePort bindings
        # report the create-only violation as REJECTED, and the state machine
        # must still retire the session and sign a fresh target.
        if received.status_code == 412:
            # Create-only violation: the state machine retires this session, and
            # the ephemeral target is discarded so it can never be replayed.
            self._targets.pop(session_id, None)
            return MutationOutcome("precondition_failed")
        if received.outcome == Outcome.REJECTED:
            self._targets.pop(session_id, None)
            return MutationOutcome("failed")
        return MutationOutcome("uncertain")

    def drop_target(self, session_id: str) -> None:
        self._targets.pop(session_id, None)


def _read_dataset(port: PlatformPort, owner: str, slug: str) -> dict[str, Any] | None:
    result = port.execute(
        PlatformRequest(Operation.READ_DATASET, path={"owner": owner, "dataset": slug})
    )
    if result.outcome == Outcome.REJECTED:
        # No dataset under that exact owner/slug. Absence is not proof that an
        # uncertain create cannot still arrive, so callers must not conclude
        # that the create failed.
        return None
    if result.outcome != Outcome.SUCCEEDED or not isinstance(result.data, dict):
        raise SyncConflict("dataset lookup did not succeed")
    dataset = result.data.get("dataset")
    if not isinstance(dataset, dict):
        raise SyncConflict("dataset lookup returned no record")
    return dataset


def find_dataset_evidence(
    port: PlatformPort, owner: str, slug: str, *, operation_id: str
) -> DatasetEvidence | None:
    """Lookup the exact owner/slug after an uncertain create; absence is not proof."""
    dataset = _read_dataset(port, owner, slug)
    if dataset is None:
        return None
    metadata = dataset.get("metadata") or {}
    if not isinstance(metadata, dict):
        return None
    operation = metadata.get("labelloopOperation")
    manifest = metadata.get("labelloopManifest")
    archive = metadata.get("labelloopArchive")
    if (
        not isinstance(operation, str)
        or not isinstance(manifest, str)
        or not isinstance(archive, str)
    ):
        return None
    if operation != operation_id:
        return None
    return DatasetEvidence(
        dataset.get("id", ""),
        owner,
        slug,
        dataset.get("visibility", ""),
        dataset.get("task", ""),
        tuple(dataset.get("classNames") or []),
        operation,
        manifest,
        archive,
    )


class FoundationLabelTransport(LabelTransport):
    """Reads and fully replaces image labels through the foundation port."""

    def __init__(self, port: PlatformPort) -> None:
        self._port = port

    def read_image(self, image_id: str) -> ImageSnapshot:
        result = self._port.execute(
            PlatformRequest(Operation.READ_IMAGE, path={"imageId": image_id})
        )
        if result.outcome != Outcome.SUCCEEDED or not isinstance(result.data, dict):
            raise SyncConflict("image read did not succeed")
        return ImageSnapshot.from_wire(result.data)

    def replace_labels(self, image_id: str, body: dict[str, Any], *, operation_id: str) -> None:
        labels = body.get("labels")
        if not isinstance(labels, list):
            raise SyncConflict("label replacement needs the complete label set")
        result = self._port.execute(
            PlatformRequest(
                Operation.REPLACE_LABELS,
                operation_id=operation_id,
                path={"imageId": image_id},
                body={"labels": labels},
            )
        )
        if result.outcome != Outcome.SUCCEEDED:
            raise SyncConflict("label replacement did not succeed; reconcile readback")


# Bounded image-lookup budget: pages of 5000, so 10 pages covers every
# reader-acceptable release (read_saved_export caps membership at 10000).
_LIST_IMAGES_BUDGET_PAGES = 10


class FoundationSavedExportSource(SavedExportSource):
    """Binds `read_saved_export` to export, list and storage reads."""

    def __init__(
        self,
        port: PlatformPort,
        storage: StoragePort,
        *,
        decode_dimensions: Callable[[bytes], tuple[int, int]],
        max_export_bytes: int = 16 * 1024 * 1024,
        max_image_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        self._port = port
        self._storage = storage
        self._decode = decode_dimensions
        self._max_export_bytes = max_export_bytes
        self._max_image_bytes = max_image_bytes
        # Bound by `saved_export` in the reader's call order: image lookups for
        # one frozen dataset are scoped to the record fetched for that export,
        # never to a caller- or model-selected slug.
        self._datasets: dict[str, dict[str, Any]] = {}

    def saved_export(self, owner: str, slug: str, version: int) -> SavedExport:
        dataset = _read_dataset(self._port, owner, slug)
        if dataset is None:
            raise SyncConflict("dataset record is absent")
        if not isinstance(dataset.get("id"), str):
            raise SyncConflict("dataset record lacks an ID")
        self._datasets[dataset["id"]] = dataset
        result = self._port.execute(
            PlatformRequest(
                Operation.READ_EXPORT, path={"owner": owner, "dataset": slug}, query={"v": version}
            )
        )
        if result.outcome != Outcome.SUCCEEDED or not isinstance(result.data, dict):
            raise SyncConflict("saved export request did not succeed")
        returned = result.data.get("version")
        download_url = result.data.get("downloadUrl")
        if not isinstance(download_url, str) or not download_url:
            raise SyncConflict("saved export returned no download target")
        if type(returned) is not int:
            raise SyncConflict("saved export returned an invalid version")
        content = self.download_image(download_url, max_bytes=self._max_export_bytes)
        return SavedExport(dataset, returned, content)

    def image_by_filename(self, dataset_id: str, filename: str) -> ImageSnapshot:
        dataset = self._dataset_for_id(dataset_id)
        # `search` is a substring match, not an exact selector: exhaust every
        # page within a bounded budget so a later exact filename is found and
        # a later duplicate still makes the identity ambiguous.
        matches: list[dict[str, Any]] = []
        cursor: str | None = None
        for _page in range(_LIST_IMAGES_BUDGET_PAGES):
            query: dict[str, Any] = {
                "search": filename,
                "includeImageUrls": "true",
                "limit": 5000,
            }
            if cursor is not None:
                query["cursor"] = cursor
            listed = self._port.execute(
                PlatformRequest(
                    Operation.LIST_IMAGES,
                    path={"owner": dataset["owner"], "dataset": dataset["dataset"]},
                    query=query,
                )
            )
            if listed.outcome != Outcome.SUCCEEDED or not isinstance(listed.data, dict):
                raise SyncConflict("image lookup did not succeed")
            images = listed.data.get("images")
            if not isinstance(images, list) or not isinstance(listed.data.get("hasMore"), bool):
                raise SyncConflict("image lookup returned no catalogue")
            matches.extend(
                item for item in images if isinstance(item, dict) and item.get("name") == filename
            )
            if not listed.data["hasMore"]:
                break
            next_cursor = listed.data.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise SyncConflict("image lookup pagination is incomplete")
            cursor = next_cursor
        else:
            raise SyncConflict("image lookup exceeds bounded pagination budget")
        if len(matches) != 1:
            raise SyncConflict("saved image identity is ambiguous")
        image_id = matches[0].get("id")
        if not isinstance(image_id, str) or not image_id:
            raise SyncConflict("image lookup returned no ID")
        snapshot = FoundationLabelTransport(self._port).read_image(image_id)
        image_url = matches[0].get("imageUrl")
        if not isinstance(image_url, str) or not image_url:
            raise SyncConflict("image lookup returned no content URL")
        pixels = self.download_image(image_url, max_bytes=self._max_image_bytes)
        width, height = self._decode(pixels)
        return replace(snapshot, content=ContentEvidence(sha256(pixels), width, height))

    def download_image(self, url: str, *, max_bytes: int | None = None) -> bytes:
        # Default matches the bounded reader's image limit; callers pass the
        # export limit for NDJSON downloads.
        received = self._storage.get(
            StorageTarget(url),
            max_bytes=self._max_image_bytes if max_bytes is None else max_bytes,
        )
        if received.outcome != Outcome.SUCCEEDED:
            raise SyncConflict("image download did not succeed")
        return received.content

    def _dataset_for_id(self, dataset_id: str) -> dict[str, Any]:
        dataset = self._datasets.get(dataset_id)
        if dataset is None:
            raise SyncConflict("image lookup has no bound dataset record")
        return dataset
