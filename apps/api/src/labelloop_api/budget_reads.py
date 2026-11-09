"""Authenticated bounded ledger inspection, never a paid-operation grant."""

from dataclasses import asdict
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import NoResultFound, SQLAlchemyError

from labelloop_api import budget_schema as db
from labelloop_api.auth import auth_store, require_admin
from labelloop_api.auth_store import Principal
from labelloop_api.budget_store import decode_entry
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


def load_budget_page(connection, *, project_key, after, limit):
    # All budget writers take this same head lock before changing policy/entries.
    # Hold it through projection and outer commit for one consistent page only.
    head = (
        connection.execute(
            sa.select(db.head).where(db.head.c.project_id == project_key).with_for_update()
        )
        .mappings()
        .one()
    )
    policy = BudgetPolicy(**head["policy"]["limits"])
    query = sa.select(db.operation.c.operation_id, db.operation.c.entry).where(
        db.operation.c.project_id == project_key
    )
    if after is not None:
        query = query.where(db.operation.c.operation_id > after)
    rows = (
        connection.execute(query.order_by(db.operation.c.operation_id).limit(limit + 1))
        .mappings()
        .all()
    )
    operations = []
    for row in rows[:limit]:
        entry = decode_entry(row["entry"])
        request = entry.request
        if request.project_id != project_key or request.operation_id != row["operation_id"]:
            raise ValueError("ledger identity mismatch")
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
