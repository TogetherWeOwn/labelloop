"""Authenticated registry recovery; promotion has no HTTP qualification grant yet."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import NoResultFound, SQLAlchemyError

from labelloop_api import model_store
from labelloop_api.auth import auth_store, require_admin
from labelloop_api.auth_store import Principal

router = APIRouter(tags=["registry"])


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["rollback"]
    recipe_key: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")
    expected_revision: int = Field(ge=0)


@router.post("/projects/{project_key}/promotions")
def rollback(
    project_key: Annotated[str, Path(min_length=1, max_length=128, pattern=r"^\S+$")],
    payload: RollbackRequest,
    actor: Annotated[Principal, Depends(require_admin)],
) -> dict:
    # A rollback selects only the persisted pointer, never a caller's release or gates.
    # The store keeps project-first locking, adverse-audit vetoes and the atomic event.
    try:
        with auth_store().engine.begin() as connection:
            changed = model_store.change_registry(
                connection,
                project_key=project_key,
                recipe_key=payload.recipe_key,
                actor_id=actor.id,
                expected_revision=payload.expected_revision,
            )
            record = model_store.json_record(changed)
    except NoResultFound:
        raise HTTPException(404, detail="registry_scope_not_found") from None
    except ValueError:
        raise HTTPException(409, detail="registry_change_rejected") from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="registry_unavailable") from None
    return record
