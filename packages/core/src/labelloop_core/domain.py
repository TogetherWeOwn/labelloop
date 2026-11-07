"""Canonical, domain-independent annotation records and revision guards."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from .geometry import Box

LabelState = Literal["identified", "ambiguous", "unknown", "insufficient_evidence"]
ApprovalState = Literal[
    "draft", "proposed", "auto_accepted", "human_accepted", "rejected", "superseded"
]
ReviewTier = Literal[
    "unreviewed", "machine_verified_no_human_review", "human_reviewed", "adjudicated"
]
ActorKind = Literal["human", "model", "system"]
Split = Literal["train", "val", "test"]


@dataclass(frozen=True, slots=True)
class ClassDefinition:
    stable_id: str
    name: str
    definition: str
    aliases: tuple[str, ...] = ()
    parent_id: str | None = None

    def __post_init__(self) -> None:
        if not self.stable_id or not self.name or not self.definition:
            raise ValueError("class identity, name and definition are required")


@dataclass(frozen=True, slots=True)
class DatasetObject:
    object_id: str
    class_id: str
    box: Box
    label_state: LabelState = "identified"
    attributes: dict[str, str] = field(default_factory=dict)
    occluded: bool = False
    truncated_at_image_edge: bool = False


@dataclass(frozen=True, slots=True)
class AnnotationRevision:
    revision: int
    objects: tuple[DatasetObject, ...]
    actor_kind: ActorKind
    parent_revision: int | None = None
    approval_state: ApprovalState = "draft"
    review_tier: ReviewTier = "unreviewed"
    scope_completeness: Literal["unreviewed", "partial", "exhaustive_for_scope"] = "unreviewed"
    training_eligible: bool = False
    gold_eligible: bool = False

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("annotation revision numbers are positive")
        if self.revision == 1 and self.parent_revision is not None:
            raise ValueError("the first revision has no parent")
        if self.revision > 1 and self.parent_revision != self.revision - 1:
            raise ValueError("annotation revisions must have contiguous parent lineage")
        identifiers = [obj.object_id for obj in self.objects]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("object IDs must be unique in one revision")
        if self.approval_state == "human_accepted" and self.actor_kind != "human":
            raise ValueError("only an authenticated human can create human approval")
        if self.review_tier in {"human_reviewed", "adjudicated"} and self.actor_kind != "human":
            raise ValueError("model or system output cannot assert human review")
        if self.training_eligible or self.gold_eligible:
            if self.scope_completeness != "exhaustive_for_scope":
                raise ValueError("release eligibility requires exhaustive scope completeness")
            if self.approval_state not in {"auto_accepted", "human_accepted"}:
                raise ValueError("release eligibility requires an accepted annotation revision")
            if any(obj.label_state != "identified" for obj in self.objects):
                raise ValueError("unresolved objects prevent ordinary training eligibility")
        if self.gold_eligible and self.review_tier != "adjudicated":
            raise ValueError("gold eligibility requires independent adjudication")

    def next_revision(
        self,
        *,
        objects: Iterable[DatasetObject],
        expected_revision: int,
        actor_kind: ActorKind,
        **changes: Any,  # dataclasses.replace resolves the validated field types
    ) -> "AnnotationRevision":
        if expected_revision != self.revision:
            raise ValueError("stale expected annotation revision")
        next_objects = tuple(objects)
        if next_objects != self.objects:
            if (
                changes.get("training_eligible")
                or changes.get("gold_eligible")
                or changes.get("scope_completeness") == "exhaustive_for_scope"
            ):
                raise ValueError("changed object sets need a new authoritative completeness review")
            changes = {
                "approval_state": "draft",
                "review_tier": "unreviewed",
                "scope_completeness": "unreviewed",
                "training_eligible": False,
                "gold_eligible": False,
                **changes,
            }
        return replace(
            self,
            revision=self.revision + 1,
            parent_revision=self.revision,
            objects=next_objects,
            actor_kind=actor_kind,
            **changes,
        )


@dataclass(frozen=True, slots=True)
class CompletenessAssessment:
    ontology_hash: str
    object_set_revision: int
    scope_completeness: Literal["unreviewed", "partial", "exhaustive_for_scope"]
    actor_kind: ActorKind
    output_truncated: bool = False
    unresolved_regions: tuple[Box, ...] = ()

    def valid_for(self, ontology_hash: str, revision: int) -> bool:
        return (
            self.ontology_hash == ontology_hash
            and self.object_set_revision == revision
            and self.scope_completeness == "exhaustive_for_scope"
            and not self.output_truncated
            and not self.unresolved_regions
        )


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    reviewer_id: str
    actor_kind: ActorKind
    decision: str
    expected_revision: int
    correction: AnnotationRevision | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    workspace_id: str
    task: str
    scope: str
    domain: str


@dataclass(frozen=True, slots=True)
class OntologyRelease:
    ontology_id: str
    project_id: str
    version: int
    classes: tuple[ClassDefinition, ...]
    class_map: dict[str, int]

    def __post_init__(self) -> None:
        identifiers = tuple(item.stable_id for item in self.classes)
        if set(identifiers) != set(self.class_map):
            raise ValueError("class map must exactly resolve stable class IDs")
        values = [self.class_map[identifier] for identifier in identifiers]
        if len(set(identifiers)) != len(identifiers) or sorted(values) != list(range(len(values))):
            raise ValueError("class map must be unique and dense")

    def mapping(self) -> dict[str, int]:
        return {item.stable_id: self.class_map[item.stable_id] for item in self.classes}


@dataclass(frozen=True, slots=True)
class SourceAsset:
    asset_id: str
    project_id: str
    sha256: str
    object_key: str
    width: int
    height: int
    exif_orientation: int = 1
    source_uri: str = ""
    source_group: str | None = None

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or any(char not in "0123456789abcdef" for char in self.sha256):
            raise ValueError("asset hash must be a lowercase SHA-256 digest")
        if self.object_key != f"sha256/{self.sha256[:2]}/{self.sha256}":
            raise ValueError("source object storage must be content-addressed")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("source dimensions must be positive")


@dataclass(frozen=True, slots=True)
class ReferenceExample:
    reference_id: str
    asset_id: str
    class_id: str
    evidence_provenance: str
    approved: bool
    validity_scope: str
    crop: Box | None = None


@dataclass(frozen=True, slots=True)
class SourceSplitAssignment:
    assignment_version: str
    asset_splits: dict[str, Split]
    group_splits: dict[str, Split]
    group_ids: dict[str, str]
    seed: int
    algorithm: str
    reasons: dict[str, str]
    locked: bool = True

    def split_for(self, asset_id: str) -> Split:
        split = self.asset_splits[asset_id]
        group = self.group_ids[asset_id]
        if self.group_splits[group] != split:
            raise ValueError("asset split disagrees with its source group")
        return split


@dataclass(frozen=True, slots=True)
class Recipe:
    recipe_id: str
    output_size: int
    interpolation: str
    allow_upscale: bool = True
    padding_color: tuple[int, int, int] = (114, 114, 114)
    min_target_px: int = 4
    min_retained_fraction: float = 0.01
    encoder: str = "JPEG"
    encoder_options: dict[str, int | bool] = field(
        default_factory=lambda: {"quality": 95, "optimize": False, "progressive": False}
    )

    def __post_init__(self) -> None:
        if self.output_size <= 0 or self.min_target_px <= 0:
            raise ValueError("recipe output and minimum target sizes must be positive")
        if not 0 < self.min_retained_fraction <= 1:
            raise ValueError("retained fraction must be in (0,1]")


@dataclass(frozen=True, slots=True)
class ReleasedSample:
    asset: SourceAsset
    revision: AnnotationRevision
    group_id: str
    split: Split


@dataclass(frozen=True, slots=True)
class CanonicalDatasetRelease:
    release_id: str
    project_id: str
    ontology: OntologyRelease
    assignment: SourceSplitAssignment
    samples: tuple[ReleasedSample, ...]
    excluded_asset_ids: dict[str, str]
    frozen: bool = True

    def __post_init__(self) -> None:
        if not self.frozen or not self.release_id:
            raise ValueError("compiled releases must be frozen and explicitly identified")
        if self.ontology.project_id != self.project_id or not self.assignment.locked:
            raise ValueError("release ontology and source assignment must be locked to the project")
        if not any(sample.split == "val" for sample in self.samples):
            raise ValueError("a canonical release requires non-empty validation membership")
        seen: set[str] = set()
        allowed_classes = set(self.ontology.class_map)
        for sample in self.samples:
            if sample.asset.asset_id in seen:
                raise ValueError("exactly one canonical revision per source is allowed")
            seen.add(sample.asset.asset_id)
            if sample.asset.project_id != self.project_id:
                raise ValueError("source project must match release project")
            if self.assignment.split_for(sample.asset.asset_id) != sample.split:
                raise ValueError("split must come from locked source-group assignment")
            if self.assignment.group_ids[sample.asset.asset_id] != sample.group_id:
                raise ValueError("release group must match assignment grouping")
            if not sample.revision.training_eligible:
                raise ValueError("unresolved samples must be explicit exclusions")
            for obj in sample.revision.objects:
                if obj.class_id not in allowed_classes:
                    raise ValueError("annotation class is absent from release ontology")
                obj.box.within(sample.asset.width, sample.asset.height)
        if seen & set(self.excluded_asset_ids):
            raise ValueError("an asset cannot be both selected and excluded")

    def ontology_hash(self) -> str:
        encoded = json.dumps(self.ontology.mapping(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()
