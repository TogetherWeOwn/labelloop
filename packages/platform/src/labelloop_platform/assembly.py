"""Typed composition preflight, not credential, synchronization or persistence adapters."""

from dataclasses import dataclass
from typing import Literal, Protocol

from labelloop_platform.cloud_training import CloudPlan, PlatformTrainingBackend
from labelloop_platform.training_ports import (
    ArtifactPort,
    BudgetPort,
    ConnectionGrant,
    RunStore,
    SecretPort,
    TrainingTransport,
)


class AssemblyRefused(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseBinding:
    project_id: str
    workspace_id: str
    owner: str
    dataset_id: str
    dataset_slug: str
    release_id: str
    recipe_id: str
    manifest_sha256: str
    class_map_sha256: str
    split_assignment_version: str
    platform_content_hash: str

    @classmethod
    def expected(cls, plan: CloudPlan) -> "ReleaseBinding":
        request = plan.request
        return cls(
            request.project_id,
            plan.workspace_id,
            plan.owner,
            plan.dataset_id,
            plan.dataset_slug,
            request.dataset_release_id,
            request.recipe_id,
            request.dataset_manifest_sha256,
            request.class_map_sha256,
            request.split_assignment_version,
            plan.expected_dataset_content_hash,
        )


@dataclass(frozen=True)
class ReleaseReadback:
    binding: ReleaseBinding
    state: Literal["pending", "incomplete", "conflicted", "verified"]
    reconciliation_id: str


class SynchronizedReleasePort(Protocol):
    def read(self, plan: CloudPlan, grant: ConnectionGrant) -> ReleaseReadback | None:
        """Read a persisted, frozen mapping authorized for this exact grant.

        Only return verified after complete byte/label/class/split/dimension and
        partial-output reconciliation. Queued ingest is not verified. The producer
        owns those checks, immutable mapping persistence and its writer fence;
        this composition layer neither reconstructs them nor trusts a public flag.
        None means missing mapping, never permission to upload or create one.
        """
        ...


def assemble_training(
    plan: CloudPlan,
    *,
    sync: SynchronizedReleasePort | None = None,
    transport: TrainingTransport | None = None,
    budget: BudgetPort | None = None,
    secrets: SecretPort | None = None,
    runs: RunStore | None = None,
    artifacts: ArtifactPort | None = None,
    maximum_checkpoint_bytes: int = 128 * 1024 * 1024,
) -> PlatformTrainingBackend:
    """Perform read-only producer preflight and compose the delivered backend.

    No reservation, write, provider mutation or training happens here. Ports come
    from trusted worker composition, never an HTTP/model payload. This does not
    certify their production implementations. Backend.train still authenticates,
    fences intent and gates spend on every advance. Keep the backend for recovery:
    reassembly must not gate late billing reconciliation on sync availability.
    """
    if any(port is None for port in (sync, transport, budget, secrets, runs, artifacts)):
        raise AssemblyRefused("bindings_unavailable")
    assert sync is not None and secrets is not None
    try:
        grant = secrets.authorize(plan)
    except Exception:
        raise AssemblyRefused("connection_unavailable") from None
    if grant != ConnectionGrant(
        plan.request.project_id,
        plan.workspace_id,
        plan.request.run_id,
        plan.configuration_sha256,
    ):
        raise AssemblyRefused("connection_binding_mismatch")
    try:
        readback = sync.read(plan, grant)
    except Exception:
        raise AssemblyRefused("sync_persistence_unavailable") from None
    if readback is None:
        raise AssemblyRefused("sync_mapping_missing")
    if readback.state != "verified" or not readback.reconciliation_id:
        raise AssemblyRefused("sync_incomplete")
    if readback.binding != ReleaseBinding.expected(plan):
        raise AssemblyRefused("sync_binding_mismatch")
    return PlatformTrainingBackend(
        plan,
        transport=transport,
        budget=budget,
        secrets=secrets,
        runs=runs,
        artifacts=artifacts,
        maximum_checkpoint_bytes=maximum_checkpoint_bytes,
    )
