"""Untrusted management inputs and public, secret-free read models."""

from typing import Annotated, Literal, Self

from pydantic import Base64Bytes, BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")]
Revision = Annotated[int, Field(ge=0)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RecipeId = Literal["full-letterbox-320-v1", "full-letterbox-640-v1"]
ResourceKind = Literal[
    "project", "asset", "dataset-release", "variant", "training-run", "model", "evaluation", "job"
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


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


class AssetIngest(Mutation):
    # Uploaded bytes only: neither a remote URL fetch nor a client storage key.
    original_base64: Annotated[Base64Bytes, Field(min_length=1, max_length=12_582_912)]
    source_uri: Annotated[str, Field(max_length=2048)] = ""
    source_group: Identifier


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
