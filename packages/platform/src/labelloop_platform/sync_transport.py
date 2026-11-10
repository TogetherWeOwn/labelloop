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


def _failure(outcome: Outcome) -> MutationOutcome:
    if outcome in {Outcome.REJECTED, Outcome.MALFORMED}:
        return MutationOutcome("failed")
    # RATE_LIMITED/UNAVAILABLE/TRANSPORT_ERROR/PAID_PAUSED all mean the
    # mutation must not be replayed; uncertain forces reconciliation.
    return MutationOutcome("uncertain")


class FoundationReleaseTransport(ReleaseTransport):
    """Maps `SyncCommand` to foundation requests and a create-only storage PUT."""

    def __init__(self, port: PlatformPort, storage: StoragePort, archive: bytes) -> None:
        if not archive:
            raise SyncConflict("release adapter needs the archive bytes")
        self._port = port
        self._storage = storage
        self._archive = archive
        self.archive_sha256 = sha256(archive)
        self._targets: dict[str, StorageTarget] = {}

    def execute(self, command: SyncCommand) -> MutationOutcome:
        payload = json.loads(command.payload_json)
        if not isinstance(payload, dict):
            raise SyncConflict("mutation payload is not an object")
        if payload.get("archiveSha256", self.archive_sha256) != self.archive_sha256:
            raise SyncConflict("upload target is for different archive bytes")
        if command.action == Action.PUT:
            return self._put(command)
        request = PlatformRequest(
            _OPERATIONS[command.action],
            operation_id=command.dispatch_id,
            path={"owner": command.owner, "dataset": command.slug}
            if command.action == Action.INGEST
            else {},
            body=payload,
        )
        result = self._port.execute(request)
        if result.outcome != Outcome.SUCCEEDED:
            return _failure(result.outcome)
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
            headers = data.get("headers")
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
        if received.outcome == Outcome.REJECTED:
            self._targets.pop(session_id, None)
            return MutationOutcome("failed")
        if received.status_code == 412:
            # Create-only violation: the state machine retires this session, and
            # the ephemeral target is discarded so it can never be replayed.
            self._targets.pop(session_id, None)
            return MutationOutcome("precondition_failed")
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

    def replace_labels(self, image_id: str, body: dict[str, Any]) -> None:
        labels = body.get("labels")
        if not isinstance(labels, list):
            raise SyncConflict("label replacement needs the complete label set")
        result = self._port.execute(
            PlatformRequest(
                Operation.REPLACE_LABELS, path={"imageId": image_id}, body={"labels": labels}
            )
        )
        if result.outcome != Outcome.SUCCEEDED:
            raise SyncConflict("label replacement did not succeed; reconcile readback")


class FoundationSavedExportSource(SavedExportSource):
    """Binds `read_saved_export` to export, list and storage reads."""

    def __init__(
        self,
        port: PlatformPort,
        storage: StoragePort,
        *,
        decode_dimensions: Callable[[bytes], tuple[int, int]],
    ) -> None:
        self._port = port
        self._storage = storage
        self._decode = decode_dimensions
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
        content = self.download_image(download_url)
        return SavedExport(dataset, returned, content)

    def image_by_filename(self, dataset_id: str, filename: str) -> ImageSnapshot:
        dataset = self._dataset_for_id(dataset_id)
        listed = self._port.execute(
            PlatformRequest(
                Operation.LIST_IMAGES,
                path={"owner": dataset["owner"], "dataset": dataset["dataset"]},
                query={"search": filename, "includeImageUrls": "true", "limit": 5000},
            )
        )
        if listed.outcome != Outcome.SUCCEEDED or not isinstance(listed.data, dict):
            raise SyncConflict("image lookup did not succeed")
        images = listed.data.get("images")
        if not isinstance(images, list):
            raise SyncConflict("image lookup returned no catalogue")
        matches = [
            item for item in images if isinstance(item, dict) and item.get("name") == filename
        ]
        if len(matches) != 1:
            raise SyncConflict("saved image identity is ambiguous")
        image_id = matches[0].get("id")
        if not isinstance(image_id, str) or not image_id:
            raise SyncConflict("image lookup returned no ID")
        snapshot = FoundationLabelTransport(self._port).read_image(image_id)
        image_url = matches[0].get("imageUrl")
        if not isinstance(image_url, str) or not image_url:
            raise SyncConflict("image lookup returned no content URL")
        pixels = self.download_image(image_url)
        width, height = self._decode(pixels)
        return replace(snapshot, content=ContentEvidence(sha256(pixels), width, height))

    def download_image(self, url: str) -> bytes:
        received = self._storage.get(StorageTarget(url))
        if received.outcome != Outcome.SUCCEEDED:
            raise SyncConflict("image download did not succeed")
        return received.content

    def _dataset_for_id(self, dataset_id: str) -> dict[str, Any]:
        dataset = self._datasets.get(dataset_id)
        if dataset is None:
            raise SyncConflict("image lookup has no bound dataset record")
        return dataset
