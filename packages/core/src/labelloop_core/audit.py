"""Reproducible blind-audit membership and fail-closed annotation audit evidence."""

import hashlib
from dataclasses import dataclass

from .domain import AnnotationRevision


@dataclass(frozen=True, slots=True)
class AuditCandidate:
    asset_id: str
    revision: AnnotationRevision
    pipeline_id: str
    prior_reviewer_ids: frozenset[str]

    def __post_init__(self) -> None:
        if not self.asset_id or not self.pipeline_id or not self.prior_reviewer_ids:
            raise ValueError("audit candidates require source, pipeline and reviewer provenance")
        if self.revision.approval_state not in {"auto_accepted", "human_accepted"}:
            raise ValueError("audit sampling requires ordinary accepted labels")


@dataclass(frozen=True, slots=True)
class AuditSample:
    sample_id: str
    candidates: tuple[AuditCandidate, ...]
    seed: int


def select_audit_sample(
    candidates: tuple[AuditCandidate, ...], *, sample_id: str, size: int, seed: int
) -> AuditSample:
    """Sample ordinary acceptances without confidence filtering; cover negatives too."""
    if not sample_id or type(size) is not int or not 1 <= size <= len(candidates):
        raise ValueError("audit sample needs an identity and a feasible positive size")
    if len({item.asset_id for item in candidates}) != len(candidates):
        raise ValueError("audit pool must contain one revision per source")

    def rank(item: AuditCandidate) -> str:
        return hashlib.sha256(
            f"{sample_id}:{seed}:{item.asset_id}:{item.revision.revision}".encode()
        ).hexdigest()

    ordered = sorted(candidates, key=lambda item: (rank(item), item.asset_id))
    negative = next((item for item in ordered if not item.revision.objects), None)
    positive = next((item for item in ordered if item.revision.objects), None)
    required = [item for item in (negative, positive) if item is not None]
    if size < len(required):
        raise ValueError("audit size must cover both accepted positives and negatives")
    selected = required + [item for item in ordered if item not in required][: size - len(required)]
    return AuditSample(sample_id, tuple(sorted(selected, key=lambda item: item.asset_id)), seed)


@dataclass(frozen=True, slots=True)
class AuditObservation:
    asset_id: str
    revision: int
    reviewer_id: str
    blind_full_image_inspected: bool
    labels_correct: bool
    scope_complete: bool
    output_truncated: bool = False

    def __post_init__(self) -> None:
        if any(
            type(value) is not bool
            for value in (
                self.blind_full_image_inspected,
                self.labels_correct,
                self.scope_complete,
                self.output_truncated,
            )
        ):
            raise ValueError("audit evidence flags must be booleans")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("audit evidence requires a positive integer revision")


@dataclass(frozen=True, slots=True)
class AuditVerdict:
    sample_id: str
    passed: bool
    blocking_asset_ids: tuple[str, ...]
    pending_asset_ids: tuple[str, ...]


def assess_audit(sample: AuditSample, observations: tuple[AuditObservation, ...]) -> AuditVerdict:
    """An absent, dependent, truncated or adverse audit is never a passing gate."""
    if not sample.candidates:
        raise ValueError("an empty audit cannot qualify annotation quality")
    candidates = {item.asset_id: item for item in sample.candidates}
    if len(candidates) != len(sample.candidates):
        raise ValueError("audit membership must be unique")
    seen: set[str] = set()
    blocking: set[str] = set()
    for observation in observations:
        if observation.asset_id in seen or observation.asset_id not in candidates:
            raise ValueError("audit observation is duplicate or outside the frozen sample")
        candidate = candidates[observation.asset_id]
        if observation.revision != candidate.revision.revision:
            raise ValueError("audit observation has a stale source revision")
        if not observation.reviewer_id or observation.reviewer_id in candidate.prior_reviewer_ids:
            raise ValueError("audit reviewer must be independent of the original decision")
        seen.add(observation.asset_id)
        if (
            not observation.blind_full_image_inspected
            or not observation.labels_correct
            or not observation.scope_complete
            or observation.output_truncated
        ):
            blocking.add(observation.asset_id)
    pending = tuple(sorted(set(candidates) - seen))
    return AuditVerdict(
        sample.sample_id, not blocking and not pending, tuple(sorted(blocking)), pending
    )
