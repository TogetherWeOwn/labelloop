"""Authorized read projections, not a second domain model or mutable store.

Adapters load these from the canonical stores after authorizing the project.
Counts, checks, transforms and provenance are server facts, not browser claims.
Do not populate any field from raw provider responses, secret-bearing URIs or
worker-local paths. Missing evidence stays null; it is never a passing gate.
"""

from typing import Annotated, Literal

from pydantic import Field

from labelloop_core.evaluation import EvaluationReport
from labelloop_core.geometry import AffineTransform, Box
from labelloop_core.registry import ModelRelease
from labelloop_core.training import Checkpoint

from .management_contracts import (
    ClassDraftInput,
    ClassInput,
    Contract,
    Digest,
    Identifier,
    RecipeId,
    ReleaseFreeze,
    Resource,
    Revision,
)

Count = Annotated[int, Field(ge=0)]
Text = Annotated[str, Field(max_length=16384)]
SetupStep = Literal["scope", "classes", "evidence", "guide", "sources", "publish"]
Scalar = str | int | float | bool


class ClassRead(ClassInput):
    aliases: list[Text]
    parent_id: Identifier | None
    positive_examples: Count | None
    exclusions: list[Text] | None
    mirror_safe: bool | None
    approval: Literal["proposed", "admin_approved"] | None


class ReferenceRead(Contract):
    id: Identifier
    asset_id: Identifier
    class_id: Identifier
    source: Text
    permitted_use: Text
    view: Text
    lighting: Text
    scale: Text
    confused_with: list[Identifier]
    validity_scope: Text
    approval: Literal["proposed", "admin_approved", "withdrawn"]


class ReferenceLifecycleRead(Contract):
    id: Identifier
    asset_id: Identifier
    class_id: Identifier
    state: Literal["proposed", "accepted", "withdrawn"]
    # Historical authority pins for explicit recovery, never current eligibility.
    ontology_id: Identifier
    assignment_version: Identifier
    last_approval_revision: Annotated[int, Field(ge=1)] | None
    eligibility: Literal["not_assessed"]


class ReferenceLifecycleList(Contract):
    project_id: Identifier
    project_revision: Revision
    references: Annotated[list[ReferenceLifecycleRead], Field(max_length=1000)]


class GuideRead(Contract):
    extent: Literal["visible", "estimated"]
    truncation: Text
    occlusion: Text
    reflections: Text
    min_target_px: Annotated[float, Field(gt=0)]
    exhaustive_for_scope: bool
    background: Text


class SourcePairRead(Contract):
    id: Identifier
    left_asset_id: Identifier
    right_asset_id: Identifier


class SourceGroupsRead(Contract):
    images: Count
    groups: Count | None
    metadata_keys: list[Identifier] | None
    pending_pairs: list[SourcePairRead]
    split_counts: dict[Literal["train", "val", "test"], Count] | None
    assignment_version: Identifier | None


class ClassCatalogRead(Contract):
    base_ontology_id: Identifier
    classes: list[ClassDraftInput]


class SourcePolicyRead(Contract):
    metadata_keys: list[Literal["session", "sequence", "subject"]]
    collection_notes: Text


class ProjectDetail(Contract):
    resource: Resource
    name: Text | None
    scope: Text
    domain: Text
    representation: Text | None
    intended_use: Text | None
    specificity: Text | None
    source_permitted_use: Text
    external_provider_ids: list[Identifier]
    classes: list[ClassRead]
    # Separate from authoritative ontology classes until publication/migration.
    class_catalog_draft: ClassCatalogRead | None = None
    references: list[ReferenceRead] | None
    # The bootstrap pack stores a free-text guide, not the structured policy.
    annotation_guide: Text
    guide: GuideRead | None
    sources: SourceGroupsRead
    # Configuration intent is separate from observed grouping/split facts.
    source_policy_draft: SourcePolicyRead | None = None
    setup_draft_sha256: dict[Literal["scope", "guide", "classes", "sources"], Digest] = Field(
        default_factory=dict
    )
    approved_steps: list[SetupStep] | None
    versions: dict[Identifier, Identifier]
    published_release_id: Identifier | None
    saved_at: Text


class ReleaseMemberRead(Contract):
    asset_id: Identifier
    annotation_revision: Annotated[int, Field(ge=1)]
    source_sha256: Digest
    split: Literal["train", "val", "test"]
    objects: Count
    review_tier: Literal["machine_verified_no_human_review", "human_reviewed", "adjudicated"]
    scope_completeness: Literal["exhaustive_for_scope"]


class ReleaseClassRead(Contract):
    class_id: Identifier
    train: Count
    val: Count
    test: Count
    delta: int
    minimum_support: Count | None


