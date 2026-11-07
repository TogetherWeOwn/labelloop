"""Durable release upload transitions, independent of transport and job scheduling."""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol

from .reconciliation import FrozenReleaseMapping, ReleaseIntent
from .sync_records import SyncConflict


class Phase(StrEnum):
    NEW = "new"
    DATASET = "dataset"
    SIGNED = "signed"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    RECONCILE = "reconcile"
    IN_FLIGHT = "in_flight"
    UNCERTAIN = "uncertain"
    FROZEN = "frozen"
    FAILED = "failed"


class Action(StrEnum):
    CREATE = "create_dataset"
    SIGN = "sign_archive"
    PUT = "put_archive"
    INGEST = "ingest_archive"


@dataclass(frozen=True, slots=True)
class UploadSession:
    session_id: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class SyncCommand:
    action: Action
    operation_id: str
    owner: str
    slug: str
    dataset_id: str | None
    payload_json: str


@dataclass(frozen=True, slots=True)
class MutationOutcome:
    kind: str
    dataset_id: str | None = None
    session: UploadSession | None = None
    job_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReleaseSync:
    intent: ReleaseIntent
    owner: str
    slug: str
    sequence: int = 0
    phase: Phase = Phase.NEW
    dataset_id: str | None = None
    session: UploadSession | None = None
    retired_sessions: tuple[str, ...] = ()
    job_id: str | None = None
    pending: SyncCommand | None = None
    history: tuple[str, ...] = ()
    frozen: FrozenReleaseMapping | None = None

    def prepare(self, now: float) -> tuple["ReleaseSync", SyncCommand]:
        from .sync_records import canonical_json

        if not self.owner or not self.slug:
            raise SyncConflict("authorized owner and stable release slug are required")
        payload: dict[str, Any]
        if self.phase == Phase.NEW:
            action = Action.CREATE
            payload = {
                "owner": self.owner,
                "dataset": self.slug,
                "name": self.slug,
                "task": "detect",
                "visibility": "private",
                "format": "yolo",
                "classNames": self.intent.class_names,
            }
        elif self.phase == Phase.DATASET:
            action = Action.SIGN
            payload = {
                "assetId": self.dataset_id,
                "assetType": "datasets",
                "contentType": "application/zip",
                "filename": "release.zip",
            }
        elif self.phase == Phase.SIGNED:
            if self.session is None or now >= self.session.expires_at:
                raise SyncConflict("signed PUT expired; retire session and sign a fresh target")
            action = Action.PUT
            payload = {
                "sessionId": self.session.session_id,
                "archiveSha256": self.intent.archive_sha256,
            }
        elif self.phase == Phase.UPLOADED:
            if self.session is None:
                raise SyncConflict("ingest needs a recorded successful PUT session")
            action = Action.INGEST
            payload = self.intent.ingest_body(self.session.session_id)
        else:
            raise SyncConflict("no mutation allowed; reconcile the persisted outcome first")
        command = SyncCommand(
            action,
            self.intent.operation_id,
            self.owner,
            self.slug,
            self.dataset_id,
            canonical_json(payload),
        )
        return replace(
            self,
            sequence=self.sequence + 1,
            phase=Phase.IN_FLIGHT,
            pending=command,
            history=(*self.history, f"intent:{action}"),
        ), command

    def observe(self, command: SyncCommand, outcome: MutationOutcome) -> "ReleaseSync":
        if self.phase != Phase.IN_FLIGHT or self.pending != command:
            raise SyncConflict("stale or duplicate mutation outcome")
        result = replace(
            self,
            sequence=self.sequence + 1,
            history=(*self.history, f"outcome:{command.action}:{outcome.kind}"),
        )
        if outcome.kind == "uncertain":
            return replace(result, phase=Phase.UNCERTAIN)
        if outcome.kind == "failed":
            return replace(result, phase=Phase.FAILED)
        if command.action == Action.PUT and outcome.kind in {"expired", "precondition_failed"}:
            return result.retire_session()
        if command.action == Action.CREATE and outcome.kind == "ok" and outcome.dataset_id:
            return replace(result, phase=Phase.DATASET, dataset_id=outcome.dataset_id, pending=None)
        if command.action == Action.SIGN and outcome.kind == "ok" and outcome.session:
            if (
                not outcome.session.session_id
                or outcome.session.session_id in self.retired_sessions
            ):
                raise SyncConflict("signed target reused a retired session")
            return replace(result, phase=Phase.SIGNED, session=outcome.session, pending=None)
        if command.action == Action.PUT and outcome.kind == "ok":
            return replace(result, phase=Phase.UPLOADED, pending=None)
        if command.action == Action.INGEST and outcome.kind == "queued" and outcome.job_id:
            return replace(result, phase=Phase.PROCESSING, job_id=outcome.job_id, pending=None)
        # A malformed response after dispatch is uncertain, not permission to replay.
        return replace(result, phase=Phase.UNCERTAIN)

    def retire_session(self) -> "ReleaseSync":
        if self.phase not in {Phase.SIGNED, Phase.IN_FLIGHT, Phase.UNCERTAIN}:
            raise SyncConflict("cannot retire a session already submitted to ingest")
        if self.pending and self.pending.action not in {Action.SIGN, Action.PUT}:
            raise SyncConflict("uncertain ingest/create must reconcile IDs, never repeat")
        retired = (
            (*self.retired_sessions, self.session.session_id)
            if self.session
            else self.retired_sessions
        )
        return replace(
            self,
            sequence=self.sequence + 1,
            phase=Phase.DATASET,
            session=None,
            retired_sessions=retired,
            pending=None,
            history=(*self.history, "retire_upload_session"),
        )

    def recover_dataset(self, dataset_id: str, class_names: tuple[str, ...]) -> "ReleaseSync":
        """Caller looked up this exact owner/slug; absence is NOT proof create failed."""
        if (
            self.phase not in {Phase.IN_FLIGHT, Phase.UNCERTAIN}
            or not self.pending
            or self.pending.action != Action.CREATE
        ):
            raise SyncConflict("not recovering dataset creation")
        if not dataset_id or class_names != self.intent.class_names:
            raise SyncConflict("dataset lookup did not establish exact identity/catalog")
        return replace(
            self,
            sequence=self.sequence + 1,
            phase=Phase.DATASET,
            dataset_id=dataset_id,
            pending=None,
            history=(*self.history, "reconciled_dataset_id"),
        )

    def processing_finished(self, job_id: str, *, succeeded: bool) -> "ReleaseSync":
        if self.phase != Phase.PROCESSING or self.job_id != job_id:
            raise SyncConflict("stale ingest job outcome")
        return replace(
            self,
            sequence=self.sequence + 1,
            phase=Phase.RECONCILE if succeeded else Phase.FAILED,
            history=(*self.history, "ingest_completed" if succeeded else "ingest_failed"),
        )

    def freeze(self, mapping: FrozenReleaseMapping) -> "ReleaseSync":
        uncertain_ingest = (
            self.phase in {Phase.IN_FLIGHT, Phase.UNCERTAIN}
            and self.pending
            and self.pending.action == Action.INGEST
        )
        if self.phase != Phase.RECONCILE and not uncertain_ingest:
            raise SyncConflict("release cannot freeze before ingest reconciliation")
        if (
            mapping.operation_id,
            mapping.manifest_sha256,
            mapping.archive_sha256,
            mapping.owner,
            mapping.dataset_slug,
            mapping.dataset_id,
        ) != (
            self.intent.operation_id,
            self.intent.manifest_sha256,
            self.intent.archive_sha256,
            self.owner,
            self.slug,
            self.dataset_id,
        ):
            raise SyncConflict("frozen mapping is for different release intent")
        return replace(
            self,
            sequence=self.sequence + 1,
            phase=Phase.FROZEN,
            frozen=mapping,
            pending=None,
            history=(*self.history, "release_frozen"),
        )


