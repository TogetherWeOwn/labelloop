"""Unmounted canonical source-group lifecycle leaf adapter.

This module is the landing boundary for source-group lifecycle work. It
exposes typed, transaction-bound operations over the delivered canonical
schema and the core ``group_sources`` / ``assign_splits`` implementation.
It deliberately does not mount HTTP routes, authenticate principals,
acquire project or retry locks, assign revisions to frozen releases, start
workers, or implement any management-facade behaviour. Authenticated
principal, lock, audit-attribution and mounting decisions remain owned by
the caller / source integrator, which supplies a caller-owned SQLAlchemy
``Connection`` and an explicit authenticated actor ID.

Canonical authority
-------------------
Labels live on original, orientation-normalized images and sized datasets
are reproducible derivatives. This adapter therefore never mutates
historical rows: applying a regroup publication appends new
``source_split_assignment`` rows under a new assignment version and records
the publication (and any pair decisions) as append-only ``canonical_event``
rows. Historical assignment and ``canonical_dataset_release`` rows,
including their hashes and membership, are preserved byte-for-byte.

Automatic relations cannot be removed by pair decisions
--------------------------------------------------------
``group_sources`` unions exact-hash, perceptual-hash, embedding-cosine and
declared-metadata relations with transitive closure. A persisted
``different_group`` decision is recorded, but it never deletes an automatic
relation or splits a transitive component. If such a decision contradicts
the automatic closure the pair is reported as a conflicting separation and
the group stands. A ``same_group`` decision adds one further union edge;
the closure is recomputed over automatic relations plus those edges.

Training / holdout quarantine
-----------------------------
When a new grouping connects assets that the pinned assignment placed in
different splits (for example train and val/test), the affected frozen
evaluation releases are reported as quarantined and
``requires_fresh_holdout`` is set. Quarantine is a read-only report plus an
entry in the publication event; history is never relabelled. The caller
must obtain a fresh independent holdout or retrain before any evaluation
release that used the old assignment is trusted again.

Embeddings
----------
Embedding vectors are bounded trusted observations supplied by the caller.
They are validated and pinned, never inferred: absent evidence stays
absent (no embedding relation is synthesised), and any supplied embedding
must name the approved embedding model and version. Supplying an embedding
without an approved pinned model, or with a stale or mismatched model or
version, is rejected.

Source DTO mapping (for the integrator)
---------------------------------------
The source integrator owns ``InitialSourceGrouping`` / discovery / pair
DTOs. It maps them into this adapter without this module importing any
management module:

* discovery payloads -> :func:`observation_from_payload`
  (``asset_id``, ``sha256``, ``phash``, optional ``embedding`` /
  ``embedding_model`` / ``embedding_version``, ``metadata``,
  ``class_counts``);
* pair payloads -> :func:`pair_decision_from_payload`
  (``left_asset_id``, ``right_asset_id``, ``decision``, ``rationale``,
  ``actor_kind``, ``actor_id``).

Later source admission is the caller's existing ingest path: the caller
registers the new source asset (for example through the canonical ingest
transaction), and this adapter then admits it into a newly published
grouping. A metadata revision is a new observation set with changed
``metadata`` for an owned asset; re-proposing re-derives the closure.

No ``ultralytics`` or ``ultralytics_platform`` imports are used here.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy import Connection

from labelloop_core.domain import SourceSplitAssignment
from labelloop_core.grouping import (
    ALGORITHM,
    METADATA_KEYS,
    AssetEvidence,
    BorderlineReview,
    GroupingThresholds,
    SimilarityRelation,
    group_sources,
)

from .revision_store import completeness_context_hash
from .schema import (
    annotation_revision,
    borderline_review,
    canonical_dataset_release,
    canonical_event,
    ontology_release,
    project,
    similarity_relation,
    source_asset,
    source_split_assignment,
)

__all__ = [
    "LifecyclePins",
    "PairDecision",
    "ProposedGrouping",
    "PublishedGrouping",
    "QuarantinedRelease",
    "TrustedObservation",
    "apply_regroup",
    "compute_proposal",
    "observation_from_payload",
    "pair_decision_from_payload",
    "propose_regroup",
    "validate_observations",
]

MAX_OBSERVATIONS = 5000
MAX_EMBEDDING_DIM = 4096
MAX_METADATA_VALUE_LENGTH = 512
MAX_CLASS_COUNT_KEYS = 256
DECISION_EVENT = "source_pair_decision"
PUBLICATION_EVENT = "source_grouping_published"
NO_EMBEDDING_MODEL = "none-v1"
PairDecisionKind = Literal["same_group", "different_group"]


class StalePinError(ValueError):
    """A caller-supplied pin no longer matches canonical state."""


@dataclass(frozen=True, slots=True)
class TrustedObservation:
    """Bounded trusted evidence for one owned source asset."""

    asset_id: str
    sha256: str
    phash: int
    embedding: tuple[float, ...] = ()
    embedding_model: str | None = None
    embedding_version: str | None = None
    metadata: Mapping[str, str] | None = None
    class_counts: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        if not self.asset_id:
            raise ValueError("trusted observation requires an asset ID")
        if len(self.sha256) != 64 or any(char not in "0123456789abcdef" for char in self.sha256):
            raise ValueError("trusted observation hash must be lowercase SHA-256 hex")
        if not 0 <= self.phash < 2**64:
            raise ValueError("trusted observation phash must fit in 64 bits")
        if len(self.embedding) > MAX_EMBEDDING_DIM:
            raise ValueError("trusted embedding exceeds the bounded dimension")
        for value in self.embedding:
            if not math.isfinite(value):
                raise ValueError("trusted embedding values must be finite")
        if self.embedding and not self.embedding_model:
            raise ValueError("supplied embeddings must name their embedding model")
        metadata = dict(self.metadata or {})
        for key, value in metadata.items():
            if key not in METADATA_KEYS:
                raise ValueError("trusted metadata keys are limited to grouping keys")
            if not value or len(value) > MAX_METADATA_VALUE_LENGTH:
                raise ValueError("trusted metadata values must be bounded and non-empty")
        counts = dict(self.class_counts or {})
        if len(counts) > MAX_CLASS_COUNT_KEYS:
            raise ValueError("trusted class counts exceed the bounded key count")
        for class_id, count in counts.items():
            if not class_id or count < 0:
                raise ValueError("trusted class counts must be non-negative")
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "class_counts", counts)


@dataclass(frozen=True, slots=True)
class PairDecision:
    """A persisted human or system grouping decision for one asset pair."""

    left_asset_id: str
    right_asset_id: str
    decision: PairDecisionKind
    rationale: str
    actor_kind: Literal["human", "system"]
    actor_id: str

    def __post_init__(self) -> None:
        if not self.left_asset_id or not self.right_asset_id:
            raise ValueError("pair decisions require both asset IDs")
        if self.left_asset_id == self.right_asset_id:
            raise ValueError("pair decisions require two distinct assets")
        if self.decision not in {"same_group", "different_group"}:
            raise ValueError("pair decisions must be same_group or different_group")
        if self.actor_kind not in {"human", "system"}:
            raise ValueError("model output cannot persist grouping decisions")
        if not self.rationale.strip() or not self.actor_id:
            raise ValueError("pair decisions require a rationale and an actor")


@dataclass(frozen=True, slots=True)
class LifecyclePins:
    """Caller-asserted current state; stale pins reject the operation."""

    expected_assignment_version: str | None = None
    expected_ontology_hash: str | None = None
    expected_annotation_revisions: Mapping[str, int] | None = None


@dataclass(frozen=True, slots=True)
class QuarantinedRelease:
    release_id: str
    release_key: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProposedGrouping:
    """Read-only regroup proposal; computing it writes nothing."""

    groups: dict[str, str]
    relations: tuple[SimilarityRelation, ...]
    borderline: tuple[BorderlineReview, ...]
    assignment: SourceSplitAssignment | None
    changed_memberships: tuple[frozenset[str], ...]
    cross_split_pairs: tuple[tuple[str, str], ...]
    conflicting_separations: tuple[tuple[str, str], ...]
    quarantined_releases: tuple[QuarantinedRelease, ...]
    requires_fresh_holdout: bool
    new_assignment_version: str | None
    split_transitions: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PublishedGrouping:
    assignment_version: str
    groups: dict[str, str]
    quarantined_releases: tuple[QuarantinedRelease, ...]
    requires_fresh_holdout: bool
    published: bool


def validate_observations(
    observations: Sequence[TrustedObservation],
    *,
    approved_embedding_model: str | None,
    approved_embedding_version: str | None,
) -> None:
    """Reject unbounded, duplicate or unpinned embedding evidence."""
    if not observations or len(observations) > MAX_OBSERVATIONS:
        raise ValueError("trusted observations must be bounded and non-empty")
    asset_ids = [item.asset_id for item in observations]
    if len(set(asset_ids)) != len(asset_ids):
        raise ValueError("trusted observations require unique asset IDs")
    dimensions = {len(item.embedding) for item in observations if item.embedding}
    if len(dimensions) > 1:
        raise ValueError("trusted embeddings must share one dimension")
    approved = approved_embedding_model if approved_embedding_model != NO_EMBEDDING_MODEL else None
    for item in observations:
        if item.embedding:
            if not approved:
                raise ValueError("embedding evidence supplied without an approved pinned model")
            if item.embedding_model != approved:
                raise ValueError("stale or unapproved embedding model")
            if not approved_embedding_version or not item.embedding_version:
                raise ValueError(
                    "embedding evidence supplied without an approved pinned embedding version"
                )
            if item.embedding_version != approved_embedding_version:
                raise ValueError("stale or unapproved embedding version")
        elif item.embedding_model not in {None, "", NO_EMBEDDING_MODEL}:
            raise ValueError("absent embeddings must not claim an embedding model")


def _evidence(item: TrustedObservation) -> AssetEvidence:
    return AssetEvidence(
        asset_id=item.asset_id,
        sha256=item.sha256,
        phash=item.phash,
        embedding=item.embedding,
        metadata=dict(item.metadata or {}),
        class_counts=dict(item.class_counts or {}),
    )


def decision_hash(decision: PairDecision) -> str:
    left, right = sorted((decision.left_asset_id, decision.right_asset_id))
    encoded = json.dumps(
        {
            "left": left,
            "right": right,
            "decision": decision.decision,
            "rationale": decision.rationale,
            "actor_kind": decision.actor_kind,
            "actor_id": decision.actor_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _published_version(
    assignment: SourceSplitAssignment, decisions: Sequence[PairDecision], evidence_digest: str
) -> str:
    # The core identity binds thresholds/seed/algorithm only, not the asset
    # set or relations, so the published version always appends a content
    # digest. Identical content repeats the same version (idempotent
    # repeat); any membership, split, decision or evidence change
    # (borderline band, embedding pin, thresholds, seed) publishes anew.
    digest = hashlib.sha256(
        json.dumps(
            {
                "membership": sorted(assignment.group_ids.items()),
                "splits": sorted(assignment.asset_splits.items()),
                "decisions": sorted(decision_hash(item) for item in decisions),
                "evidence_digest": evidence_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:12]
    return f"{assignment.assignment_version}-{digest}"


def _evidence_digest(
    observations: Sequence[TrustedObservation],
    relations: Sequence[SimilarityRelation],
    borderline: Sequence[BorderlineReview],
    *,
    thresholds: GroupingThresholds,
    seed: int,
    validation_fraction: float,
    approved_embedding_version: str | None,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "relations": sorted(
                    (item.left_asset_id, item.right_asset_id, item.reason, item.evidence)
                    for item in relations
                ),
                "borderline": sorted(
                    (item.left_asset_id, item.right_asset_id, item.metric, str(item.value))
                    for item in borderline
                ),
                "embedding_pins": sorted(
                    (item.asset_id, item.embedding_model or "", item.embedding_version or "")
                    for item in observations
                    if item.embedding
                ),
                "approved_embedding_version": approved_embedding_version or "",
                "thresholds": {
                    "phash_max_distance": thresholds.phash_max_distance,
                    "phash_review_distance": thresholds.phash_review_distance,
                    "embed_min_cosine": thresholds.embed_min_cosine,
                    "embed_review_cosine": thresholds.embed_review_cosine,
                    "embedding_model": thresholds.embedding_model,
                },
                "seed": seed,
                "validation_fraction": validation_fraction,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _stratified_val_groups(
    group_ids: Sequence[str],
    members: Mapping[str, Sequence[str]],
    class_counts: Mapping[str, Mapping[str, int]],
    *,
    seed: int,
    validation_fraction: float,
) -> set[str]:
    """Seeded group-level class-count stratification over explicit components.

    Mirrors the core assigner's balancing rule, but operates on the
    already-computed closure components directly — no synthetic metadata
    relations are fabricated, so unrelated assets that happen to share a
    session value can never be merged by construction.
    """
    from collections import Counter

    shuffled = sorted(
        group_ids, key=lambda group: hashlib.sha256(f"{seed}:{group}".encode()).hexdigest()
    )
    positions = {group: index for index, group in enumerate(shuffled)}
    counts = {group: Counter(class_counts.get(group, {})) for group in group_ids}
    classes = {class_id for row in counts.values() for class_id in row} or {"negative"}
    targets = {
        class_id: max(
            1, round(sum(row.get(class_id, 0) for row in counts.values()) * validation_fraction)
        )
        for class_id in classes
    }
    val_cap = min(len(group_ids) - 1, max(1, round(len(group_ids) * validation_fraction)))

    def candidate_key(candidate: list[str]) -> tuple[float, float, tuple[int, ...]]:
        deficit = sum(
            max(0, targets[class_id] - sum(counts[group][class_id] for group in candidate))
            for class_id in classes
        )
        return (
            deficit,
            abs(len(candidate) - val_cap),
            tuple(positions[group] for group in candidate),
        )

    best: list[str] | None = None
    if len(group_ids) <= 16:

        def search(selected: list[str], tail: list[str]) -> None:
            nonlocal best
            if len(selected) > val_cap:
                return
            if not tail:
                if (
                    selected
                    and len(selected) < len(group_ids)
                    and (best is None or candidate_key(selected) < candidate_key(best))
                ):
                    best = list(selected)
                return
            search(selected + [tail[0]], tail[1:])
            search(selected, tail[1:])

        search([], list(shuffled))
    else:
        selected = []
        covered: Counter = Counter()
        remaining = list(shuffled)
        while len(selected) < val_cap:
            group = max(
                remaining,
                key=lambda item: (
                    sum(
                        min(counts[item][class_id], targets[class_id])
                        for class_id in classes
                        if covered[class_id] < targets[class_id]
                    ),
                    -positions[item],
                ),
            )
            selected.append(group)
            covered.update(counts[group])
            remaining.remove(group)
        best = selected
    if best is None:
        raise ValueError("assignment cannot establish non-empty train and validation")
    return set(best)


def _core_identity(thresholds: GroupingThresholds, seed: int, validation_fraction: float) -> str:
    from dataclasses import asdict

    identity = hashlib.sha256(
        json.dumps(
            {
                "thresholds": asdict(thresholds),
                "validation_fraction": validation_fraction,
                "seed": seed,
                "algorithm": ALGORITHM,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:16]
    return f"{ALGORITHM}-{identity}"


def compute_proposal(
    observations: Sequence[TrustedObservation],
    decisions: Sequence[PairDecision] = (),
    *,
    thresholds: GroupingThresholds | None = None,
    seed: int = 20261107,
    validation_fraction: float = 0.25,
    approved_embedding_version: str | None = None,
    old_groups: Mapping[str, str] | None = None,
    old_splits: Mapping[str, str] | None = None,
) -> ProposedGrouping:
    """Recompute the transitive closure over trusted evidence and decisions."""
    thresholds = thresholds or GroupingThresholds()
    validate_observations(
        observations,
        approved_embedding_model=thresholds.embedding_model,
        approved_embedding_version=approved_embedding_version,
    )
    known = {item.asset_id for item in observations}
    for decision in decisions:
        if decision.left_asset_id not in known or decision.right_asset_id not in known:
            raise ValueError("pair decisions must name observed assets")
    deduped: dict[str, PairDecision] = {}
    for decision in decisions:
        deduped.setdefault(decision_hash(decision), decision)
    decisions = tuple(deduped.values())
    evidence = [_evidence(item) for item in observations]
    base_groups, relations, borderline = group_sources(evidence, thresholds)

    parents = {item.asset_id: item.asset_id for item in evidence}

    def find(asset_id: str) -> str:
        while parents[asset_id] != asset_id:
            parents[asset_id] = parents[parents[asset_id]]
            asset_id = parents[asset_id]
        return asset_id

    # Seed the closure with the automatic (core) groups so a separation
    # decision is judged against the full automatic/transitive closure.
    for group in set(base_groups.values()):
        members = [asset_id for asset_id, item in base_groups.items() if item == group]
        for other in members[1:]:
            first, second = find(members[0]), find(other)
            if first != second:
                parents[max(first, second)] = min(first, second)
    for decision in decisions:
        if decision.decision == "same_group":
            first, second = find(decision.left_asset_id), find(decision.right_asset_id)
            if first != second:
                parents[max(first, second)] = min(first, second)
    components: dict[str, list[str]] = {}
    for asset_id in known:
        components.setdefault(find(asset_id), []).append(asset_id)
    conflicting = tuple(
        sorted(
            (decision.left_asset_id, decision.right_asset_id)
            for decision in decisions
            if decision.decision == "different_group"
            and find(decision.left_asset_id) == find(decision.right_asset_id)
        )
    )

    # Explicit components: no fabricated metadata relations. Group IDs are
    # deterministic in the sorted membership, so splits are reproducible.
    ordered_components = sorted(
        (sorted(members) for members in components.values()), key=lambda members: tuple(members)
    )
    groups = {
        asset_id: f"group-{index:04d}"
        for index, members in enumerate(ordered_components)
        for asset_id in members
    }
    class_counts_by_group: dict[str, dict[str, int]] = {}
    for item in evidence:
        combined = class_counts_by_group.setdefault(groups[item.asset_id], {})
        for class_id, count in item.class_counts.items():
            combined[class_id] = combined.get(class_id, 0) + count

    old_splits = dict(old_splits or {})
    group_splits: dict[str, str] = {}
    preserved: set[str] = set()
    free: list[str] = []
    for index, members in enumerate(ordered_components):
        group_id = f"group-{index:04d}"
        old_member_splits = {old_splits[asset_id] for asset_id in members if asset_id in old_splits}
        if len(old_member_splits) == 1:
            # Locked assignment, preserved verbatim (train/val/test all
            # survive; an unchanged singleton never moves split because the
            # seed changed).
            group_splits[group_id] = next(iter(old_member_splits))
            preserved.add(group_id)
        else:
            free.append(group_id)
    total_groups = len(ordered_components)
    if free and total_groups > 1:
        if len(free) == 1:
            locked = {group_splits[group] for group in preserved}
            group_splits[free[0]] = "val" if locked == {"train"} else "train"
        else:
            free_members = {
                f"group-{index:04d}": members
                for index, members in enumerate(ordered_components)
                if f"group-{index:04d}" in free
            }
            val_groups = _stratified_val_groups(
                free,
                free_members,
                class_counts_by_group,
                seed=seed,
                validation_fraction=validation_fraction,
            )
            for group_id in free:
                group_splits[group_id] = "val" if group_id in val_groups else "train"

    old_sets = _membership_sets(old_groups or {})
    new_sets = _membership_sets(groups)
    changed_set = {item for item in new_sets if item not in old_sets}
    cross: set[tuple[str, str]] = set()
    for members in ordered_components:
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                if (
                    left in old_splits
                    and right in old_splits
                    and old_splits[left] != old_splits[right]
                ):
                    cross.add((left, right))

    if len(set(group_splits.values())) < 2 or total_groups < 2:
        # Complete collapse: closure/quarantine is still reported, but no
        # usable train/val assignment exists. Apply must fail closed.
        return ProposedGrouping(
            groups=groups,
            relations=tuple(relations),
            borderline=tuple(borderline),
            assignment=None,
            changed_memberships=tuple(sorted(changed_set, key=sorted)),
            cross_split_pairs=tuple(sorted(cross)),
            conflicting_separations=conflicting,
            quarantined_releases=(),
            requires_fresh_holdout=bool(cross),
            new_assignment_version=None,
        )

    asset_splits = {asset_id: group_splits[group] for asset_id, group in groups.items()}
    transitions = tuple(
        sorted(
            (asset_id, old_splits[asset_id], asset_splits[asset_id])
            for asset_id in asset_splits
            if old_splits.get(asset_id) in {"val", "test"} and asset_splits[asset_id] == "train"
        )
    )
    if transitions:
        transition_assets = {asset for asset, _old, _new in transitions}
        changed_set |= {
            frozenset(members) for members in ordered_components if transition_assets & set(members)
        }
    assignment = SourceSplitAssignment(
        assignment_version=_core_identity(thresholds, seed, validation_fraction),
        asset_splits=asset_splits,  # type: ignore[arg-type]
        group_splits=group_splits,  # type: ignore[arg-type]
        group_ids=groups,
        seed=seed,
        algorithm=ALGORITHM,
        reasons={
            group_id: (
                "preserved locked assignment"
                if group_id in preserved
                else "seeded group-level class-count stratification"
            )
            for group_id in group_splits
        },
    )
    return ProposedGrouping(
        groups=groups,
        relations=tuple(relations),
        borderline=tuple(borderline),
        assignment=assignment,
        changed_memberships=tuple(sorted(changed_set, key=sorted)),
        cross_split_pairs=tuple(sorted(cross)),
        conflicting_separations=conflicting,
        quarantined_releases=(),
        requires_fresh_holdout=bool(cross) or bool(transitions),
        new_assignment_version=_published_version(
            assignment,
            decisions,
            _evidence_digest(
                observations,
                relations,
                borderline,
                thresholds=thresholds,
                seed=seed,
                validation_fraction=validation_fraction,
                approved_embedding_version=approved_embedding_version,
            ),
        ),
        split_transitions=transitions,
    )


def _membership_sets(groups: Mapping[str, str]) -> set[frozenset[str]]:
    members: dict[str, set[str]] = {}
    for asset_id, group in groups.items():
        members.setdefault(group, set()).add(asset_id)
    return {frozenset(item) for item in members.values()}


def observation_from_payload(payload: Mapping[str, Any]) -> TrustedObservation:
    """Map one discovery / initial-grouping DTO into a trusted observation."""
    unknown = set(payload) - {
        "asset_id",
        "sha256",
        "phash",
        "embedding",
        "embedding_model",
        "embedding_version",
        "metadata",
        "class_counts",
    }
    if unknown:
        raise ValueError("discovery payload has unknown fields")
    return TrustedObservation(
        asset_id=str(payload["asset_id"]),
        sha256=str(payload["sha256"]),
        phash=int(payload["phash"]),
        embedding=tuple(float(value) for value in payload.get("embedding") or ()),
        embedding_model=payload.get("embedding_model"),
        embedding_version=payload.get("embedding_version"),
        metadata=dict(payload.get("metadata") or {}),
        class_counts=dict(payload.get("class_counts") or {}),
    )


def pair_decision_from_payload(payload: Mapping[str, Any]) -> PairDecision:
    """Map one pair-decision DTO into a persisted decision."""
    unknown = set(payload) - {
        "left_asset_id",
        "right_asset_id",
        "decision",
        "rationale",
        "actor_kind",
        "actor_id",
    }
    if unknown:
        raise ValueError("pair payload has unknown fields")
    return PairDecision(
        left_asset_id=str(payload["left_asset_id"]),
        right_asset_id=str(payload["right_asset_id"]),
        decision=payload["decision"],  # type: ignore[arg-type]
        rationale=str(payload["rationale"]),
        actor_kind=payload["actor_kind"],  # type: ignore[arg-type]
        actor_id=str(payload["actor_id"]),
    )


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _as_dict(value: Any) -> dict[str, Any]:
    decoded = json.loads(value) if isinstance(value, str) else value
    return dict(decoded or {})


def _as_list(value: Any) -> list[Any]:
    decoded = json.loads(value) if isinstance(value, str) else value
    return list(decoded or [])


def _project_hashes(connection: Connection, project_id: uuid.UUID) -> dict[str, str]:
    rows = connection.execute(
        sa.select(source_asset.c.asset_key, source_asset.c.sha256).where(
            source_asset.c.project_id == project_id
        )
    ).all()
    return {key: digest for key, digest in rows}


def _project_assets(connection: Connection, project_id: uuid.UUID) -> dict[str, uuid.UUID]:
    rows = connection.execute(
        sa.select(source_asset.c.id, source_asset.c.asset_key).where(
            source_asset.c.project_id == project_id
        )
    ).all()
    return {key: asset_id for asset_id, key in rows}


def _rows_for_version(
    rows: list[Any], version: str, asset_ids: Mapping[str, uuid.UUID]
) -> tuple[dict[str, str], dict[str, str]]:
    uuid_to_key = {value: key for key, value in asset_ids.items()}
    groups: dict[str, str] = {}
    splits: dict[str, str] = {}
    for row_version, asset_uuid, group, split, _created in rows:
        if row_version != version:
            continue
        key = uuid_to_key.get(asset_uuid)
        if key is not None:
            groups[key] = group
            splits[key] = split
    return groups, splits


def _current_assignment(
    connection: Connection,
    asset_ids: Mapping[str, uuid.UUID],
    project_uuid: uuid.UUID | None = None,
) -> tuple[str | None, dict[str, str], dict[str, str]]:
    if not asset_ids:
        return None, {}, {}
    rows = connection.execute(
        sa.select(
            source_split_assignment.c.assignment_version,
            source_split_assignment.c.asset_id,
            source_split_assignment.c.group_id,
            source_split_assignment.c.split,
            source_split_assignment.c.created_at,
        ).where(source_split_assignment.c.asset_id.in_(list(asset_ids.values())))
    ).all()
    if not rows:
        return None, {}, {}
    # Authoritative publication order: the publication event's
    # expected_revision sequence (monotonic per project, assigned at print
    # time), never created_at ties — two publications in one transaction
    # share now() and lexical version order is arbitrary.
    if project_uuid is not None:
        event_rows = connection.execute(
            sa.select(canonical_event.c.payload, canonical_event.c.expected_revision)
            .where(
                canonical_event.c.event_type == PUBLICATION_EVENT,
                canonical_event.c.aggregate_id == str(project_uuid),
            )
            .order_by(canonical_event.c.expected_revision.desc())
            .limit(1)
        ).all()
        for payload, _revision in event_rows:
            version = _as_dict(payload).get("assignment_version")
            if version:
                groups, splits = _rows_for_version(rows, version, asset_ids)
                if len(groups) == len(asset_ids):
                    return version, groups, splits
    uuid_to_key = {value: key for key, value in asset_ids.items()}
    latest: dict[str, tuple[Any, set[str]]] = {}
    for version, asset_uuid, _group, _split, created in rows:
        key = uuid_to_key.get(asset_uuid)
        if key is None:
            continue
        stamp, keys = latest.get(version, (None, set()))
        latest[version] = (created if stamp is None or created > stamp else stamp, keys | {key})
    # Fallback for assignments published outside this leaf (no publication
    # event): the current version covers every asset; among those, the
    # latest publication stamp wins. Version text breaks residual ties.
    version = max(
        latest,
        key=lambda item: (
            len(latest[item][1]) == len(asset_ids),
            latest[item][0] is not None,
            latest[item][0],
            item,
        ),
    )
    groups, splits = _rows_for_version(rows, version, asset_ids)
    return version, groups, splits


def _next_publication_revision(connection: Connection, project_uuid: uuid.UUID) -> int:
    row = connection.execute(
        sa.select(sa.func.max(canonical_event.c.expected_revision)).where(
            canonical_event.c.event_type == PUBLICATION_EVENT,
            canonical_event.c.aggregate_id == str(project_uuid),
        )
    ).one()
    return int(row[0] or 0) + 1


def _ontology_hash(connection: Connection, project_id: uuid.UUID) -> str | None:
    row = connection.execute(
        sa.select(
            project.c.scope,
            ontology_release.c.classes,
            ontology_release.c.class_map,
        )
        .join(ontology_release, ontology_release.c.project_id == project_id)
        .where(project.c.id == project_id)
        .order_by(ontology_release.c.version.desc())
        .limit(1)
    ).one_or_none()
    if row is None:
        return None
    return completeness_context_hash(_as_dict(row[2]), _as_list(row[1]), row[0])


def _latest_revisions(connection: Connection, asset_ids: Mapping[str, uuid.UUID]) -> dict[str, int]:
    revisions: dict[str, int] = {}
    uuid_to_key = {value: key for key, value in asset_ids.items()}
    rows = connection.execute(
        sa.select(annotation_revision.c.asset_id, annotation_revision.c.revision)
        .where(annotation_revision.c.asset_id.in_(list(asset_ids.values())))
        .order_by(annotation_revision.c.revision.desc())
    ).all()
    for asset_uuid, revision in rows:
        key = uuid_to_key.get(asset_uuid)
        if key is not None and key not in revisions:
            revisions[key] = revision
    return revisions


def _read_decisions(connection: Connection, project_id: uuid.UUID) -> tuple[PairDecision, ...]:
    rows = connection.execute(
        sa.select(canonical_event.c.payload)
        .where(
            canonical_event.c.event_type == DECISION_EVENT,
            canonical_event.c.aggregate_id == str(project_id),
        )
        .order_by(canonical_event.c.created_at)
    ).all()
    decisions = []
    for (payload,) in rows:
        data = _as_dict(payload)
        decisions.append(pair_decision_from_payload(data["decision"]))
    return tuple(decisions)


def _affected_releases(
    connection: Connection,
    *,
    project_id: uuid.UUID,
    old_version: str | None,
    changed_assets: set[str],
    cross_assets: set[str],
) -> tuple[QuarantinedRelease, ...]:
    if old_version is None:
        return ()
    names = changed_assets | cross_assets
    if not names:
        return ()
    # Relevant releases are those frozen under *any* historical assignment
    # that contains affected assets — not only the immediate predecessor.
    # A release pinned to an older version is quarantined too; its stored
    # hash/membership is never modified here.
    rows = connection.execute(
        sa.select(
            canonical_dataset_release.c.id,
            canonical_dataset_release.c.release_key,
            canonical_dataset_release.c.sample_revisions,
        ).where(canonical_dataset_release.c.project_id == project_id)
    ).all()
    quarantined = []
    for release_id, release_key, sample_revisions in rows:
        sampled = set(_as_dict(sample_revisions))
        if sampled & names:
            quarantined.append(
                QuarantinedRelease(
                    release_id=str(release_id),
                    release_key=release_key,
                    reason=(
                        "new relation connects training and holdout groups; historical "
                        "release is quarantined pending fresh independent holdout or retraining"
                    ),
                )
            )
    return tuple(sorted(quarantined, key=lambda item: item.release_key))


def _prepare(
    connection: Connection,
    *,
    project_uuid: uuid.UUID,
    observations: Sequence[TrustedObservation],
    decisions: Sequence[PairDecision],
    pins: LifecyclePins,
    thresholds: GroupingThresholds,
    seed: int,
    validation_fraction: float,
    approved_embedding_version: str | None,
) -> ProposedGrouping:
    asset_ids = _project_assets(connection, project_uuid)
    observed = {item.asset_id for item in observations}
    if observed != set(asset_ids):
        raise ValueError(
            "lifecycle observations must exactly cover owned project sources; "
            "register new sources through ingest before admission"
        )
    canonical_hashes = _project_hashes(connection, project_uuid)
    for item in observations:
        if canonical_hashes.get(item.asset_id) != item.sha256:
            raise ValueError("observation hash must match the immutable canonical source hash")
    current_version, old_groups, old_splits = _current_assignment(
        connection, asset_ids, project_uuid
    )
    if pins.expected_assignment_version != current_version:
        raise StalePinError("stale assignment pin")
    if pins.expected_ontology_hash is not None:
        current_hash = _ontology_hash(connection, project_uuid)
        if pins.expected_ontology_hash != current_hash:
            raise StalePinError("stale ontology pin")
    if pins.expected_annotation_revisions is not None:
        latest = _latest_revisions(connection, asset_ids)
        for asset_id, revision in pins.expected_annotation_revisions.items():
            if latest.get(asset_id) != revision:
                raise StalePinError("stale annotation pin")
    persisted = _read_decisions(connection, project_uuid)
    known_hashes = {decision_hash(item) for item in persisted}
    effective: list[PairDecision] = list(persisted)
    for item in decisions:
        digest = decision_hash(item)
        if digest not in known_hashes:
            known_hashes.add(digest)
            effective.append(item)
    effective = list({decision_hash(item): item for item in effective}.values())
    proposal = compute_proposal(
        observations,
        effective,
        thresholds=thresholds,
        seed=seed,
        validation_fraction=validation_fraction,
        approved_embedding_version=approved_embedding_version,
        old_groups=old_groups,
        old_splits=old_splits,
    )
    changed_assets = (
        set().union(*proposal.changed_memberships) if proposal.changed_memberships else set()
    )
    cross_assets = {asset for pair in proposal.cross_split_pairs for asset in pair}
    transition_assets = {asset for asset, _old, _new in proposal.split_transitions}
    quarantined = _affected_releases(
        connection,
        project_id=project_uuid,
        old_version=current_version,
        changed_assets=changed_assets | transition_assets,
        cross_assets=cross_assets,
    )
    return ProposedGrouping(
        groups=proposal.groups,
        relations=proposal.relations,
        borderline=proposal.borderline,
        assignment=proposal.assignment,
        changed_memberships=proposal.changed_memberships,
        cross_split_pairs=proposal.cross_split_pairs,
        conflicting_separations=proposal.conflicting_separations,
        quarantined_releases=quarantined,
        requires_fresh_holdout=proposal.requires_fresh_holdout,
        new_assignment_version=proposal.new_assignment_version,
        split_transitions=proposal.split_transitions,
    )


def propose_regroup(
    connection: Connection,
    *,
    project_id: str | uuid.UUID,
    observations: Sequence[TrustedObservation],
    decisions: Sequence[PairDecision] = (),
    pins: LifecyclePins | None = None,
    thresholds: GroupingThresholds | None = None,
    seed: int = 20261107,
    validation_fraction: float = 0.25,
    approved_embedding_version: str | None = None,
) -> ProposedGrouping:
    """Return the read-only proposed membership / invalidation / quarantine."""
    return _prepare(
        connection,
        project_uuid=_uuid(project_id),
        observations=observations,
        decisions=decisions,
        pins=pins or LifecyclePins(),
        thresholds=thresholds or GroupingThresholds(),
        seed=seed,
        validation_fraction=validation_fraction,
        approved_embedding_version=approved_embedding_version,
    )


def apply_regroup(
    connection: Connection,
    *,
    project_id: str | uuid.UUID,
    observations: Sequence[TrustedObservation],
    decisions: Sequence[PairDecision] = (),
    pins: LifecyclePins | None = None,
    actor_id: str,
    thresholds: GroupingThresholds | None = None,
    seed: int = 20261107,
    validation_fraction: float = 0.25,
    approved_embedding_version: str | None = None,
    audit_hook: Callable[[], None] | None = None,
) -> PublishedGrouping:
    """Publish a regroup inside one savepoint; any failure rolls back all of it.

    The caller owns the outer transaction. This function takes a project
    row lock, re-validates pins against locked state, then appends pair
    decisions, similarity relations, borderline reviews, the new locked
    assignment rows and the final attributed publication event. The
    ``audit_hook`` runs inside the same savepoint so an audit or caller
    failure aborts publication in full.
    """
    if not actor_id:
        raise ValueError("publication requires an authenticated actor ID")
    project_uuid = _uuid(project_id)
    thresholds = thresholds or GroupingThresholds()
    with connection.begin_nested():
        connection.execute(
            sa.select(project.c.id).where(project.c.id == project_uuid).with_for_update()
        ).one()
        proposal = _prepare(
            connection,
            project_uuid=project_uuid,
            observations=observations,
            decisions=decisions,
            pins=pins or LifecyclePins(),
            thresholds=thresholds,
            seed=seed,
            validation_fraction=validation_fraction,
            approved_embedding_version=approved_embedding_version,
        )
        if proposal.assignment is None or proposal.new_assignment_version is None:
            # Closure/invalidation was reported by the proposal (quarantine,
            # cross-split pairs), but no usable train/val assignment exists.
            # Fail closed: write nothing; training/evaluation must not start
            # from an unusable assignment.
            raise ValueError(
                "regroup cannot produce a usable train/val assignment; "
                "quarantined releases require a fresh independent holdout or retraining"
            )
        current_version, _, _ = _current_assignment(
            connection, _project_assets(connection, project_uuid), project_uuid
        )
        asset_ids = _project_assets(connection, project_uuid)
        if proposal.new_assignment_version == current_version:
            return PublishedGrouping(
                assignment_version=current_version,
                groups=proposal.groups,
                quarantined_releases=proposal.quarantined_releases,
                requires_fresh_holdout=proposal.requires_fresh_holdout,
                published=False,
            )
        existing_versions = {
            row[0]
            for row in connection.execute(
                sa.select(source_split_assignment.c.assignment_version).where(
                    source_split_assignment.c.asset_id.in_(list(asset_ids.values()))
                )
            ).all()
        }
        if proposal.new_assignment_version in existing_versions:
            # Returning to a historical grouping (A -> B -> A): reuse the
            # immutable rows safely instead of violating the production
            # UNIQUE(assignment_version, asset_id), and record the
            # publication transition so current-version selection moves
            # back to the reused version.
            connection.execute(
                sa.insert(canonical_event).values(
                    id=uuid.uuid4(),
                    event_type=PUBLICATION_EVENT,
                    aggregate_id=str(project_uuid),
                    expected_revision=_next_publication_revision(connection, project_uuid),
                    payload={
                        "assignment_version": proposal.new_assignment_version,
                        "previous_assignment_version": current_version,
                        "reused_existing_version": True,
                        "groups": proposal.groups,
                        "requires_fresh_holdout": proposal.requires_fresh_holdout,
                    },
                    actor_kind="system",
                    actor_id=actor_id,
                )
            )
            if audit_hook is not None:
                audit_hook()
            return PublishedGrouping(
                assignment_version=proposal.new_assignment_version,
                groups=proposal.groups,
                quarantined_releases=proposal.quarantined_releases,
                requires_fresh_holdout=proposal.requires_fresh_holdout,
                published=False,
            )
        existing_relations = {
            (row[0], row[1], row[2])
            for row in connection.execute(
                sa.select(
                    similarity_relation.c.left_asset_id,
                    similarity_relation.c.right_asset_id,
                    similarity_relation.c.reason,
                )
            ).all()
        }
        for relation in proposal.relations:
            key = (
                asset_ids[relation.left_asset_id],
                asset_ids[relation.right_asset_id],
                relation.reason,
            )
            if key in existing_relations:
                continue
            connection.execute(
                sa.insert(similarity_relation).values(
                    id=uuid.uuid4(),
                    left_asset_id=key[0],
                    right_asset_id=key[1],
                    reason=relation.reason,
                    evidence=relation.evidence,
                    embedding_model=(
                        thresholds.embedding_model
                        if relation.reason == "embedding_cosine"
                        else None
                    ),
                    algorithm_version="union-find-grouped-stratified-v2",
                )
            )
        existing_borderline = {
            (row[0], row[1], row[2], row[3])
            for row in connection.execute(
                sa.select(
                    borderline_review.c.left_asset_id,
                    borderline_review.c.right_asset_id,
                    borderline_review.c.metric,
                    borderline_review.c.value,
                )
            ).all()
        }
        for item in proposal.borderline:
            key = (
                asset_ids[item.left_asset_id],
                asset_ids[item.right_asset_id],
                item.metric,
                str(item.value),
            )
            if key in existing_borderline:
                continue
            connection.execute(
                sa.insert(borderline_review).values(
                    id=uuid.uuid4(),
                    left_asset_id=key[0],
                    right_asset_id=key[1],
                    metric=item.metric,
                    value=str(item.value),
                    review_state="pending",
                    algorithm_version="union-find-grouped-stratified-v2",
                )
            )
        persisted_hashes = {
            decision_hash(item) for item in _read_decisions(connection, project_uuid)
        }
        for decision in decisions:
            if decision_hash(decision) in persisted_hashes:
                continue
            connection.execute(
                sa.insert(canonical_event).values(
                    id=uuid.uuid4(),
                    event_type=DECISION_EVENT,
                    aggregate_id=str(project_uuid),
                    expected_revision=0,
                    payload={
                        "decision": {
                            "left_asset_id": decision.left_asset_id,
                            "right_asset_id": decision.right_asset_id,
                            "decision": decision.decision,
                            "rationale": decision.rationale,
                            "actor_kind": decision.actor_kind,
                            "actor_id": decision.actor_id,
                        },
                        "decision_hash": decision_hash(decision),
                    },
                    actor_kind=decision.actor_kind,
                    actor_id=decision.actor_id,
                )
            )
            persisted_hashes.add(decision_hash(decision))
        for asset_id, group in proposal.groups.items():
            connection.execute(
                sa.insert(source_split_assignment).values(
                    id=uuid.uuid4(),
                    assignment_version=proposal.new_assignment_version,
                    asset_id=asset_ids[asset_id],
                    group_id=group,
                    split=proposal.assignment.asset_splits[asset_id],
                    seed=proposal.assignment.seed,
                    algorithm=proposal.assignment.algorithm,
                    thresholds={
                        "phash_max_distance": thresholds.phash_max_distance,
                        "phash_review_distance": thresholds.phash_review_distance,
                        "embed_min_cosine": thresholds.embed_min_cosine,
                        "embed_review_cosine": thresholds.embed_review_cosine,
                        "embedding_model": thresholds.embedding_model,
                        "embedding_version": approved_embedding_version,
                        "seed": seed,
                        "validation_fraction": validation_fraction,
                        "decisions": sorted(decision_hash(item) for item in decisions),
                    },
                    reason=proposal.assignment.reasons[group],
                    locked=True,
                )
            )
        connection.execute(
            sa.insert(canonical_event).values(
                id=uuid.uuid4(),
                event_type=PUBLICATION_EVENT,
                aggregate_id=str(project_uuid),
                expected_revision=_next_publication_revision(connection, project_uuid),
                payload={
                    "assignment_version": proposal.new_assignment_version,
                    "previous_assignment_version": current_version,
                    "groups": proposal.groups,
                    "changed_memberships": [sorted(item) for item in proposal.changed_memberships],
                    "cross_split_pairs": [list(pair) for pair in proposal.cross_split_pairs],
                    "conflicting_separations": [
                        list(pair) for pair in proposal.conflicting_separations
                    ],
                    "split_transitions": [list(item) for item in proposal.split_transitions],
                    "quarantined_releases": [
                        item.release_key for item in proposal.quarantined_releases
                    ],
                    "requires_fresh_holdout": proposal.requires_fresh_holdout,
                },
                actor_kind="system",
                actor_id=actor_id,
            )
        )
        if audit_hook is not None:
            audit_hook()
    return PublishedGrouping(
        assignment_version=proposal.new_assignment_version,
        groups=proposal.groups,
        quarantined_releases=proposal.quarantined_releases,
        requires_fresh_holdout=proposal.requires_fresh_holdout,
        published=True,
    )
