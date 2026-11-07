"""Append-only descendant invalidation plans; never rewrite canonical evidence."""

from dataclasses import dataclass
from typing import Literal

from .domain import AnnotationRevision
from .lineage import LineageGraph

ArtifactKind = Literal[
    "source_label",
    "reference",
    "canonical_release",
    "derived_sample",
    "dataset_variant",
    "proposal_run",
    "training_run",
    "model",
    "generated_annotation",
]
InvalidationAction = Literal["review", "rebuild", "retrain", "adjudication_required"]


@dataclass(frozen=True, slots=True)
class EvidenceArtifact:
    artifact_id: str
    kind: ArtifactKind
    revision: AnnotationRevision | None = None

    def __post_init__(self) -> None:
        if not self.artifact_id or self.kind not in {
            "source_label",
            "reference",
            "canonical_release",
            "derived_sample",
            "dataset_variant",
            "proposal_run",
            "training_run",
            "model",
            "generated_annotation",
        }:
            raise ValueError("lineage artifacts require explicit identity and kind")
        if self.kind in {"source_label", "generated_annotation"} and self.revision is None:
            raise ValueError("annotation lineage must pin its immutable revision")


@dataclass(frozen=True, slots=True)
class InvalidationMark:
    artifact_id: str
    action: InvalidationAction
    affected_by: tuple[str, ...]
    expected_revision: int | None


@dataclass(frozen=True, slots=True)
class InvalidationEvent:
    event_id: str
    reason: str
    bad_evidence_ids: tuple[str, ...]
    marks: tuple[InvalidationMark, ...]


def plan_invalidation(
    *,
    event_id: str,
    reason: str,
    bad_evidence_ids: frozenset[str],
    graph: LineageGraph,
    artifacts: tuple[EvidenceArtifact, ...],
) -> InvalidationEvent:
    if not event_id or not reason or not bad_evidence_ids:
        raise ValueError("invalidation requires an identified event, reason and bad evidence")
    records = {item.artifact_id: item for item in artifacts}
    if len(records) != len(artifacts):
        raise ValueError("lineage artifact identities must be unique")
    nodes = set(graph.parents) | {
        parent for parents in graph.parents.values() for parent in parents
    }
    if not nodes <= records.keys() or not bad_evidence_ids <= records.keys():
        raise ValueError("incomplete lineage inventory cannot certify affected descendants")
    for root in bad_evidence_ids:
        if records[root].kind not in {"source_label", "reference"}:
            raise ValueError("bad evidence must identify a source revision or reference")
    # Validate even graphs reconstructed directly from persisted mappings.
    for child, parents in graph.parents.items():
        if not parents or any(child in graph.ancestors(parent) for parent in parents):
            raise ValueError("invalidation requires acyclic, explicit lineage")
    causes: dict[str, set[str]] = {}
    for root in sorted(bad_evidence_ids):
        for affected in graph.descendants(root) | {root}:
            causes.setdefault(affected, set()).add(root)
    marks: list[InvalidationMark] = []
    for artifact_id, roots in sorted(causes.items()):
        record = records[artifact_id]
        action: InvalidationAction = "review"
        if record.kind in {"canonical_release", "derived_sample", "dataset_variant"}:
            action = "rebuild"
        elif record.kind in {"training_run", "model"}:
            action = "retrain"
        if record.revision is not None and record.revision.review_tier in {
            "human_reviewed",
            "adjudicated",
        }:
            action = "adjudication_required"
        marks.append(
            InvalidationMark(
                artifact_id,
                action,
                tuple(sorted(roots)),
                record.revision.revision if record.revision is not None else None,
            )
        )
    return InvalidationEvent(event_id, reason, tuple(sorted(bad_evidence_ids)), tuple(marks))


def append_invalidation(
    history: tuple[InvalidationEvent, ...], event: InvalidationEvent
) -> tuple[InvalidationEvent, ...]:
    for previous in history:
        if previous.event_id == event.event_id:
            if previous != event:
                raise ValueError("invalidation event IDs cannot overwrite history")
            return history
    return (*history, event)
