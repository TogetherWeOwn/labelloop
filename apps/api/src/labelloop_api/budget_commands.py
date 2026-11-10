"""Session-authorized limit edits; no dispatch, pricing, privacy or resume grant."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import NoResultFound, SQLAlchemyError

from labelloop_api.auth import auth_store, require_admin
from labelloop_api.auth_store import Principal
from labelloop_api.budget_reads import BudgetLimits
from labelloop_api.budget_store import BudgetStore
from labelloop_core.budget import BudgetPolicy, BudgetRefused

router = APIRouter(tags=["costs"])


class BudgetLimitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: Annotated[int, Field(ge=1)]
    policy: BudgetLimits


class BudgetPolicyRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    project_id: str
    policy_revision: Annotated[int, Field(ge=1)]


@router.post("/projects/{project_key}/costs/policy", response_model=BudgetPolicyRevision)
def update_cost_limits(
    project_key: Annotated[str, Path(min_length=1, max_length=128, pattern=r"^\S+$")],
    payload: BudgetLimitUpdate,
    actor: Annotated[Principal, Depends(require_admin)],
) -> BudgetPolicyRevision:
    try:
        revision = BudgetStore(auth_store().engine).set_limits(
            project_key,
            actor=actor,
            expected_revision=payload.expected_revision,
            policy=BudgetPolicy(**payload.policy.model_dump()),
        )
    except NoResultFound:
        raise HTTPException(404, detail="cost_scope_not_found") from None
    except BudgetRefused as error:
        if error.code == "budget_not_configured":
            raise HTTPException(404, detail="cost_scope_not_found") from None
        raise HTTPException(409, detail="cost_state_rejected") from None
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, detail="cost_state_rejected") from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="costs_unavailable") from None
    return BudgetPolicyRevision(project_id=project_key, policy_revision=revision)
