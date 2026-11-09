"""Bounded metadata-only history; recorded payloads never become display text."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import NoResultFound, SQLAlchemyError

from labelloop_api import model_schema as models
from labelloop_api import schema as db
from labelloop_api.auth import auth_store, require_reviewer
from labelloop_api.auth_schema import principal
from labelloop_api.auth_store import Principal, utc

router = APIRouter(tags=["audit"])
AuditKind = Literal["Review", "Promotion"]
ActorKind = Literal["human", "model", "system", "unknown"]
AuditSearch = Annotated[str | None, Query(min_length=1, max_length=128)]


class AuditMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: UUID
    at: datetime
    kind: AuditKind
    actor_id: str = Field(min_length=1, max_length=256)
    # Unknown historical promotion actors are not inferred to be human principals.
    actor_kind: ActorKind
    # Current directory metadata, not a historical name or an attribution grant.
    actor_name: str | None = Field(min_length=1, max_length=128)
    record_id: UUID
    revision: Annotated[int, Field(ge=0)]
    sentence: str


class AuditMetadataPage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_id: str
    events: list[AuditMetadata]
    total: Annotated[int, Field(ge=0)]
    next_after: str | None


def visible_events(project_id, actor, kind, since, through, actor_kind, search):
    # Select permitted sources before any filtering, counting, paging or export.
    review = (
        sa.select(
            db.review_event.c.id,
            db.review_event.c.created_at.label("at"),
            sa.literal("Review").label("kind"),
            db.review_event.c.actor_id,
            db.review_event.c.actor_kind,
            db.review_event.c.asset_id.label("record_id"),
            db.review_event.c.expected_revision.label("revision"),
        )
        .join(db.source_asset)
        .where(db.source_asset.c.project_id == project_id)
    )
    queries = [review]
    if actor.permits("admin"):
        queries.append(
            sa.select(
                models.promotion_event.c.id,
                models.promotion_event.c.created_at.label("at"),
                sa.literal("Promotion").label("kind"),
                models.promotion_event.c.actor_id,
                sa.literal("unknown").label("actor_kind"),
                models.promotion_event.c.recipe_id.label("record_id"),
                models.promotion_event.c.revision,
            ).where(models.promotion_event.c.project_id == project_id)
        )
    recorded = sa.union_all(*queries).subquery()
    # Resolve by immutable ID only, never by a legacy actor string equal to a
    # username. Preserve unknown promotion provenance and incompatible review IDs.
    compatible = sa.or_(
        recorded.c.actor_kind == "unknown",
        sa.and_(recorded.c.actor_kind == "human", principal.c.kind == "human"),
        sa.and_(recorded.c.actor_kind.in_(("model", "system")), principal.c.kind == "service"),
    )
    events = (
        sa.select(recorded, principal.c.name.label("actor_name"))
        .select_from(
            recorded.outerjoin(
                principal, sa.and_(recorded.c.actor_id == principal.c.id, compatible)
            )
        )
        .subquery()
    )
    query = sa.select(events)
    if kind is not None:
        query = query.where(events.c.kind == kind)
    if since is not None:
        query = query.where(events.c.at >= since)
    if through is not None:
        query = query.where(events.c.at <= through)
    if actor_kind is not None:
        query = query.where(events.c.actor_kind == actor_kind)
    if search is not None:
        # Search only public metadata. Escaped LIKE treats % and _ literally;
        # exact UUID comparisons work with both native and text-backed storage.
        matches = [
            sa.func.lower(column).contains(search.lower(), autoescape=True)
            for column in (events.c.actor_id, events.c.actor_name)
        ]
        try:
            identity = UUID(search)
        except ValueError:
            pass
        else:
            matches.extend((events.c.id == identity, events.c.record_id == identity))
        query = query.where(sa.or_(*matches))
    return query.subquery()


def load_audit_page(
    connection,
    *,
    project_key,
    actor,
    kind,
    since,
    through,
    after,
    limit,
    actor_kind=None,
    search=None,
):
    if (since is not None and since.utcoffset() is None) or (
        through is not None and through.utcoffset() is None
    ):
        raise ValueError("audit dates require an explicit timezone")
    since = utc(since) if since is not None else None
    through = utc(through) if through is not None else None
    if since is not None and through is not None and since > through:
        raise ValueError("inverted audit date range")
    project_id = connection.execute(
        sa.select(db.project.c.id).where(db.project.c.project_key == project_key)
    ).scalar_one()
    events = visible_events(project_id, actor, kind, since, through, actor_kind, search)
    query = sa.select(events)
    if after is not None:
        cursor_kind, separator, cursor_id = after.partition("/")
        if not separator or cursor_kind not in ("Review", "Promotion"):
            raise ValueError("invalid audit cursor")
        cursor = connection.execute(
            sa.select(events.c.at, events.c.kind, events.c.id).where(
                events.c.kind == cursor_kind, events.c.id == UUID(cursor_id)
            )
        ).one_or_none()
        if cursor is None:
            raise ValueError("audit cursor is outside the visible query")
        query = query.where(
            sa.tuple_(events.c.at, events.c.kind, events.c.id)
            < sa.tuple_(cursor.at, cursor.kind, cursor.id)
        )
    page = (
        query.order_by(events.c.at.desc(), events.c.kind.desc(), events.c.id.desc())
        .limit(limit + 1)
        .subquery()
    )
    total = sa.select(sa.func.count().label("total")).select_from(events).subquery()
    # One statement snapshot covers total and page, including an empty page. The
    # cursor is a visible row position, not authority or a frozen history snapshot.
    rows = (
        connection.execute(
            sa.select(total.c.total, page)
            .select_from(total.outerjoin(page, sa.true()))
            .order_by(page.c.at.desc(), page.c.kind.desc(), page.c.id.desc())
        )
        .mappings()
        .all()
    )
    records = [row for row in rows if row["id"] is not None]
    projected = [
        AuditMetadata(
            id=row["id"],
            at=utc(row["at"]),
            kind=row["kind"],
            actor_id=row["actor_id"],
            actor_kind=row["actor_kind"],
            actor_name=row["actor_name"],
            record_id=row["record_id"],
            revision=row["revision"],
            sentence="Recorded a review decision."
            if row["kind"] == "Review"
            else "Recorded a registry transition.",
        )
        for row in records[:limit]
    ]
    return AuditMetadataPage(
        project_id=project_key,
        events=projected,
        total=rows[0]["total"],
        next_after=f"{projected[-1].kind}/{projected[-1].id}" if len(records) > limit else None,
    )


def read_audit(project_key, actor, kind, since, through, after, limit, actor_kind, search):
    try:
        with auth_store().engine.begin() as connection:
            page = load_audit_page(
                connection,
                project_key=project_key,
                actor=actor,
                kind=kind,
                since=since,
                through=through,
                after=after,
                limit=limit,
                actor_kind=actor_kind,
                search=search,
            )
    except NoResultFound:
        raise HTTPException(404, detail="audit_scope_not_found") from None
    except (ValueError, TypeError, KeyError):
        raise HTTPException(409, detail="audit_state_rejected") from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="audit_unavailable") from None
    return page


ProjectKey = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^\S+$")]


@router.get("/projects/{project_key}/audit-log", response_model=AuditMetadataPage)
def inspect_audit(
    project_key: ProjectKey,
    actor: Annotated[Principal, Depends(require_reviewer)],
    kind: AuditKind | None = None,
    since: datetime | None = None,
    through: datetime | None = None,
    after: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    actor_kind: ActorKind | None = None,
    search: AuditSearch = None,
) -> AuditMetadataPage:
    return read_audit(project_key, actor, kind, since, through, after, limit, actor_kind, search)


@router.get("/projects/{project_key}/audit-log/export", response_class=Response)
def export_audit(
    project_key: ProjectKey,
    actor: Annotated[Principal, Depends(require_reviewer)],
    kind: AuditKind | None = None,
    since: datetime | None = None,
    through: datetime | None = None,
    actor_kind: ActorKind | None = None,
    search: AuditSearch = None,
) -> Response:
    # Buffer a bounded complete result before responding: never silently truncate
    # or start an export that may fail halfway through a database cursor.
    page = read_audit(project_key, actor, kind, since, through, None, 1000, actor_kind, search)
    if page.next_after is not None:
        raise HTTPException(409, detail="audit_export_requires_narrower_range")
    return Response(
        content="".join(event.model_dump_json() + "\n" for event in page.events),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="audit.jsonl"'},
    )
