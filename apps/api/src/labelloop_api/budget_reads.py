"""Authenticated bounded ledger inspection, never a paid-operation grant."""

from dataclasses import asdict
from datetime import date, datetime
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from sqlalchemy.exc import NoResultFound, SQLAlchemyError

from labelloop_api import budget_schema as db
from labelloop_api.auth import auth_store, require_admin
from labelloop_api.auth_store import Principal, utc
from labelloop_api.budget_store import DispatchIntent, decode_entry, digest
from labelloop_api.review_budget import ReviewBatch
from labelloop_api.schema import project
from labelloop_core.budget import BudgetPolicy

router = APIRouter(tags=["costs"])
Amount = Annotated[int, Field(ge=0)]


class BudgetLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    monthly_limit: Amount | None
    iteration_limit: Amount | None
    operation_limit: Amount | None
    autonomous_spend: bool


class CostOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_id: str
    iteration_id: str
    month: str
    price_version: str
    estimate: Amount
    actual: Amount | None
    exposure: Amount
    state: Literal[
        "reserved", "dispatched", "unconfirmed", "settled", "cancelled", "insufficient_credits"
    ]
    approved_by: str | None
    reserved_autonomously: bool
    # Original reservation event only; missing historical evidence stays unknown.
    reserved_at: datetime | None
    # Platform intent does not distinguish training, annotation and prediction.
    kind: Literal["LLM review"] | None
    annotation_job_id: str | None


class BudgetPage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_id: str
    currency: Literal["USD"] = "USD"
    unit: Literal["micro_usd"] = "micro_usd"
    # This revisions the policy, not the mutable ledger or a cross-page snapshot.
    policy_revision: Annotated[int, Field(ge=1)]
    policy: BudgetLimits
    pause_reason: Literal["insufficient_credits", "estimate_overrun"] | None
    operations: list[CostOperation]
    next_after: str | None