class ExclusionRead(Contract):
    reason: Text
    asset_ids: list[Identifier]


class CheckRead(Contract):
    code: Identifier
    passed: bool | None
    reason: Text


class ReleaseDetail(Contract):
    resource: Resource
    previous_release_id: Identifier | None
    ontology_id: Identifier
    assignment_version: Identifier
    members: list[ReleaseMemberRead]
    classes: list[ReleaseClassRead]
    exclusions: list[ExclusionRead]
    checks: list[CheckRead]
    changed_asset_ids: list[Identifier]
    auto_accepted_objects: Count
    human_reviewed_objects: Count
    acceptance_policy_id: Identifier
    quarantined_reason: Text | None
    manifest_artifact_id: Identifier | None
    manifest_sha256: Digest | None
    frozen_at: Text | None
    frozen_by: Identifier | None


class FreezePlanRead(Contract):
    project_id: Identifier
    # Selected from authoritative eligible revisions at this project revision.
    # A POST must recheck these under lock, not trust this read as approval.
    payload: ReleaseFreeze | None
    checks: list[CheckRead]
    exclusions: list[ExclusionRead]


class RecipeBoxRead(Contract):
    object_id: Identifier
    class_id: Identifier
    box: Box
    review_tier: Literal["machine_verified_no_human_review", "human_reviewed", "adjudicated"]
    clipped: bool
    consequence: Text
    flag: Literal["none", "warn", "exclude"]


class RecipePreviewRead(Contract):
    evidence_state: Literal["planned", "compiled"] = "compiled"
    recipe_id: RecipeId
    recipe_revision: Revision
    source_asset_id: Identifier
    source_width: Annotated[int, Field(gt=0)]
    source_height: Annotated[int, Field(gt=0)]
    transform: AffineTransform
    boxes: list[RecipeBoxRead]
    tiny_targets: Count
    below_minimum_targets: Count
    clipped_targets: Count
    excluded_images: Count
    checks: list[CheckRead]
    manifest_artifact_id: Identifier | None
    validation_artifact_id: Identifier | None


class TrainingProfileRead(Contract):
    id: Identifier
    revision: Revision
    recipe_id: RecipeId
    backend: Literal["local_ultralytics"]
    initialization_sha256: Digest
    epochs: Annotated[int, Field(ge=1)]
    # Only approved baseline arguments, no arbitrary provider metadata/paths.
    arguments: dict[Identifier, Scalar]


class TrainingProfilesRead(Contract):
    project_id: Identifier
    profiles: list[TrainingProfileRead]


class RetentionRead(Contract):
    before: Count
    after: Count
    dropped_object_ids: list[Identifier]


class ArgumentMismatchRead(Contract):
    key: Identifier
    requested: Scalar | None
    resolved: Scalar | None


class TrainingDetail(Contract):
    resource: Resource
    recipe_id: RecipeId
    dataset_release_id: Identifier
    variant_id: Identifier
    training_profile_id: Identifier
    training_profile_revision: Revision
    initialization_sha256: Digest
    epoch: Count
    epochs: Annotated[int, Field(ge=1)]
    images: Count
    reason: Text | None
    updated_at: Text
    requested_arguments: dict[Identifier, Scalar]
    resolved_arguments: dict[Identifier, Scalar] | None
    checkpoints: list[Checkpoint]
    retention: RetentionRead | None
    mismatches: list[ArgumentMismatchRead]


class EvaluationDetail(Contract):
    resource: Resource
    candidate_id: Identifier
    incumbent_id: Identifier | None
    # Actual source-frame evaluator output; never native val as qualification.
    report: EvaluationReport | None
    incumbent_report: EvaluationReport | None
    checks: list[CheckRead]
    qualification_id: Identifier | None
    qualification: Literal["unqualified", "evaluating", "qualified"]
    reason: Text
    minimum_class_support: Count | None
    opened_final_test_count: Count


class RegistryHistoryRead(Contract):
    event_id: Identifier
    occurred_at: Text
    principal_id: Identifier
    action: Literal["promoted", "retained", "rolled_back"]
    prior_release_id: Identifier | None
    release_id: Identifier | None
    reason: Text


class RegistryDetail(Contract):
    project_id: Identifier
    recipe_id: RecipeId
    revision: Revision
    # Reuse the complete atomic core release, not a browser reconstruction.
    champion: ModelRelease | None
    candidate: ModelRelease | None
    rollback: ModelRelease | None
    qualification_id: Identifier | None
    qualification: Literal["unqualified", "evaluating", "qualified"]
    checks: list[CheckRead]
    reason: Text
    history: list[RegistryHistoryRead]
    next_history_cursor: Identifier | None
