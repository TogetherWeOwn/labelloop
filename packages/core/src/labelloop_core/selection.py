"""Deterministic active review selection over declared, precomputed evidence."""

import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Literal

from .domain import AnnotationRevision
from .evidence import EvidenceScope

SIGNALS = frozenset(
    {
        "disagreement",
        "low_confidence",
        "omission",
        "confusion",
        "novelty",
        "rare_class",
        "difficult_size",
        "distribution_shift",
    }
)
Stratum = Literal["zero_detection", "missing_object", "ordinary_random", "ranked"]


@dataclass(frozen=True, slots=True)
class SelectionSignals:
    disagreement: float
    low_confidence: float
    omission: float
    confusion: float
    novelty: float
    rare_class: float
    difficult_size: float
    distribution_shift: float

    def __post_init__(self) -> None:
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in asdict(self).values()):
            raise ValueError("selection signals must be finite values in [0,1]")


@dataclass(frozen=True, slots=True)
class SelectionCandidate:
    asset_id: str
    revision: AnnotationRevision
    evidence_id: str
    signal_version: str
    signals: SelectionSignals
    detection_count: int
    cluster_id: str
    teacher_generation: str
    cost_units: int

    def __post_init__(self) -> None:
        if not all((self.asset_id, self.evidence_id, self.cluster_id, self.teacher_generation)):
            raise ValueError("selection evidence, cluster and teacher must be identified")
        if type(self.detection_count) is not int or self.detection_count < 0:
            raise ValueError("detection count must be a nonnegative integer")
        if type(self.cost_units) is not int or self.cost_units < 1:
            raise ValueError("selection cost units must be positive integers")


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    version: str
    signal_version: str
    seed: int
    weights: dict[str, float]
    max_items: int
    max_candidates: int
    budget_units: int
    zero_detection_quota: int
    missing_object_quota: int
    ordinary_random_quota: int
    omission_threshold: float
    ordinary_max_signal: float
    max_per_group: int
    max_per_cluster: int
    diversity_weight: float

    def __post_init__(self) -> None:
        if not self.version or not self.signal_version:
            raise ValueError("selection and signal policy versions are required")
        if set(self.weights) != SIGNALS or any(
            not math.isfinite(v) or not 0 < v <= 1 for v in self.weights.values()
        ):
            raise ValueError("explicit positive weights are required for every selection signal")
        counts = (
            self.max_items,
            self.budget_units,
            self.zero_detection_quota,
            self.missing_object_quota,
            self.ordinary_random_quota,
            self.max_per_group,
            self.max_per_cluster,
        )
        if any(type(v) is not int or v < 1 for v in counts):
            raise ValueError("selection budgets, quotas and caps must be positive integers")
        if type(self.max_candidates) is not int or self.max_candidates < self.max_items:
            raise ValueError("candidate bound must be an integer covering the item budget")
        if sum(counts[2:5]) > self.max_items:
            raise ValueError("mandatory strata exceed the item budget")
        if not 0 <= self.ordinary_max_signal < self.omission_threshold <= 1:
            raise ValueError("ordinary and omission thresholds must be finite and disjoint")
        if not math.isfinite(self.diversity_weight) or self.diversity_weight < 0:
            raise ValueError("diversity weight must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class SelectedEvidence:
    asset_id: str
    revision: int
    evidence_id: str
    stratum: Stratum
    score: float
    cost_units: int


@dataclass(frozen=True, slots=True)
class SelectionResult:
    input_sha256: str
    policy_version: str
    signal_version: str
    items: tuple[SelectedEvidence, ...]
    used_units: int


def select_evidence(
    scope: EvidenceScope,
    candidates: tuple[SelectionCandidate, ...],
    policy: SelectionPolicy,
) -> SelectionResult:
    """Reserve distinct quota slots, then greedily rank within hard resource caps.

    Insufficient strata/budget fail closed, rather than quietly weakening quotas.
    Confidence is only one prioritization signal, never acceptance evidence.
    """
    if len(candidates) > policy.max_candidates:
        raise ValueError("selection exceeds the configured candidate bound")
    ordered = sorted(candidates, key=lambda item: item.asset_id)
    if not ordered or len({item.asset_id for item in ordered}) != len(ordered):
        raise ValueError("selection requires unique source-bound evidence")
    for item in ordered:
        source = scope.require_training_source(item.asset_id)
        scope.validate_revision(item.revision)
        if item.signal_version != policy.signal_version:
            raise ValueError("selection signal evidence version differs from policy")
        for obj in item.revision.objects:
            obj.box.within(source.width, source.height)
        if item.revision.gold_eligible:
            raise ValueError("gold evidence cannot guide active training selection")
    groups: Counter[str] = Counter()
    clusters: Counter[str] = Counter()
    selected: list[SelectedEvidence] = []
    used_units = 0
    remaining = {item.asset_id: item for item in ordered}

    def score(item: SelectionCandidate) -> float:
        values: dict[str, float] = asdict(item.signals)
        weighted = sum(values[key] * policy.weights[key] for key in sorted(SIGNALS))
        return weighted + policy.diversity_weight / (1 + clusters[item.cluster_id])

    def fits(item: SelectionCandidate) -> bool:
        return (
            used_units + item.cost_units <= policy.budget_units
            and groups[scope.assignment.group_ids[item.asset_id]] < policy.max_per_group
            and clusters[item.cluster_id] < policy.max_per_cluster
        )

    def random_rank(item: SelectionCandidate) -> str:
        return hashlib.sha256(f"{policy.seed}:{item.asset_id}".encode()).hexdigest()

    def take(stratum: Stratum, count: int) -> None:
        nonlocal used_units
        for _ in range(count):
            pool = [item for item in remaining.values() if fits(item)]
            if stratum == "zero_detection":
                pool = [item for item in pool if item.detection_count == 0]
            elif stratum == "missing_object":
                pool = [item for item in pool if item.signals.omission >= policy.omission_threshold]
            elif stratum == "ordinary_random":
                pool = [
                    item
                    for item in pool
                    if item.detection_count > 0
                    and item.revision.training_eligible
                    and max(asdict(item.signals).values()) <= policy.ordinary_max_signal
                ]
            if not pool:
                if stratum == "ranked":
                    return
                raise ValueError(f"insufficient evidence or budget for {stratum} quota")
            chosen = min(
                pool,
                key=lambda item: (
                    0.0 if stratum == "ordinary_random" else -score(item),
                    random_rank(item),
                    item.asset_id,
                ),
            )
            selected.append(
                SelectedEvidence(
                    chosen.asset_id,
                    chosen.revision.revision,
                    chosen.evidence_id,
                    stratum,
                    score(chosen),
                    chosen.cost_units,
                )
            )
            used_units += chosen.cost_units
            groups[scope.assignment.group_ids[chosen.asset_id]] += 1
            clusters[chosen.cluster_id] += 1
            del remaining[chosen.asset_id]

    take("zero_detection", policy.zero_detection_quota)
    take("missing_object", policy.missing_object_quota)
    take("ordinary_random", policy.ordinary_random_quota)
    take("ranked", policy.max_items - len(selected))
    scope_data = asdict(scope)
    scope_data["sources"] = sorted(scope_data["sources"], key=lambda item: item["asset_id"])
    scope_data["ontology"]["classes"] = sorted(
        scope_data["ontology"]["classes"], key=lambda item: item["stable_id"]
    )
    scope_data["evaluation_asset_ids"] = sorted(scope.evaluation_asset_ids)
    encoded = json.dumps(
        {
            "algorithm": "active-quota-v1",
            "scope": scope_data,
            "candidates": [asdict(item) for item in ordered],
            "policy": asdict(policy),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return SelectionResult(
        hashlib.sha256(encoded.encode()).hexdigest(),
        policy.version,
        policy.signal_version,
        tuple(selected),
        used_units,
    )