class CostTotals(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_count: Amount
    actual: Amount
    confirmed_estimate: Amount
    # Pending buckets retain core exposure, including known child charges.
    reserved: Amount
    unconfirmed: Amount
    exposure: Amount
    delta: int


class BudgetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_id: str
    currency: Literal["USD"] = "USD"
    unit: Literal["micro_usd"] = "micro_usd"
    policy_revision: Annotated[int, Field(ge=1)]
    policy: BudgetLimits
    pause_reason: Literal["insufficient_credits", "estimate_overrun"] | None
    month: str
    iteration_id: str
    monthly: CostTotals
    iteration: CostTotals


SUMMARY_MAX_OPERATIONS = 10_000


def _month(value: str) -> str:
    date.fromisoformat(value + "-01")
    return value


def _locked_head(connection, project_key):
    # All budget writers take this same head lock. Each response is one read,
    # never a snapshot shared with a separate page/summary request.
    return (
        connection.execute(
            sa.select(db.head).where(db.head.c.project_id == project_key).with_for_update()
        )
        .mappings()
        .one()
    )


def _scoped_entry(row, project_key):
    entry = decode_entry(row["entry"])
    if entry.request.project_id != project_key or entry.request.operation_id != row["operation_id"]:
        raise ValueError("ledger identity mismatch")
    return entry


def _totals(entries):
    actual = confirmed_estimate = reserved = unconfirmed = exposure = count = 0
    for entry in entries:
        count += 1
        exposure += entry.exposure
        if entry.actual is not None:
            actual += entry.actual
            confirmed_estimate += entry.request.estimate
        elif entry.state == "unconfirmed":
            unconfirmed += entry.exposure
        elif entry.state != "cancelled":
            reserved += entry.exposure
    return CostTotals(
        operation_count=count,
        actual=actual,
        confirmed_estimate=confirmed_estimate,
        reserved=reserved,
        unconfirmed=unconfirmed,
        exposure=exposure,
        delta=actual - confirmed_estimate,
    )


def load_budget_summary(connection, *, project_key, month, iteration_id):
    head = _locked_head(connection, project_key)
    rows = (
        connection.execute(
            sa.select(db.operation.c.operation_id, db.operation.c.entry)
            .where(db.operation.c.project_id == project_key)
            .order_by(db.operation.c.operation_id)
            .limit(SUMMARY_MAX_OPERATIONS + 1)
        )
        .mappings()
        .all()
    )
    if len(rows) > SUMMARY_MAX_OPERATIONS:
        raise ValueError("complete ledger summary exceeds read bound")
    entries = [_scoped_entry(row, project_key) for row in rows]
    policy = BudgetPolicy(**head["policy"]["limits"])
    return BudgetSummary(
        project_id=project_key,
        policy_revision=head["revision"],
        policy=BudgetLimits(**asdict(policy)),
        pause_reason=head["pause_reason"],
        month=month,
        iteration_id=iteration_id,
        monthly=_totals(entry for entry in entries if entry.request.month == month),
        iteration=_totals(entry for entry in entries if entry.request.iteration_id == iteration_id),
    )


def _reservation_times(connection, project_key, entries):
    if not entries:
        return {}
    rows = (
        connection.execute(
            sa.select(db.event.c.operation_id, db.event.c.record, db.event.c.occurred_at)
            .where(
                db.event.c.project_id == project_key,
                db.event.c.operation_id.in_(entries),
                db.event.c.action == "reserve",
            )
            .limit(len(entries) + 1)
        )
        .mappings()
        .all()
    )
    if len(rows) > len(entries):
        raise ValueError("ambiguous reservation history")
    result = {}
    for row in rows:
        key = row["operation_id"]
        recorded = decode_entry(row["record"])
        current = entries[key]
        if (
            key in result
            or recorded.request != current.request
            or recorded.approval != current.approval
            or recorded.reserved_autonomously != current.reserved_autonomously
            or recorded.state != "reserved"
        ):
            raise ValueError("reservation history mismatch")
        result[key] = utc(row["occurred_at"])
    return result


def load_budget_page(connection, *, project_key, after, limit):
    head = _locked_head(connection, project_key)
    policy = BudgetPolicy(**head["policy"]["limits"])
    query = sa.select(
        db.operation.c.operation_id,
        db.operation.c.entry,
        db.operation.c.intent,
        db.operation.c.policy_revision,
        db.operation.c.dry_run_digest,
    ).where(db.operation.c.project_id == project_key)
    if after is not None:
        query = query.where(db.operation.c.operation_id > after)
    rows = (
        connection.execute(query.order_by(db.operation.c.operation_id).limit(limit + 1))
        .mappings()
        .all()
    )
    entries = {row["operation_id"]: _scoped_entry(row, project_key) for row in rows[:limit]}
    reserved_at = _reservation_times(connection, project_key, entries)
    operations = []
    for row in rows[:limit]:
        entry = entries[row["operation_id"]]
        request = entry.request
        intent = DispatchIntent(**row["intent"])
        if intent.review_batch is not None and not isinstance(intent.review_batch, ReviewBatch):
            raise ValueError("invalid review batch")
        if row["dry_run_digest"] != digest(
            {
                "request": asdict(request),
                "intent": asdict(intent),
                "policy_revision": row["policy_revision"],
            }
        ):
            raise ValueError("reservation intent mismatch")
        operations.append(
            CostOperation(
                operation_id=request.operation_id,
                iteration_id=request.iteration_id,
                month=request.month,
                price_version=request.price_version,
                estimate=request.estimate,
                actual=entry.actual,
                exposure=entry.exposure,
                state=entry.state,
                approved_by=entry.approval.actor_id if entry.approval else None,
                reserved_autonomously=entry.reserved_autonomously,
                reserved_at=reserved_at.get(request.operation_id),
                kind="LLM review" if intent.stage == "llm" else None,
                annotation_job_id=(
                    intent.review_batch.annotation_job_id if intent.review_batch else None
                ),
            )
        )
    return BudgetPage(
        project_id=project_key,
        policy_revision=head["revision"],
        policy=BudgetLimits(**asdict(policy)),
        pause_reason=head["pause_reason"],
        operations=operations,
        next_after=rows[limit - 1]["operation_id"] if len(rows) > limit else None,
    )


@router.get("/projects/{project_key}/costs/summary", response_model=BudgetSummary)
def inspect_cost_summary(
    project_key: Annotated[str, Path(min_length=1, max_length=128, pattern=r"^\S+$")],
    month: Annotated[str, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$"), AfterValidator(_month)],
    iteration_id: Annotated[str, Query(min_length=1, max_length=256, pattern=r"\S")],
    actor: Annotated[Principal, Depends(require_admin)],
) -> BudgetSummary:
    try:
        with auth_store().engine.begin() as connection:
            connection.execute(
                sa.select(project.c.id).where(project.c.project_key == project_key)
            ).scalar_one()
            summary = load_budget_summary(
                connection, project_key=project_key, month=month, iteration_id=iteration_id
            )
    except NoResultFound:
        raise HTTPException(404, detail="cost_scope_not_found") from None
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, detail="cost_state_rejected") from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="costs_unavailable") from None
    return summary


@router.get("/projects/{project_key}/costs", response_model=BudgetPage)
def inspect_costs(
    project_key: Annotated[str, Path(min_length=1, max_length=128, pattern=r"^\S+$")],
    actor: Annotated[Principal, Depends(require_admin)],
    after: Annotated[str | None, Query(min_length=1, max_length=256, pattern=r"\S")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> BudgetPage:
    try:
        with auth_store().engine.begin() as connection:
            # Missing projects and unconfigured budgets are both unavailable. No
            # default policy or empty ledger is manufactured for either case.
            connection.execute(
                sa.select(project.c.id).where(project.c.project_key == project_key)
            ).scalar_one()
            page = load_budget_page(connection, project_key=project_key, after=after, limit=limit)
    except NoResultFound:
        raise HTTPException(404, detail="cost_scope_not_found") from None
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, detail="cost_state_rejected") from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="costs_unavailable") from None
    return page
