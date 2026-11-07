"""Near-duplicate grouping and seeded group-level class stratification."""

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
from PIL import Image

from .domain import SourceSplitAssignment, Split

ALGORITHM = "union-find-grouped-stratified-v2"
METADATA_KEYS = frozenset({"session", "sequence", "subject"})


@dataclass(frozen=True, slots=True)
class AssetEvidence:
    asset_id: str
    sha256: str
    phash: int
    embedding: tuple[float, ...]
    metadata: dict[str, str]
    class_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class GroupingThresholds:
    phash_max_distance: int = 8
    phash_review_distance: int = 12
    embed_min_cosine: float = 0.95
    embed_review_cosine: float = 0.90
    embedding_model: str = "none-v1"


@dataclass(frozen=True, slots=True)
class SimilarityRelation:
    left_asset_id: str
    right_asset_id: str
    reason: Literal["exact_hash", "phash", "embedding_cosine", "metadata"]
    evidence: str


@dataclass(frozen=True, slots=True)
class BorderlineReview:
    left_asset_id: str
    right_asset_id: str
    metric: Literal["phash", "embedding_cosine"]
    value: float


def phash_grayscale(image: Image.Image) -> int:
    """phash-dct64-v1: orthogonal DCT of the whole 32x32 luminance raster."""
    pixels = np.asarray(
        image.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64
    )
    basis = np.cos(np.pi * (2 * np.arange(32)[:, None] + 1) * np.arange(32)[None, :] / 64)
    values = (basis.T @ pixels @ basis)[:8, :8].flatten()
    median = np.median(values[1:])
    return int("".join("1" if value > median else "0" for value in values), 2)


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("embedding dimensions must match and be non-empty")
    left_norm, right_norm = (sum(value * value for value in row) ** 0.5 for row in (left, right))
    if not left_norm or not right_norm:
        raise ValueError("zero embeddings have no meaningful cosine similarity")
    return float(sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm))


def group_sources(
    evidence: list[AssetEvidence], thresholds: GroupingThresholds | None = None
) -> tuple[dict[str, str], list[SimilarityRelation], list[BorderlineReview]]:
    thresholds = thresholds or GroupingThresholds()
    ordered = sorted(evidence, key=lambda record: record.asset_id)
    parents = {record.asset_id: record.asset_id for record in ordered}
    if len(parents) != len(ordered) or not ordered:
        raise ValueError("grouping requires unique source IDs")

    def find(asset_id: str) -> str:
        while parents[asset_id] != asset_id:
            parents[asset_id] = parents[parents[asset_id]]
            asset_id = parents[asset_id]
        return asset_id

    relations: list[SimilarityRelation] = []
    reviews: list[BorderlineReview] = []
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            matched: list[SimilarityRelation] = []
            if left.sha256 == right.sha256:
                matched.append(SimilarityRelation(left.asset_id, right.asset_id, "exact_hash", ""))
            distance = (left.phash ^ right.phash).bit_count()
            if distance <= thresholds.phash_max_distance:
                matched.append(
                    SimilarityRelation(left.asset_id, right.asset_id, "phash", str(distance))
                )
            elif distance <= thresholds.phash_review_distance:
                reviews.append(BorderlineReview(left.asset_id, right.asset_id, "phash", distance))
            if left.embedding and right.embedding:
                similarity = cosine(left.embedding, right.embedding)
                if similarity >= thresholds.embed_min_cosine:
                    matched.append(
                        SimilarityRelation(
                            left.asset_id, right.asset_id, "embedding_cosine", str(similarity)
                        )
                    )
                elif similarity >= thresholds.embed_review_cosine:
                    reviews.append(
                        BorderlineReview(
                            left.asset_id, right.asset_id, "embedding_cosine", similarity
                        )
                    )
            shared = sorted(
                key
                for key in METADATA_KEYS
                if left.metadata.get(key) and left.metadata.get(key) == right.metadata.get(key)
            )
            if shared:
                matched.append(
                    SimilarityRelation(left.asset_id, right.asset_id, "metadata", ",".join(shared))
                )
            for match in matched:
                first, second = sorted((find(left.asset_id), find(right.asset_id)))
                if first != second:
                    parents[second] = first
                relations.append(match)
    roots = sorted({find(record.asset_id) for record in ordered})
    groups = {
        record.asset_id: f"group-{roots.index(find(record.asset_id)):04d}" for record in ordered
    }
    return groups, relations, reviews


