"""Authenticated registry inspection/recovery; reads never grant qualification."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError, NoResultFound, SQLAlchemyError

from labelloop_api import acceptance_store, annotation_audit_store, model_store
from labelloop_api.auth import auth_store, require_admin, require_reviewer
from labelloop_api.auth_store import Principal
from labelloop_core.audit import AuditObservation

router = APIRouter(tags=["registry"])


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["rollback"]
    recipe_key: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")
    expected_revision: int = Field(ge=0)


class AuditObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    asset_id: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")
    revision: int = Field(ge=1)
    blind_full_image_inspected: bool
    labels_correct: bool
    scope_complete: bool
    output_truncated: bool = False


class AuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    audit_key: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")
    sample_key: str = Field(min_length=1, max_length=128, pattern=r"^\S+$")
    observations: list[AuditObservationRequest] = Field(min_length=1, max_length=1000)


@router.post("/projects/{project_key}/dataset-releases/{release_key}/audits", status_code=201)
def record_audit(
    project_key: Annotated[str, Path(min_length=1, max_length=128, pattern=r"^\S+$")],
    release_key: Annotated[str, Path(min_length=1, max_length=128, pattern=r"^\S+$")],
    payload: AuditRequest,
    actor: Annotated[Principal, Depends(require_reviewer)],
) -> dict:
    # Observations are human claims, not qualification. Membership, independence,
    # vetoes and sample binding come from the store; the session owns attribution.
    observations = tuple(
        AuditObservation(reviewer_id=actor.id, **item.model_dump()) for item in payload.observations
    )
    try:
        with auth_store().engine.begin() as connection:
            annotation_audit_store.record_annotation_audit(
                connection,
                project_key=project_key,
                release_key=release_key,
                audit_key=payload.audit_key,
                actor_id=actor.id,
                observations=observations,
                sample_key=payload.sample_key,
            )
    except NoResultFound:
        raise HTTPException(404, detail="audit_scope_not_found") from None
    except ValueError:
        raise HTTPException(409, detail="audit_rejected") from None
    except IntegrityError:
        raise HTTPException(409, detail="audit_rejected") from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="audit_unavailable") from None
    return {"audit_key": payload.audit_key, "recorded": True}


@router.get("/projects/{project_key}/registry/{recipe_key}")
def inspect_registry(
    project_key: Annotated[str, Path(min_length=1, max_length=128, pattern=r"^\S+$")],
    recipe_key: Annotated[str, Path(min_length=1, max_length=128, pattern=r"^\S+$")],
    actor: Annotated[Principal, Depends(require_admin)],
) -> dict:
    # The acceptance read validates project/recipe existence and takes the project
    # lock before the registry read. Both snapshots precede the same outer commit.
    # A champion or an empty pause history is not an automatic-acceptance grant.
    try:
        with auth_store().engine.begin() as connection:
            acceptance = acceptance_store.load_acceptance(
                connection, project_key=project_key, recipe_key=recipe_key
            )
            registry = model_store.load_registry(connection, project_key, recipe_key)
            record = {
                "registry": model_store.json_record(registry),
                "acceptance": model_store.json_record(acceptance),
            }
    except NoResultFound:
        raise HTTPException(404, detail="registry_scope_not_found") from None
    except ValueError:
        raise HTTPException(409, detail="registry_state_rejected") from None
    except SQLAlchemyError:
        raise HTTPException(503, detail="registry_unavailable") from None
    return record


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
