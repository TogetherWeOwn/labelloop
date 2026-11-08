"""Untrusted management inputs and public, secret-free read models."""

import base64
import binascii
from typing import Annotated, Literal, Self

from pydantic import Base64Bytes, BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")]
Revision = Annotated[int, Field(ge=0)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RecipeId = Literal["full-letterbox-320-v1", "full-letterbox-640-v1"]
ResourceKind = Literal[
    "project", "asset", "dataset-release", "variant", "training-run", "model", "evaluation", "job"
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class ClassInput(Contract):
    stable_id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=256)]
    definition: Annotated[str, Field(min_length=1, max_length=4096)]


class ProjectCreate(Contract):
    project_key: Identifier
    task: Literal["detect"] = "detect"
    scope: Annotated[str, Field(min_length=1, max_length=8192)]
    domain: Annotated[str, Field(min_length=1, max_length=256)]
    annotation_guide: Annotated[str, Field(min_length=1, max_length=16384)]
    source_permitted_use: Annotated[str, Field(min_length=1, max_length=4096)]
    classes: Annotated[list[ClassInput], Field(min_length=1, max_length=1000)]
    expected_revision: Literal[0] = 0

    @model_validator(mode="after")
    def unique_classes(self) -> Self:
        identifiers = [item.stable_id for item in self.classes]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("class IDs must be unique")
        return self


class Mutation(Contract):
    expected_revision: Revision


class ScopeDraft(Mutation):
    name: Annotated[str, Field(min_length=1, max_length=256)]
    scope: Annotated[str, Field(min_length=1, max_length=8192)]
    domain: Annotated[str, Field(min_length=1, max_length=256)]
    representation: Literal["scene_object", "depiction", "interface_element"]
    intended_use: Annotated[str, Field(min_length=1, max_length=4096)]
    specificity: Annotated[str, Field(min_length=1, max_length=4096)]
    source_permitted_use: Annotated[str, Field(min_length=1, max_length=4096)]


class GuideDraft(Mutation):
    annotation_guide: Annotated[str, Field(min_length=1, max_length=16384)]
    # Estimated hidden extent is not an implemented labeling policy in v1.
    extent: Literal["visible"]
    truncation: Annotated[str, Field(min_length=1, max_length=16384)]
    occlusion: Annotated[str, Field(min_length=1, max_length=16384)]
    reflections: Annotated[str, Field(min_length=1, max_length=16384)]
    min_target_px: Annotated[float, Field(gt=0)]
    exhaustive_for_scope: bool
    background: Annotated[str, Field(min_length=1, max_length=16384)]


class ClassDraftInput(ClassInput):
    aliases: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=256)]], Field(max_length=64)
    ]
    parent_id: Identifier | None
    # Prose examples are draft intent, never approved reference assets.
    positive_examples: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=4096)]], Field(max_length=64)
    ]
    exclusions: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=4096)]], Field(max_length=64)
    ]
    mirror_safe: bool

    @model_validator(mode="after")
    def unique_aliases(self) -> Self:
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError("class aliases must be unique")
        return self


