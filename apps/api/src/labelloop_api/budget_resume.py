"""UnMounted human-session-only resume routes; product integration is separate."""

from dataclasses import asdict
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from labelloop_api import auth
from labelloop_api.auth_store import Principal
from labelloop_api.budget_resume_store import BudgetResumeStore
from labelloop_api.database_url import quoted_database_url
from labelloop_core.budget import BudgetRefused


class ResumeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request) -> Response:
            try:
                if request.method == "POST":
                    body = bytearray()
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > 1024:
                            raise HTTPException(413, detail="invalid_request")
                    request._body = bytes(body)
                response = await handler(request)
            except RequestValidationError:
                response = JSONResponse({"detail": "invalid_request"}, status_code=422)
            except HTTPException as error:
                response = JSONResponse({"detail": error.detail}, status_code=error.status_code)
            response.headers["Cache-Control"] = "no-store"
            return response

        return guarded


router = APIRouter(
    prefix="/projects/{project_id}/paid-stage-pause",
    tags=["paid-stage-resume"],
    route_class=ResumeRoute,
)


@lru_cache(maxsize=1)
def resume_store() -> BudgetResumeStore:
    return BudgetResumeStore(create_engine(quoted_database_url(), pool_pre_ping=True))


def session_admin(request: Request) -> Principal:
    # Re-authenticate locally rather than trusting arbitrary request.state or a
    # product mount's middleware configuration. Bearer credentials never qualify.
    if request.headers.get("authorization") is not None or not request.cookies.get(auth.COOKIE):
        raise HTTPException(401, detail="authentication_required")
    try:
        denied = auth.authenticate_request(request)
        if denied is not None:
            raise HTTPException(
                denied.status_code,
                detail=("csrf_denied" if denied.status_code == 403 else "authentication_required"),
            )
        actor = auth.require_admin(request)
    except SQLAlchemyError:
        raise HTTPException(503, detail="authentication_unavailable") from None
    if actor.kind != "human" or actor.role != "admin":
        raise HTTPException(403, detail="permission_denied")
    return actor


class ResumeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_policy_revision: int = Field(ge=1, le=2_147_483_646)
    paused_state_token: str = Field(min_length=67, max_length=67, pattern=r"^v1\.[0-9a-f]{64}$")


class PauseMetadata(BaseModel):
    project_id: str
    policy_revision: int
    pause_reason: Literal["insufficient_credits", "estimate_overrun"] | None
    state_token: str | None


class ResumeResult(BaseModel):
    project_id: str
    policy_revision: int


ProjectId = Annotated[str, Path(min_length=1, max_length=256)]
Admin = Annotated[Principal, Depends(session_admin)]
Store = Annotated[BudgetResumeStore, Depends(resume_store)]


def refusal(error: BudgetRefused) -> HTTPException:
    if error.code == "budget_not_configured":
        return HTTPException(404, detail="budget_not_configured")
    return HTTPException(409, detail="resume_conflict")


@router.get("", response_model=PauseMetadata)
def paused_state(project_id: ProjectId, actor: Admin, store: Store) -> PauseMetadata:
    try:
        return PauseMetadata(**asdict(store.paused_state(project_id, actor=actor)))
    except BudgetRefused as error:
        raise refusal(error) from None
    except (SQLAlchemyError, ValueError, TypeError, KeyError):
        raise HTTPException(503, detail="resume_unavailable") from None


@router.post("/resume", response_model=ResumeResult)
def resume(
    project_id: ProjectId, payload: ResumeCommand, actor: Admin, store: Store
) -> ResumeResult:
    try:
        result = store.resume(
            project_id,
            actor=actor,
            expected_revision=payload.expected_policy_revision,
            state_token=payload.paused_state_token,
        )
        return ResumeResult(**asdict(result))
    except BudgetRefused as error:
        raise refusal(error) from None
    except (SQLAlchemyError, ValueError, TypeError, KeyError):
        raise HTTPException(503, detail="resume_unavailable") from None
