"""Ports for the existing authenticated transaction and fenced-queue owners.

Implementations must authorize resource ownership inside the transaction, lock the
aggregate, reconcile idempotency before checking its revision, and commit the
state, attributed append-only event and queue intent together. A replay returns
the original result without another event/job. The same key with a different
fingerprint is a conflict. These interfaces do not implement persistence or a
scheduler, and fixture implementations are not integration evidence.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from .management_contracts import (
    AssetIngest,
    ClassCatalogDraft,
    Contract,
    EvaluationStart,
    ExportRead,
    GuideDraft,
    JobAccepted,
    ManagementError,
    ProjectCreate,
    PromotionRequest,
    PromotionResult,
    ReferenceAccept,
    ReferenceEnroll,
    ReleaseFreeze,
    Resource,
    ResourceKind,
    ResourcePage,
    ScopeDraft,
    SourcePolicyDraft,
    TrainingStart,
    VariantCompile,
)
from .management_details import (
    EvaluationDetail,
    FreezePlanRead,
    ProjectDetail,
    RecipePreviewRead,
    RegistryDetail,
    ReleaseDetail,
    TrainingDetail,
    TrainingProfilesRead,
)


class ManagementPrincipal(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def role(self) -> str: ...

    def permits(self, permission: str) -> bool: ...


@dataclass(frozen=True)
class MutationContext:
    actor_id: str
    idempotency_key: str
    operation: str
    target_id: str
    fingerprint: str

    @classmethod
    def bind(
        cls, actor: ManagementPrincipal, key: str, operation: str, target: str, payload: Contract
    ) -> "MutationContext":
        # Include the authenticated actor and route target, not just the body.
        parts = [actor.id, operation, target, payload.model_dump(mode="json")]
        fingerprint = hashlib.sha256(
            json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return cls(actor.id, key, operation, target, fingerprint)


class ManagementFailure(Exception):
    def __init__(self, code: ManagementError):
        self.error = code
        super().__init__(code.detail)


class ManagementOperations(Protocol):
    def create_project(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ProjectCreate
    ) -> Resource: ...

    def save_scope_draft(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ScopeDraft
    ) -> Resource: ...

    def save_guide_draft(
        self, actor: ManagementPrincipal, context: MutationContext, payload: GuideDraft
    ) -> Resource: ...

    def save_class_catalog_draft(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ClassCatalogDraft
    ) -> Resource: ...

    def save_source_policy_draft(
        self, actor: ManagementPrincipal, context: MutationContext, payload: SourcePolicyDraft
    ) -> Resource: ...

    def enroll_reference(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ReferenceEnroll
    ) -> Resource: ...

    def accept_reference(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ReferenceAccept
    ) -> Resource: ...

    def ingest_asset(
        self, actor: ManagementPrincipal, context: MutationContext, payload: AssetIngest
    ) -> Resource: ...

    def freeze_release(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ReleaseFreeze
    ) -> Resource: ...

    def compile_variant(
        self, actor: ManagementPrincipal, context: MutationContext, payload: VariantCompile
    ) -> JobAccepted: ...

    def start_training(
        self, actor: ManagementPrincipal, context: MutationContext, payload: TrainingStart
    ) -> JobAccepted: ...

    def start_evaluation(
        self, actor: ManagementPrincipal, context: MutationContext, payload: EvaluationStart
    ) -> JobAccepted: ...

    def promote(
        self, actor: ManagementPrincipal, context: MutationContext, payload: PromotionRequest
    ) -> PromotionResult: ...

    def list_projects(
        self, actor: ManagementPrincipal, cursor: str | None, limit: int
    ) -> ResourcePage: ...

    def list_resources(
        self,
        actor: ManagementPrincipal,
        project_id: str,
        kind: ResourceKind,
        cursor: str | None,
        limit: int,
    ) -> ResourcePage: ...

    def read(
        self, actor: ManagementPrincipal, kind: ResourceKind, resource_id: str
    ) -> Resource: ...

    def read_export(self, actor: ManagementPrincipal, model_id: str) -> ExportRead: ...

    def read_project(self, actor: ManagementPrincipal, project_id: str) -> ProjectDetail: ...

    def read_release(self, actor: ManagementPrincipal, release_id: str) -> ReleaseDetail: ...

    def read_freeze_plan(self, actor: ManagementPrincipal, project_id: str) -> FreezePlanRead: ...

    def read_recipe_preview(
        self, actor: ManagementPrincipal, release_id: str, recipe_id: str, asset_id: str
    ) -> RecipePreviewRead: ...

    def read_training(self, actor: ManagementPrincipal, run_id: str) -> TrainingDetail: ...

    def list_training_profiles(
        self, actor: ManagementPrincipal, project_id: str
    ) -> TrainingProfilesRead: ...

    def read_evaluation(
        self, actor: ManagementPrincipal, evaluation_id: str
    ) -> EvaluationDetail: ...

    def read_registry(
        self,
        actor: ManagementPrincipal,
        project_id: str,
        recipe_id: str,
        cursor: str | None,
        limit: int,
    ) -> RegistryDetail: ...
