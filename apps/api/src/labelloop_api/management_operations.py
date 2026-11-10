"""Opt-in canonical facade; no default mount, admission or registry authority.

Trusted server configuration supplies one engine, object store and workspace.
Existing transaction adapters retain authorization, revisions and queue ownership.
"""

from dataclasses import dataclass

from labelloop_worker_core.jobs import JobStore

from .management_canonical import CanonicalManagementMutations, refuse
from .management_compilation import CanonicalManagementCompilation
from .management_compile_worker import CompilationObjects
from .management_contracts import (
    AssetIngest,
    EvaluationStart,
    ExportRead,
    InitialSourceGrouping,
    JobAccepted,
    PromotionRequest,
    PromotionResult,
    Resource,
    ResourceKind,
    ResourcePage,
    TrainingStart,
    VariantCompile,
)
from .management_details import (
    EvaluationDetail,
    FreezePlanRead,
    ProjectDetail,
    RecipePreviewRead,
    ReferenceLifecycleList,
    RegistryDetail,
    ReleaseDetail,
    TrainingDetail,
    TrainingProfilesRead,
)
from .management_evaluation import ApprovedDevelopmentProtocol, CanonicalManagementEvaluation
from .management_grouping_publication import CanonicalInitialGrouping
from .management_ports import ManagementPrincipal, MutationContext
from .management_reads import CanonicalManagementReads
from .management_training import ApprovedTrainingProfile, CanonicalManagementTraining


@dataclass(frozen=True)
class CanonicalManagementOperations(CanonicalManagementMutations):
    """Compose delivered adapters without granting unavailable producer authority.

    Setup/create/freeze reuse the canonical mutation methods unchanged. Async
    submission always uses the native caller-owned queue transaction, never a
    request-selected callback. Construction performs no database or object I/O.
    """

    profiles: tuple[ApprovedTrainingProfile, ...] = ()
    protocols: tuple[ApprovedDevelopmentProtocol, ...] = ()
    grouping_objects: CompilationObjects | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        # Validate bounded immutable server configuration before serving requests.
        self._training()
        self._evaluation()
        if self.grouping_objects is not None and not callable(
            getattr(self.grouping_objects, "read", None)
        ):
            raise ValueError("grouping requires an explicit trusted object reader")

    def _reads(self) -> CanonicalManagementReads:
        return CanonicalManagementReads(self.engine, self.workspace_id)

    def _training(self) -> CanonicalManagementTraining:
        return CanonicalManagementTraining(self, JobStore.enqueue_in_transaction, self.profiles)

    def _evaluation(self) -> CanonicalManagementEvaluation:
        return CanonicalManagementEvaluation(self, JobStore.enqueue_in_transaction, self.protocols)

    def ingest_asset(
        self, actor: ManagementPrincipal, context: MutationContext, payload: AssetIngest
    ) -> Resource:
        self.authorize(actor, context, "ingest_asset", context.target_id, payload)
        # Canonical ingest remains independently testable, not admitted here.
        refuse("management_unavailable")

    def publish_initial_grouping(
        self, actor: ManagementPrincipal, context: MutationContext, payload: InitialSourceGrouping
    ) -> Resource:
        self.authorize(actor, context, "publish_initial_grouping", context.target_id, payload)
        if self.grouping_objects is None:
            refuse("management_unavailable")
        return CanonicalInitialGrouping(self, self.grouping_objects).publish(
            actor, context, payload
        )

    def compile_variant(
        self, actor: ManagementPrincipal, context: MutationContext, payload: VariantCompile
    ) -> JobAccepted:
        return CanonicalManagementCompilation(
            self, JobStore.enqueue_in_transaction
        ).compile_variant(actor, context, payload)

    def start_training(
        self, actor: ManagementPrincipal, context: MutationContext, payload: TrainingStart
    ) -> JobAccepted:
        return self._training().start_training(actor, context, payload)

    def start_evaluation(
        self, actor: ManagementPrincipal, context: MutationContext, payload: EvaluationStart
    ) -> JobAccepted:
        return self._evaluation().start_evaluation(actor, context, payload)

    def promote(
        self, actor: ManagementPrincipal, context: MutationContext, payload: PromotionRequest
    ) -> PromotionResult:
        self.authorize(actor, context, "promote", context.target_id, payload)
        # Consume the single qualified registry service on delivery, never infer
        # qualification from development reports or accept client gate claims.
        refuse("management_unavailable")

    def list_projects(
        self, actor: ManagementPrincipal, cursor: str | None, limit: int
    ) -> ResourcePage:
        return self._reads().list_projects(actor, cursor, limit)

    def list_resources(
        self,
        actor: ManagementPrincipal,
        project_id: str,
        kind: ResourceKind,
        cursor: str | None,
        limit: int,
    ) -> ResourcePage:
        return self._reads().list_resources(actor, project_id, kind, cursor, limit)

    def read(self, actor: ManagementPrincipal, kind: ResourceKind, resource_id: str) -> Resource:
        return self._reads().read(actor, kind, resource_id)

    def read_export(self, actor: ManagementPrincipal, model_id: str) -> ExportRead:
        return self._reads().read_export(actor, model_id)

    def read_project(self, actor: ManagementPrincipal, project_id: str) -> ProjectDetail:
        return self._reads().read_project(actor, project_id)

    def read_reference_lifecycle(
        self, actor: ManagementPrincipal, project_id: str
    ) -> ReferenceLifecycleList:
        return self._reads().read_reference_lifecycle(actor, project_id)

    def read_release(self, actor: ManagementPrincipal, release_id: str) -> ReleaseDetail:
        return self._reads().read_release(actor, release_id)

    def read_freeze_plan(self, actor: ManagementPrincipal, project_id: str) -> FreezePlanRead:
        return self._reads().read_freeze_plan(actor, project_id)

    def read_recipe_preview(
        self, actor: ManagementPrincipal, release_id: str, recipe_id: str, asset_id: str
    ) -> RecipePreviewRead:
        return self._reads().read_recipe_preview(actor, release_id, recipe_id, asset_id)

    def read_training(self, actor: ManagementPrincipal, run_id: str) -> TrainingDetail:
        return self._reads().read_training(actor, run_id)

    def list_training_profiles(
        self, actor: ManagementPrincipal, project_id: str
    ) -> TrainingProfilesRead:
        return self._training().list_training_profiles(actor, project_id)

    def read_evaluation(self, actor: ManagementPrincipal, evaluation_id: str) -> EvaluationDetail:
        return self._reads().read_evaluation(actor, evaluation_id)

    def read_registry(
        self,
        actor: ManagementPrincipal,
        project_id: str,
        recipe_id: str,
        cursor: str | None,
        limit: int,
    ) -> RegistryDetail:
        self._reads().read(actor, "project", project_id)
        refuse("management_unavailable")
