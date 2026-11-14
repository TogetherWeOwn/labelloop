"""Human-only canonical annotation reads; no inference of current release eligibility."""

from datetime import datetime
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, create_engine, select
from sqlalchemy.exc import SQLAlchemyError

from labelloop_api.auth import require_reviewer
from labelloop_api.auth_store import Principal
from labelloop_api.database_url import quoted_database_url
from labelloop_api.http_contract import BOUNDARY_RESPONSES, BoundaryError
from labelloop_api.schema import annotation_revision, project, source_asset

# Persisted UUIDs are opaque lookup handles; canonical keys need not be URL segments.
router = APIRouter(
    prefix="/projects/{project_id}/assets/{asset_id}",
    tags=["annotations"],
    responses={**BOUNDARY_RESPONSES, 404: {"model": BoundaryError}},
)
Reviewer = Annotated[Principal, Depends(require_reviewer)]


class ReadModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RevisionRecord(ReadModel):
    revision: int
    parent_revision: int | None
    actor_kind: str
    actor_id: str
    created_at: datetime
    objects: list[dict[str, Any]]
    label_state: str
    approval_state: str
    review_tier: str
    scope_completeness: str
    recorded_training_eligible: bool
    recorded_gold_eligible: bool


class AnnotationSnapshot(ReadModel):
    project_key: str
    asset_key: str
    source_sha256: str
    width: int
    height: int
    annotation: RevisionRecord


class AnnotationHistory(ReadModel):
    revisions: list[RevisionRecord]
    next_before_revision: int | None


@lru_cache(maxsize=1)
def annotation_engine():
    return create_engine(quoted_database_url(), pool_pre_ping=True)


def revision_query(project_id: UUID, asset_id: UUID):
    # Both IDs are bound in every query, including subsequent history pages.
    return (
        select(
            annotation_revision.c.revision,
            annotation_revision.c.parent_revision,
            annotation_revision.c.actor_kind,
            annotation_revision.c.actor_id,
            annotation_revision.c.created_at,
            annotation_revision.c.objects,
            annotation_revision.c.label_state,
            annotation_revision.c.approval_state,
            annotation_revision.c.review_tier,
            annotation_revision.c.scope_completeness,
            annotation_revision.c.training_eligible.label("recorded_training_eligible"),
            annotation_revision.c.gold_eligible.label("recorded_gold_eligible"),
            project.c.project_key,
            source_asset.c.asset_key,
            source_asset.c.sha256.label("source_sha256"),
            source_asset.c.width,
            source_asset.c.height,
        )
        .select_from(
            annotation_revision.join(
                source_asset, annotation_revision.c.asset_id == source_asset.c.id
            ).join(project, source_asset.c.project_id == project.c.id)
        )
        .where(project.c.id == project_id, source_asset.c.id == asset_id)
        .order_by(annotation_revision.c.revision.desc())
    )


def record(row) -> RevisionRecord:
    return RevisionRecord.model_validate(
        {field: row[field] for field in RevisionRecord.model_fields}
    )


def load_snapshot(connection: Connection, project_id: UUID, asset_id: UUID) -> AnnotationSnapshot:
    row = connection.execute(revision_query(project_id, asset_id).limit(1)).mappings().first()
    if row is None:
        raise HTTPException(404, detail="annotation_not_found")
    return AnnotationSnapshot(
        project_key=row["project_key"],
        asset_key=row["asset_key"],
        source_sha256=row["source_sha256"],
        width=row["width"],
        height=row["height"],
        annotation=record(row),
    )


def load_history(
    connection: Connection,
    project_id: UUID,
    asset_id: UUID,
    *,
    before_revision: int | None,
    limit: int,
) -> AnnotationHistory:
    if not 1 <= limit <= 100 or (before_revision is not None and before_revision < 1):
        raise ValueError("invalid history page")
    statement = revision_query(project_id, asset_id)
    if before_revision is not None:
        statement = statement.where(annotation_revision.c.revision < before_revision)
    rows = connection.execute(statement.limit(limit + 1)).mappings().all()
    if not rows:
        # Distinguish an exhausted cursor from an absent or cross-project asset.
        load_snapshot(connection, project_id, asset_id)
    page = rows[:limit]
    return AnnotationHistory(
        revisions=[record(row) for row in page],
        next_before_revision=page[-1]["revision"] if len(rows) > limit else None,
    )


@router.get("/annotations", response_model=AnnotationSnapshot)
def annotation_snapshot(project_id: UUID, asset_id: UUID, actor: Reviewer):
    try:
        with annotation_engine().connect() as connection:
            return load_snapshot(connection, project_id, asset_id)
    except SQLAlchemyError:
        raise HTTPException(503, detail="annotations_unavailable") from None


@router.get("/annotation-history", response_model=AnnotationHistory)
def annotation_history(
    project_id: UUID,
    asset_id: UUID,
    actor: Reviewer,
    before_revision: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    try:
        with annotation_engine().connect() as connection:
            return load_history(
                connection,
                project_id,
                asset_id,
                before_revision=before_revision,
                limit=limit,
            )
    except SQLAlchemyError:
        raise HTTPException(503, detail="annotations_unavailable") from None
