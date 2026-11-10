"""Transport-free ports. Trusted application code chooses operations, never model output."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]


class Operation(StrEnum):
    CREATE_DATASET = "create_dataset"
    READ_DATASET = "read_dataset"
    SIGNED_UPLOAD = "signed_upload"
    COMPLETE_UPLOAD = "complete_upload"
    INGEST = "ingest"
    READ_IMAGE = "read_image"
    REPLACE_LABELS = "replace_labels"
    PREDICT_IMAGE = "predict_image"
    START_BATCH = "start_batch"
    READ_BATCH = "read_batch"
    SAVE_EXPORT = "save_export"
    READ_EXPORT = "read_export"
    CREATE_PROJECT = "create_project"
    CREATE_MODEL = "create_model"
    READ_MODEL = "read_model"
    START_TRAINING = "start_training"
    READ_TRAINING = "read_training"
    MODEL_FILES = "model_files"
    CANCEL_TRAINING = "cancel_training"
    GPU_AVAILABILITY = "gpu_availability"
    LIST_IMAGES = "list_images"
    COMPARE_VERSIONS = "compare_versions"


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    RATE_LIMITED = "rate_limited"
    PAID_PAUSED = "paid_paused"
    RECONCILE_REQUIRED = "reconcile_required"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"
    MALFORMED = "malformed"
    TRANSPORT_ERROR = "transport_error"


@dataclass(frozen=True)
class PlatformRequest:
    operation: Operation
    # Stable application write-intent ID; persist BEFORE dispatch, with the request digest.
    operation_id: str | None = None
    path: dict[str, str] = field(default_factory=dict, repr=False)
    query: dict[str, Json] = field(default_factory=dict, repr=False)
    body: dict[str, Json] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class PlatformResult:
    outcome: Outcome
    status_code: int | None = None
    # Raw success data is still untrusted; consumers validate before changing domain state.
    data: Json = field(default=None, repr=False)
    retry_at: float | None = None  # Monotonic clock, only meaningful within this process.


class PlatformPort(Protocol):
    def execute(self, request: PlatformRequest) -> PlatformResult: ...


@dataclass(frozen=True, repr=False)
class StorageTarget:
    url: str
    headers: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        return "StorageTarget(<redacted>)"


@dataclass(frozen=True)
class StorageResult:
    outcome: Outcome
    status_code: int | None = None
    content: bytes = field(default=b"", repr=False)


class StoragePort(Protocol):
    def put(self, target: StorageTarget, content: bytes) -> StorageResult: ...

    # max_bytes bounds the transfer while it streams, not only the result.
    def get(self, target: StorageTarget, *, max_bytes: int | None = None) -> StorageResult: ...
