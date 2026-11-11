"""Management route factory; mount only behind the shared session/CSRF boundary."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse

from .management_contracts import (
    AnchorPackPublish,
    AssetIngest,
    ClassCatalogDraft,
    ClassCatalogPublish,
    ConfigurationSnapshotPublish,
    ConfigurationSnapshotRead,
    EvaluationStart,
    ExportRead,
    GuideDraft,
    Identifier,
    InitialSourceGrouping,
    JobAccepted,
    LocalConfigurationPublish,
    ManagementError,
    ProjectCreate,
    PromotionRequest,
    PromotionResult,
    RecipeId,
    ReferenceAccept,
    ReferenceEnroll,
    ReferenceRevalidate,
    ReferenceWithdraw,
    ReleaseFreeze,
    Resource,
    ResourceKind,
    ResourcePage,
    ScopeDraft,
    SetupStepApprove,
    SourcePairResolve,
    SourcePolicyDraft,
    SourceRegrouping,
    TrainingStart,
    VariantCompile,
)
from .management_details import (
    AnchorPackRead,
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
from .management_ports import (
    ManagementFailure,
    ManagementOperations,
    ManagementPrincipal,
    MutationContext,
)

IdempotencyKey = Annotated[str, Header(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
PageLimit = Annotated[int, Query(ge=1, le=100)]


def unavailable_principal(request: Request) -> ManagementPrincipal:
    raise HTTPException(503, detail="management_unavailable")


def unavailable_operations() -> ManagementOperations:
    raise HTTPException(503, detail="management_unavailable")


def invoke[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except ManagementFailure as error:
        status = {
            "authentication_required": 401,
            "permission_denied": 403,
            "not_found": 404,
            "revision_conflict": 409,
            "idempotency_conflict": 409,
            "external_conflict": 409,
            "management_unavailable": 503,
            "transport_failure": 503,
        }.get(error.error.detail, 422)
        raise HTTPException(status, detail=error.error.detail) from None


class ManagementRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_response(request: Request) -> Response:
            try:
                response = await handler(request)
            except RequestValidationError:
                # Never reflect uploaded pixels or rejected privileged fields.
                response = JSONResponse({"detail": "invalid_request"}, status_code=422)
            except HTTPException as error:
                response = JSONResponse({"detail": error.detail}, status_code=error.status_code)
            response.headers["Cache-Control"] = "no-store"
            return response

        return safe_response


def management_router(
    principal: Callable[[Request], ManagementPrincipal] = unavailable_principal,
    operations: Callable[[], ManagementOperations] = unavailable_operations,
) -> APIRouter:
    """No headers/body may supply a principal; a trusted auth dependency is required.

    Resource/project authorization and atomic mutation semantics belong to the
    real adapter, which must not accept an actor merely because it has an ID.
    """

    router = APIRouter(
        tags=["management"],
        route_class=ManagementRoute,
        responses={code: {"model": ManagementError} for code in (401, 403, 404, 409, 422, 503)},
    )

    def reader(request: Request) -> ManagementPrincipal:
        actor = principal(request)
        if not actor.id or actor.kind != "human" or actor.role not in {"admin", "reviewer"}:
            raise HTTPException(403, detail="permission_denied")
        if not actor.permits("review"):
            raise HTTPException(403, detail="permission_denied")
        return actor

    def admin(request: Request) -> ManagementPrincipal:
        actor = reader(request)
        if actor.role != "admin" or not actor.permits("admin"):
            raise HTTPException(403, detail="permission_denied")
        return actor

    @router.post(
        "/projects", response_model=Resource, status_code=201, operation_id="createProject"
    )
    def create_project(
        payload: ProjectCreate,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "create_project", "projects", payload
        )
        return invoke(lambda: service.create_project(actor, context, payload))

    @router.post(
        "/projects/{project_id}/setup/scope",
        response_model=Resource,
        operation_id="saveScopeDraft",
    )
    def save_scope_draft(
        project_id: Identifier,
        payload: ScopeDraft,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "save_scope_draft", project_id, payload
        )
        return invoke(lambda: service.save_scope_draft(actor, context, payload))

    @router.post(
        "/projects/{project_id}/setup/guide",
        response_model=Resource,
        operation_id="saveGuideDraft",
    )
    def save_guide_draft(
        project_id: Identifier,
        payload: GuideDraft,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "save_guide_draft", project_id, payload
        )
        return invoke(lambda: service.save_guide_draft(actor, context, payload))

    @router.post(
        "/projects/{project_id}/setup/classes",
        response_model=Resource,
        operation_id="saveClassCatalogDraft",
    )
    def save_class_catalog_draft(
        project_id: Identifier,
        payload: ClassCatalogDraft,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "save_class_catalog_draft", project_id, payload
        )
        return invoke(lambda: service.save_class_catalog_draft(actor, context, payload))

    @router.post(
        "/projects/{project_id}/setup/classes/publish",
        response_model=Resource,
        operation_id="publishClassCatalog",
    )
    def publish_class_catalog(
        project_id: Identifier,
        payload: ClassCatalogPublish,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "publish_class_catalog", project_id, payload
        )
        return invoke(lambda: service.publish_class_catalog(actor, context, payload))

    @router.post(
        "/projects/{project_id}/source-assignments/initial",
        response_model=Resource,
        operation_id="publishInitialSourceGrouping",
    )
    def publish_initial_grouping(
        project_id: Identifier,
        payload: InitialSourceGrouping,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "publish_initial_grouping", project_id, payload
        )
        return invoke(lambda: service.publish_initial_grouping(actor, context, payload))

    @router.post(
        "/projects/{project_id}/source-assignments/regroup",
        response_model=Resource,
        operation_id="publishSourceRegrouping",
    )
    def publish_source_regrouping(
        project_id: Identifier,
        payload: SourceRegrouping,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "publish_source_regrouping", project_id, payload
        )
        return invoke(lambda: service.publish_source_regrouping(actor, context, payload))

    @router.post(
        "/projects/{project_id}/source-pairs/discover",
        response_model=Resource,
        operation_id="discoverInitialSourcePairs",
    )
    def discover_initial_source_pairs(
        project_id: Identifier,
        payload: InitialSourceGrouping,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "discover_initial_source_pairs", project_id, payload
        )
        return invoke(lambda: service.discover_initial_source_pairs(actor, context, payload))

    @router.post(
        "/projects/{project_id}/source-pairs/resolve",
        response_model=Resource,
        operation_id="resolveSourcePair",
    )
    def resolve_source_pair(
        project_id: Identifier,
        payload: SourcePairResolve,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "resolve_source_pair", project_id, payload
        )
        return invoke(lambda: service.resolve_source_pair(actor, context, payload))

    @router.post(
        "/projects/{project_id}/setup/sources",
        response_model=Resource,
        operation_id="saveSourcePolicyDraft",
    )
    def save_source_policy_draft(
        project_id: Identifier,
        payload: SourcePolicyDraft,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "save_source_policy_draft", project_id, payload
        )
        return invoke(lambda: service.save_source_policy_draft(actor, context, payload))

    @router.post(
        "/projects/{project_id}/setup/approvals",
        response_model=Resource,
        operation_id="approveSetupStep",
    )
    def approve_setup_step(
        project_id: Identifier,
        payload: SetupStepApprove,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "approve_setup_step", project_id, payload
        )
        return invoke(lambda: service.approve_setup_step(actor, context, payload))

    @router.post(
        "/projects/{project_id}/reference-packs",
        response_model=Resource,
        operation_id="publishAnchorPack",
    )
    def publish_anchor_pack(
        project_id: Identifier,
        payload: AnchorPackPublish,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "publish_anchor_pack", project_id, payload
        )
        return invoke(lambda: service.publish_anchor_pack(actor, context, payload))

    @router.post(
        "/projects/{project_id}/configuration-snapshots",
        response_model=Resource,
        operation_id="publishConfigurationSnapshot",
    )
    def publish_configuration_snapshot(
        project_id: Identifier,
        payload: ConfigurationSnapshotPublish,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "publish_configuration_snapshot", project_id, payload
        )
        return invoke(lambda: service.publish_configuration_snapshot(actor, context, payload))

    @router.get(
        "/projects/{project_id}/configuration-snapshots/{version}",
        response_model=ConfigurationSnapshotRead,
        operation_id="readConfigurationSnapshot",
    )
    def read_configuration_snapshot(
        project_id: Identifier,
        version: Identifier,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> ConfigurationSnapshotRead:
        return invoke(lambda: service.read_configuration_snapshot(actor, project_id, version))

    @router.post(
        "/projects/{project_id}/local-configuration-releases",
        response_model=Resource,
        operation_id="publishLocalConfiguration",
    )
    def publish_local_configuration(
        project_id: Identifier,
        payload: LocalConfigurationPublish,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "publish_local_configuration", project_id, payload
        )
        return invoke(lambda: service.publish_local_configuration(actor, context, payload))

    @router.post(
        "/projects/{project_id}/references",
        response_model=Resource,
        status_code=201,
        operation_id="enrollReference",
    )
    def enroll_reference(
        project_id: Identifier,
        payload: ReferenceEnroll,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "enroll_reference", project_id, payload
        )
        return invoke(lambda: service.enroll_reference(actor, context, payload))

    @router.post(
        "/projects/{project_id}/references/accept",
        response_model=Resource,
        operation_id="acceptReference",
    )
    def accept_reference(
        project_id: Identifier,
        payload: ReferenceAccept,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "accept_reference", project_id, payload
        )
        return invoke(lambda: service.accept_reference(actor, context, payload))

    @router.post(
        "/projects/{project_id}/references/revalidate",
        response_model=Resource,
        operation_id="revalidateReference",
    )
    def revalidate_reference(
        project_id: Identifier,
        payload: ReferenceRevalidate,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "revalidate_reference", project_id, payload
        )
        return invoke(lambda: service.revalidate_reference(actor, context, payload))

    @router.post(
        "/projects/{project_id}/references/withdraw",
        response_model=Resource,
        operation_id="withdrawReference",
    )
    def withdraw_reference(
        project_id: Identifier,
        payload: ReferenceWithdraw,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "withdraw_reference", project_id, payload
        )
        return invoke(lambda: service.withdraw_reference(actor, context, payload))

    @router.post(
        "/projects/{project_id}/assets",
        response_model=Resource,
        status_code=201,
        operation_id="ingestAsset",
    )
    def ingest_asset(
        project_id: Identifier,
        payload: AssetIngest,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(actor, idempotency_key, "ingest_asset", project_id, payload)
        return invoke(lambda: service.ingest_asset(actor, context, payload))

    @router.post(
        "/projects/{project_id}/dataset-releases",
        response_model=Resource,
        status_code=201,
        operation_id="freezeRelease",
    )
    def freeze_release(
        project_id: Identifier,
        payload: ReleaseFreeze,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        context = MutationContext.bind(
            actor, idempotency_key, "freeze_release", project_id, payload
        )
        return invoke(lambda: service.freeze_release(actor, context, payload))

    @router.post(
        "/dataset-releases/{release_id}/variants",
        response_model=JobAccepted,
        status_code=202,
        operation_id="compileVariant",
    )
    def compile_variant(
        release_id: Identifier,
        payload: VariantCompile,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> JobAccepted:
        context = MutationContext.bind(
            actor, idempotency_key, "compile_variant", release_id, payload
        )
        return invoke(lambda: service.compile_variant(actor, context, payload))

    @router.post(
        "/variants/{variant_id}/training-runs",
        response_model=JobAccepted,
        status_code=202,
        operation_id="startTraining",
    )
    def start_training(
        variant_id: Identifier,
        payload: TrainingStart,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> JobAccepted:
        context = MutationContext.bind(
            actor, idempotency_key, "start_training", variant_id, payload
        )
        return invoke(lambda: service.start_training(actor, context, payload))

    @router.post(
        "/models/{model_id}/evaluations",
        response_model=JobAccepted,
        status_code=202,
        operation_id="startEvaluation",
    )
    def start_evaluation(
        model_id: Identifier,
        payload: EvaluationStart,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> JobAccepted:
        context = MutationContext.bind(
            actor, idempotency_key, "start_evaluation", model_id, payload
        )
        return invoke(lambda: service.start_evaluation(actor, context, payload))

    @router.post(
        "/projects/{project_id}/promotions",
        response_model=PromotionResult,
        operation_id="promoteModel",
    )
    def promote_model(
        project_id: Identifier,
        payload: PromotionRequest,
        idempotency_key: IdempotencyKey,
        actor: Annotated[ManagementPrincipal, Depends(admin)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> PromotionResult:
        context = MutationContext.bind(actor, idempotency_key, "promote", project_id, payload)
        return invoke(lambda: service.promote(actor, context, payload))

    @router.get("/projects", response_model=ResourcePage, operation_id="listProjects")
    def list_projects(
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
        cursor: Identifier | None = None,
        limit: PageLimit = 50,
    ) -> ResourcePage:
        return invoke(lambda: service.list_projects(actor, cursor, limit))

    @router.get(
        "/projects/{project_id}/resources",
        response_model=ResourcePage,
        operation_id="listResources",
    )
    def list_resources(
        project_id: Identifier,
        kind: ResourceKind,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
        cursor: Identifier | None = None,
        limit: PageLimit = 50,
    ) -> ResourcePage:
        return invoke(lambda: service.list_resources(actor, project_id, kind, cursor, limit))

    @router.get(
        "/resources/{kind}/{resource_id}", response_model=Resource, operation_id="readResource"
    )
    def read_resource(
        kind: ResourceKind,
        resource_id: Identifier,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> Resource:
        return invoke(lambda: service.read(actor, kind, resource_id))

    @router.get("/models/{model_id}/export", response_model=ExportRead, operation_id="readExport")
    def read_export(
        model_id: Identifier,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> ExportRead:
        return invoke(lambda: service.read_export(actor, model_id))

    @router.get(
        "/projects/{project_id}/references/lifecycle",
        response_model=ReferenceLifecycleList,
        operation_id="readReferenceLifecycle",
    )
    def read_reference_lifecycle(
        project_id: Identifier,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> ReferenceLifecycleList:
        return invoke(lambda: service.read_reference_lifecycle(actor, project_id))

    @router.get(
        "/projects/{project_id}/reference-packs/{pack_id}/versions/{version}",
        response_model=AnchorPackRead,
        operation_id="readAnchorPack",
    )
    def read_anchor_pack(
        project_id: Identifier,
        pack_id: Identifier,
        version: Identifier,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> AnchorPackRead:
        return invoke(lambda: service.read_anchor_pack(actor, project_id, pack_id, version))

    @router.get("/projects/{project_id}", response_model=ProjectDetail, operation_id="readProject")
    def read_project(
        project_id: Identifier,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> ProjectDetail:
        return invoke(lambda: service.read_project(actor, project_id))

    @router.get(
        "/projects/{project_id}/freeze-plan",
        response_model=FreezePlanRead,
        operation_id="readFreezePlan",
    )
    def read_freeze_plan(
        project_id: Identifier,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> FreezePlanRead:
        return invoke(lambda: service.read_freeze_plan(actor, project_id))

    @router.get(
        "/dataset-releases/{release_id}",
        response_model=ReleaseDetail,
        operation_id="readDatasetRelease",
    )
    def read_release(
        release_id: Identifier,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> ReleaseDetail:
        return invoke(lambda: service.read_release(actor, release_id))

    @router.get(
        "/dataset-releases/{release_id}/recipes/{recipe_id}/preview",
        response_model=RecipePreviewRead,
        operation_id="readRecipePreview",
    )
    def read_recipe_preview(
        release_id: Identifier,
        recipe_id: RecipeId,
        asset_id: Identifier,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> RecipePreviewRead:
        return invoke(lambda: service.read_recipe_preview(actor, release_id, recipe_id, asset_id))

    @router.get(
        "/projects/{project_id}/training-profiles",
        response_model=TrainingProfilesRead,
        operation_id="listTrainingProfiles",
    )
    def list_training_profiles(
        project_id: Identifier,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> TrainingProfilesRead:
        return invoke(lambda: service.list_training_profiles(actor, project_id))

    @router.get(
        "/training-runs/{run_id}", response_model=TrainingDetail, operation_id="readTrainingRun"
    )
    def read_training(
        run_id: Identifier,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> TrainingDetail:
        return invoke(lambda: service.read_training(actor, run_id))

    @router.get(
        "/evaluations/{evaluation_id}",
        response_model=EvaluationDetail,
        operation_id="readEvaluation",
    )
    def read_evaluation(
        evaluation_id: Identifier,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
    ) -> EvaluationDetail:
        return invoke(lambda: service.read_evaluation(actor, evaluation_id))

    @router.get(
        "/projects/{project_id}/registry/{recipe_id}",
        response_model=RegistryDetail,
        operation_id="readRegistry",
    )
    def read_registry(
        project_id: Identifier,
        recipe_id: RecipeId,
        actor: Annotated[ManagementPrincipal, Depends(reader)],
        service: Annotated[ManagementOperations, Depends(operations)],
        cursor: Identifier | None = None,
        limit: PageLimit = 50,
    ) -> RegistryDetail:
        return invoke(lambda: service.read_registry(actor, project_id, recipe_id, cursor, limit))

    return router
