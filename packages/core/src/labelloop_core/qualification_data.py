"""Immutable qualification identities and strict scalar validation (no I/O)."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, is_dataclass

from .training import require_digest


def require_name(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("qualification identities must be nonblank strings")


def require_names(values: tuple[str, ...], *, allow_empty: bool = False) -> None:
    if not isinstance(values, tuple) or (not values and not allow_empty):
        raise ValueError("qualification membership must be an explicit nonempty tuple")
    for value in values:
        require_name(value)
    if len(set(values)) != len(values):
        raise ValueError("qualification membership must be unique")


def require_count(value: int, *, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError("qualification counts must be bounded integers")


def require_probability(value: float) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("qualification metrics must be finite probabilities")


def canonical_record(value: object) -> str:
    def encode(item: object) -> object:
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        if isinstance(item, frozenset):
            return sorted(item)
        raise TypeError("unsupported qualification record")

    return json.dumps(value, default=encode, sort_keys=True, separators=(",", ":"), allow_nan=False)


def record_sha256(value: object) -> str:
    return hashlib.sha256(canonical_record(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PipelineIdentity:
    project_id: str
    teacher_sha256: str
    recipe_id: str
    preprocessing_sha256: str
    reviewer_model_version: str
    reviewer_provider_version: str
    prompt_sha256: str
    schema_sha256: str
    reference_pack_sha256: str
    ontology_sha256: str
    geometry_policy_sha256: str
    acceptance_policy_sha256: str

    def __post_init__(self) -> None:
        for value in (
            self.project_id,
            self.recipe_id,
            self.reviewer_model_version,
            self.reviewer_provider_version,
        ):
            require_name(value)
        for value in (
            self.teacher_sha256,
            self.preprocessing_sha256,
            self.prompt_sha256,
            self.schema_sha256,
            self.reference_pack_sha256,
            self.ontology_sha256,
            self.geometry_policy_sha256,
            self.acceptance_policy_sha256,
        ):
            require_digest(value)

    @property
    def pipeline_id(self) -> str:
        return record_sha256(self)


@dataclass(frozen=True, slots=True, order=True)
class ScopeCell:
    class_id: str
    source_id: str
    condition: str

    def __post_init__(self) -> None:
        for value in (self.class_id, self.source_id, self.condition):
            require_name(value)


def require_scope(cells: tuple[ScopeCell, ...]) -> None:
    if not cells or len(set(cells)) != len(cells):
        raise ValueError("qualification requires unique nonempty class/source/condition scope")


@dataclass(frozen=True, slots=True)
class DriftIncident:
    """Authoritative post-pause evidence linkage, not a model-supplied freshness claim."""

    project_id: str
    recipe_id: str
    pause_id: str
    opened_revision: int
    release_sha256: str

    def __post_init__(self) -> None:
        for value in (self.project_id, self.recipe_id, self.pause_id):
            require_name(value)
        require_count(self.opened_revision, minimum=1)
        require_digest(self.release_sha256)


def require_incidents(incidents: tuple[DriftIncident, ...]) -> None:
    if len({(item.project_id, item.recipe_id, item.pause_id) for item in incidents}) != len(
        incidents
    ):
        raise ValueError("drift remediation incident links must be unique")


@dataclass(frozen=True, slots=True)
class SourceGroup:
    asset_id: str
    group_id: str
    source_id: str
    conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.asset_id, self.group_id, self.source_id):
            require_name(value)
        require_names(self.conditions)