def assign_splits(
    evidence: list[AssetEvidence],
    *,
    thresholds: GroupingThresholds | None = None,
    seed: int = 20261107,
    validation_fraction: float = 0.25,
) -> SourceSplitAssignment:
    """Seeded class-count balancing within a group cap, exact for up to 16 groups."""
    groups, _, _ = group_sources(evidence, thresholds)
    if len(set(groups.values())) < 2:
        raise ValueError("independent train and validation groups are required")
    if not 0 < validation_fraction < 1:
        raise ValueError("validation fraction must be in (0,1)")
    threshold_values = thresholds or GroupingThresholds()
    identity = hashlib.sha256(
        json.dumps(
            {
                "thresholds": asdict(threshold_values),
                "validation_fraction": validation_fraction,
                "seed": seed,
                "algorithm": ALGORITHM,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:16]
    members: dict[str, list[AssetEvidence]] = {}
    for record in evidence:
        members.setdefault(groups[record.asset_id], []).append(record)
    counts = {
        group: sum((Counter(record.class_counts) for record in records), Counter())
        for group, records in members.items()
    }
    classes = {class_id for row in counts.values() for class_id in row} or {"negative"}
    targets = {
        class_id: max(
            1, round(sum(row.get(class_id, 0) for row in counts.values()) * validation_fraction)
        )
        for class_id in classes
    }
    shuffled = sorted(
        members, key=lambda group: hashlib.sha256(f"{seed}:{group}".encode()).hexdigest()
    )
    positions = {group: index for index, group in enumerate(shuffled)}
    val_cap = min(len(members) - 1, max(1, round(len(members) * validation_fraction)))

    def best_candidate(candidate: list[str]) -> tuple[float, float, tuple[int, ...]]:
        deficit = sum(
            max(0, targets[class_id] - sum(counts[group][class_id] for group in candidate))
            for class_id in classes
        )
        size = abs(len(candidate) - val_cap)
        # Tie breaking uses the seeded order, never source iteration order.
        rank = tuple(positions[group] for group in candidate)
        return deficit, size, rank

    best: list[str] | None = None
    remaining = list(shuffled)

    def search(selected: list[str], tail: list[str]) -> None:
        nonlocal best
        if len(selected) > val_cap:
            return
        if not tail:
            if (
                selected
                and len(selected) < len(members)
                and (best is None or best_candidate(selected) < best_candidate(best))
            ):
                best = list(selected)
            return
        search(selected + [tail[0]], tail[1:])
        search(selected, tail[1:])

    if len(members) <= 16:
        search([], remaining)
    else:
        selected: list[str] = []
        covered: Counter[str] = Counter()
        while len(selected) < val_cap:
            group = max(
                remaining,
                key=lambda group: (
                    sum(
                        min(counts[group][class_id], targets[class_id])
                        for class_id in classes
                        if covered[class_id] < targets[class_id]
                    ),
                    -positions[group],
                ),
            )
            selected.append(group)
            covered.update(counts[group])
            remaining.remove(group)
        best = selected
    if best is None:
        raise ValueError("assignment cannot establish non-empty train and validation")
    chosen = set(best)
    assignment: dict[str, Split] = {
        group: "val" if group in chosen else "train" for group in members
    }
    validation_counts = sum((counts[group] for group in chosen), Counter())
    reasons = {
        group: (
            "seeded group-level class-count stratification"
            if validation_counts
            else "independent training group"
        )
        for group in members
    }
    return SourceSplitAssignment(
        assignment_version=f"{ALGORITHM}-{identity}",
        asset_splits={asset_id: assignment[group] for asset_id, group in groups.items()},
        group_splits=assignment,
        group_ids=groups,
        seed=seed,
        algorithm=ALGORITHM,
        reasons=reasons,
    )
