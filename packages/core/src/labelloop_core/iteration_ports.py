"""Typed service ports for the iteration projection; no real adapter is enabled.

Implementations belong to the existing job workers and authoritative stores. They
must authenticate, validate domain outputs, CAS-commit stage/artifact receipts and
reconcile external intent. The controller never creates another queue or registry.
"""

from dataclasses import dataclass
from typing import Protocol

from .audit import AuditVerdict
from .domain import CanonicalDatasetRelease
from .evaluation import DerivedPredictions, EvaluationReport
from .iteration import EvidenceRef, IterationManifest, StageReceipt, StageRecord
from .registry import ModelRelease, RegistryState
from .review import CorrectionLoop, ReviewPatch
from .training import TrainingRequest, TrainingResult


@dataclass(frozen=True, slots=True)
class PortResult[T]:
    receipt: StageReceipt
    value: T


@dataclass(frozen=True, slots=True)
class ProposalDiscovery:
    # Discovery includes whole images/zero detections, not just teacher boxes.
    proposals: tuple[DerivedPredictions, ...]
    discovery: EvidenceRef


@dataclass(frozen=True, slots=True)
class ValidatedCorrections:
    loops: tuple[tuple[str, CorrectionLoop], ...]


@dataclass(frozen=True, slots=True)
class AdjudicationAudit:
    adjudication: EvidenceRef
    audit: AuditVerdict
    # A passing machine audit alone never changes review_tier or gold_eligible.
    independent_review: EvidenceRef | None


@dataclass(frozen=True, slots=True)
class CompiledVariant:
    canonical_release_id: str
    recipe_id: str
    manifest: EvidenceRef
    archive: EvidenceRef
    validation: EvidenceRef


@dataclass(frozen=True, slots=True)
class SynchronizedVariant:
    variant: CompiledVariant
    remote_mapping: EvidenceRef
    saved_version: EvidenceRef
    reconciliation: EvidenceRef


@dataclass(frozen=True, slots=True)
class RecipeDecision:
    candidate: ModelRelease | None
    before: RegistryState
    after: RegistryState
    qualification: EvidenceRef


@dataclass(frozen=True, slots=True)
class NextEvidence:
    selection: EvidenceRef
    # Selection service owns novelty/diversity/omission/random-audit policy.
    source_batch: EvidenceRef | None


class IterationPorts(Protocol):
    """Signatures only. Calls require persisted/fenced job intent, not just a value.

    Ports return existing typed domain records plus immutable store receipts. The
    trusted validators must verify release completeness, source/policy identity,
    compiler hashes, training request/resolved arguments and evaluation/registry
    qualification. Real adapter wiring is intentionally outside this module.
    """

    def freeze(
        self,
        intent: StageRecord,
        manifest: IterationManifest,
    ) -> PortResult[IterationManifest]: ...

    def propose_discover(self, intent: StageRecord) -> PortResult[ProposalDiscovery]: ...

    def review(self, intent: StageRecord) -> PortResult[tuple[ReviewPatch, ...]]: ...

    def validate(self, intent: StageRecord) -> PortResult[ValidatedCorrections]: ...

    def adjudicate_audit(self, intent: StageRecord) -> PortResult[AdjudicationAudit]: ...

    def release(self, intent: StageRecord) -> PortResult[CanonicalDatasetRelease]: ...

    def compile(self, intent: StageRecord) -> PortResult[CompiledVariant]: ...

    def sync(self, intent: StageRecord) -> PortResult[SynchronizedVariant | CompiledVariant]: ...

    def train(
        self,
        intent: StageRecord,
        request: TrainingRequest,
    ) -> PortResult[TrainingResult]: ...

    def evaluate(self, intent: StageRecord) -> PortResult[EvaluationReport]: ...

    def promote_retain(self, intent: StageRecord) -> PortResult[RecipeDecision]: ...

    def select_next(self, intent: StageRecord) -> PortResult[NextEvidence]: ...

    def reconcile(self, intent: StageRecord) -> StageReceipt | None: ...


def real_iteration_ports() -> IterationPorts:
    raise RuntimeError("real iteration adapters are disabled until trusted service integration")