class ReleaseJournal(Protocol):
    """Durable transaction/CAS port; unique operation ID, no in-memory production lock."""

    def load(self, operation_id: str) -> ReleaseSync: ...
    def compare_and_set(self, expected: ReleaseSync, updated: ReleaseSync) -> bool: ...


class ReleaseTransport(Protocol):
    """Adapter owns validated routes, archive bytes and ephemeral signed-target tickets.

    A signed-target ticket is never authority to select an endpoint or an archive.
    PUT sends only returned storage headers; never a Platform bearer. SIGN derives
    totalBytes from the hash-verified archive. No automatic mutation retries.
    """

    def execute(self, command: SyncCommand) -> MutationOutcome: ...


def dispatch_once(
    journal: ReleaseJournal, transport: ReleaseTransport, operation_id: str, *, now: float
) -> ReleaseSync:
    before = journal.load(operation_id)
    prepared, command = before.prepare(now)
    if not journal.compare_and_set(before, prepared):
        raise SyncConflict("another writer claimed this operation")
    try:
        outcome = transport.execute(command)
    except Exception:
        # Do not retain provider exception bodies, signed URLs or credentials.
        outcome = MutationOutcome("uncertain")
    observed = prepared.observe(command, outcome)
    if not journal.compare_and_set(prepared, observed):
        raise SyncConflict("operation changed while response was outstanding; reconcile")
    return observed