class ClassCatalogDraft(Mutation):
    base_ontology_id: Identifier
    classes: Annotated[list[ClassDraftInput], Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def valid_hierarchy(self) -> Self:
        parents = {item.stable_id: item.parent_id for item in self.classes}
        if len(parents) != len(self.classes):
            raise ValueError("class IDs must be unique")
        complete: set[str] = set()
        for identifier in parents:
            path: set[str] = set()
            current: str | None = identifier
            while current is not None and current not in complete:
                if current not in parents or current in path:
                    raise ValueError("class parents must form an internal acyclic hierarchy")
                path.add(current)
                current = parents[current]
            complete.update(path)
        return self


class ClassCatalogPublish(Mutation):
    base_ontology_id: Identifier
    draft_sha256: Digest
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class SourcePolicyDraft(Mutation):
    metadata_keys: Annotated[
        list[Literal["session", "sequence", "subject"]], Field(min_length=1, max_length=3)
    ]
    collection_notes: Annotated[str, Field(min_length=1, max_length=8192)]

    @model_validator(mode="after")
    def unique_metadata_keys(self) -> Self:
        if len(set(self.metadata_keys)) != len(self.metadata_keys):
            raise ValueError("source metadata keys must be unique")
        return self


class SetupStepApprove(Mutation):
    # Classes require a published catalog; none of these steps qualify a pilot.
    step: Literal["scope", "guide", "classes"]
    draft_sha256: Digest
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class ReferenceEnroll(Mutation):
    base_ontology_id: Identifier
    assignment_version: Identifier
    asset_id: Identifier
    class_id: Identifier
    source: Annotated[str, Field(min_length=1, max_length=4096)]
    permitted_use: Annotated[str, Field(min_length=1, max_length=4096)]
    view: Annotated[str, Field(min_length=1, max_length=4096)]
    lighting: Annotated[str, Field(min_length=1, max_length=4096)]
    scale: Annotated[str, Field(min_length=1, max_length=4096)]
    validity_scope: Annotated[str, Field(min_length=1, max_length=4096)]
    confused_with: Annotated[list[Identifier], Field(max_length=64)]

    @model_validator(mode="after")
    def distinct_confusion_classes(self) -> Self:
        if (
            len(set(self.confused_with)) != len(self.confused_with)
            or self.class_id in self.confused_with
        ):
            raise ValueError("confusion classes must be unique and distinct from the target")
        return self


class ReferenceAccept(Mutation):
    reference_id: Identifier
    base_ontology_id: Identifier
    assignment_version: Identifier
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class ReferenceRevalidate(ReferenceAccept):
    # A renewal names the latest attributed approval, never replacement metadata.
    previous_approval_revision: Annotated[int, Field(ge=1)]


class ReferenceWithdraw(Mutation):
    reference_id: Identifier
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class AssetIngest(Mutation):
    # Uploaded bytes only: neither a remote URL fetch nor a client storage key.
    original_base64: Annotated[
        Base64Bytes, Field(strict=False, min_length=1, max_length=12_582_912)
    ]
    source_uri: Annotated[str, Field(max_length=2048)] = ""
    source_group: Identifier

    @field_validator("original_base64", mode="before")
    @classmethod
    def validate_base64_text(cls, value: object) -> str:
        # JSON carries text, while the port receives decoded bytes. Relax only
        # that conversion, not strict revisions or the base64 alphabet/bounds.
        if not isinstance(value, str) or not 1 <= len(value) <= 16_777_216:
            raise ValueError("invalid original encoding")
        try:
            base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("invalid original encoding") from None
        return value


class ReleaseFreeze(Mutation):
    ontology_id: Identifier
    assignment_version: Identifier
    annotation_revisions: Annotated[
        dict[Identifier, Annotated[int, Field(ge=1)]], Field(min_length=1, max_length=10000)
    ]


class VariantCompile(Mutation):
    recipe_id: RecipeId


class TrainingStart(Mutation):
    # The approved server profile resolves checkpoint, shape and arguments.
    # Cloud training and paid authorization belong to the separate budgeted port.
    backend: Literal["local_ultralytics"] = "local_ultralytics"
    training_profile_id: Identifier


class EvaluationStart(Mutation):
    dataset_release_id: Identifier
    protocol_id: Identifier


class PromotionRequest(Mutation):
    recipe_id: RecipeId
    candidate_id: Identifier
    qualification_id: Identifier
    expected_incumbent_id: Identifier | None
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class Resource(Contract):
    id: Identifier
    project_id: Identifier
    kind: ResourceKind
    revision: Revision
    state: Literal["draft", "frozen", "queued", "running", "completed", "failed", "cancelled"]


class ResourcePage(Contract):
    items: list[Resource]
    next_cursor: Identifier | None = None


class JobAccepted(Contract):
    job_id: Identifier
    resource: Resource


class PromotionResult(Contract):
    project_id: Identifier
    recipe_id: RecipeId
    revision: Revision
    incumbent_id: Identifier | None
    decision: Literal["promoted", "retained"]
    gate_codes: list[Identifier]


class ExportRead(Contract):
    model_id: Identifier
    recipe_id: RecipeId
    weights_sha256: Digest
    artifact_sha256: Digest
    format: Literal["onnx"]
    input_hw: tuple[int, int]
    # An application artifact ID, not an expiring credential-bearing URL.
    artifact_id: Identifier


class ManagementError(Contract):
    detail: Literal[
        "authentication_required",
        "permission_denied",
        "invalid_request",
        "management_unavailable",
        "not_found",
        "revision_conflict",
        "idempotency_conflict",
        "incomplete_labels",
        "unsupported_classes",
        "missing_evidence",
        "incompatible_recipe",
        "shape_mismatch",
        "qualification_required",
        "external_conflict",
        "transport_failure",
    ]
