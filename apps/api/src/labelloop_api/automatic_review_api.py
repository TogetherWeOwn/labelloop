"""Admin-triggered fixture mechanics only; no real machine execution authority."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import NoResultFound, SQLAlchemyError

from labelloop_api import annotation_reads, automatic_review_service
from labelloop_api.auth import require_admin
from labelloop_api.auth_store import Principal
from labelloop_core.qualification_data import ScopeCell

router = APIRouter(tags=["annotations"])
Name = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")]


class FixtureScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    class_id: Name
    source_id: Name
    condition: Name


class FixtureReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    recipe_key: Name
    expected_revision: int = Field(ge=1)
    expected_registry_revision: int = Field(ge=0)
    scope: list[FixtureScope] = Field(min_length=1, max_length=1000)
    proposal_run_keys: list[Name] = Field(default_factory=list, max_length=100)


class FixtureReviewResult(BaseModel):
    revision: int
    fixture_only: Literal[True] = True


@router.post(
    "/projects/{project_id}/assets/{asset_id}/fixture-automatic-reviews",
    response_model=FixtureReviewResult,
    status_code=201,
)
def append_fixture_review(
    project_id: UUID,
    asset_id: UUID,
    payload: FixtureReviewRequest,
    actor: Annotated[Principal, Depends(require_admin)],
):
    # Reuse canonical objects, never request-supplied geometry or approval fields.
    # This explicit fixture surface does not attest reviewer execution, provenance
    # completeness or source/condition truth, and cannot qualify real-policy writes.
    try:
        with annotation_reads.annotation_engine().begin() as connection:
            current = annotation_reads.load_snapshot(connection, project_id, asset_id)
            if current.annotation.revision != payload.expected_revision:
                raise ValueError("stale expected annotation revision")
            revision = automatic_review_service.append_current_review(
                connection,
                project_key=current.project_key,
                asset_key=current.asset_key,
                recipe_key=payload.recipe_key,
                actor_id=actor.id,
                expected_revision=payload.expected_revision,
                expected_registry_revision=payload.expected_registry_revision,
                scope=tuple(ScopeCell(**cell.model_dump()) for cell in payload.scope),
                objects=current.annotation.objects,
                scope_completeness="exhaustive_for_scope",
                training_eligible=False,
                proposal_run_keys=tuple(payload.proposal_run_keys),
            )
            record = FixtureReviewResult(revision=revision)
    except NoResultFound:
        raise HTTPException(404, detail="automatic_review_scope_not_found") from None
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, detail="fixture_automatic_review_rejected") from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="annotations_unavailable") from None
    return record
