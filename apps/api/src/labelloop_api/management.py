"""Management route factory; mount only behind the shared session/CSRF boundary."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse

from .management_contracts import (
    AssetIngest,
    ClassCatalogDraft,
    EvaluationStart,
    ExportRead,
    GuideDraft,
    Identifier,
    JobAccepted,
    ManagementError,
    ProjectCreate,
    PromotionRequest,
    PromotionResult,
    RecipeId,
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
