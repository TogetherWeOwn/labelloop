"""Normalized ports for cloud training; no SDK client or credential values here.

Production adapters are deliberately not supplied. Ports are trusted application
capabilities, never deserialized from a public request or selected by model output.
The transport owner validates the pinned HTTP schema and translates USD to integer
micro-USD. It must not retry mutations, even on 429, or forward bearer credentials
to artifact hosts. Exceptions may contain secrets; the backend never records them.
"""

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from labelloop_core.budget import LedgerEntry, SpendRequest

if TYPE_CHECKING:
    from labelloop_platform.cloud_training import CloudPlan, CloudRun

ProviderState = Literal[
    "untrained", "pending", "starting", "running", "completed", "failed", "cancelled"
]


class TransportError(Exception):
    def __init__(self, status: int | None = None) -> None:
        self.status = status
        super().__init__("platform_transport_error")


@dataclass(frozen=True)
class ConnectionGrant:
    project_id: str
    workspace_id: str
    operation_id: str
    configuration_sha256: str


class SecretPort(Protocol):
    def authorize(self, plan: "CloudPlan") -> ConnectionGrant:
        """Authenticate the service, authorize project/workspace/operation and key scope.

        Configure the transport's credential out of band. Never return key bytes.
        A grant is not proof unless issued by the trusted secret/auth adapter.
        """
        ...


@dataclass(frozen=True)
class Charge:
    transaction_id: str
    model_id: str
    provider_run_id: str
    actual_microusd: int


class BudgetPort(Protocol):
    def reserve_and_authorize(self, request: SpendRequest) -> LedgerEntry:
        """Durably reserve under all limits and authenticate operation-bound authority.

        Idempotent for an identical request; reject changed bindings. Recheck current
        limits, pauses and authenticated authority even for an existing dispatched
        entry (the backend calls again immediately before start). Do not merely
        delegate to BudgetGate.reserve, whose idempotent fast path skips these checks.
        """
        ...

    def dispatch(self, request: SpendRequest) -> LedgerEntry:
        """Atomic single-use dispatch, checking current pause/limits/authority."""
        ...

    def reconcile(self, request: SpendRequest, charge: Charge | None) -> LedgerEntry:
        """Idempotently settle only attributable charges; None retains the estimate."""
        ...

    def pause_insufficient_credits(self, request: SpendRequest) -> None:
        """Atomically record 402 and pause all project paid stages; never retry."""
        ...


@dataclass(frozen=True)
class ModelSnapshot:
    model_id: str
    owner: str
    project_slug: str
    model_slug: str
    state: ProviderState
    provider_run_id: str | None = None
    dataset_id: str | None = None
    dataset_version: int | None = None
    dataset_content_hash: str | None = None
    resolved_arguments: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class StartReceipt:
    model_id: str
    gpu_type: str
    # TrainingStartResponse.estimatedCost.pricePerHour/gpuMemoryGb and billing.
    price_per_hour_microusd: int
    gpu_memory_gb: int
    estimated_cost_microusd: int
    balance_microusd: int


class TrainingTransport(Protocol):
    def gpu_available(self, grant: ConnectionGrant, gpu_type: str) -> bool: ...

    def find_model(self, grant: ConnectionGrant, plan: "CloudPlan") -> ModelSnapshot | None:
        """Exact owner/project/stable model slug lookup, not a fuzzy name search.

        Return None only for authoritative absence. Transport failures are errors.
        Do not interpret absence after an uncertain create as permission to create.
        """
        ...

    def create_model(self, grant: ConnectionGrant, plan: "CloudPlan") -> ModelSnapshot:
        """Create using plan.model_slug. One call, no automatic retries."""
        ...

    def start(self, grant: ConnectionGrant, body: Mapping[str, object]) -> StartReceipt: ...

    def read_model(
        self, grant: ConnectionGrant, model_id: str, plan: "CloudPlan"
    ) -> ModelSnapshot: ...

    def charge(self, grant: ConnectionGrant, model_id: str, provider_run_id: str) -> Charge | None:
        """Return an attributable run charge, not estimatedCost or account balance."""
        ...

    def download_best(
        self, grant: ConnectionGrant, model_id: str, maximum_bytes: int
    ) -> Iterator[bytes]:
        """Stream best.pt through a bounded, approved-host download; no URL persistence."""
        ...


class ArtifactPort(Protocol):
    def store(self, sha256: str, content: bytes) -> str:
        """Store content-addressed bytes; return a non-secret stable reference."""
        ...


class RunSession(Protocol):
    def load(self) -> "CloudRun | None": ...

    def save(self, run: "CloudRun") -> None:
        """Commit durably before returning; snapshots are not live mutable aliases."""
        ...


class RunStore(Protocol):
    def locked(self, project_id: str, operation_id: str) -> AbstractContextManager[RunSession]:
        """Serialize the entire bounded advance against competing workers/restarts.

        A transaction/lease adapter must fence the writer through transport I/O;
        lease expiry must never let an old worker issue a second mutation. Neither
        a plain in-memory dict nor an unfenced expiring lease qualifies in production.
        """
        ...
