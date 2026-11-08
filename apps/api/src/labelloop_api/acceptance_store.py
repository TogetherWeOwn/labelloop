"""Append-only scoped drift journal; callers authorize principals, never model claims.

The project lock orders pauses/resumptions with registry, audit and final-test
writes. A journal transition grants no acceptance by itself: each acceptance must
check the current champion, annotation veto, final state and qualified scope.
"""

import json

import sqlalchemy as sa

from labelloop_api import model_schema, qualification_schema
from labelloop_api.annotation_audit_store import lock_project, require_no_annotation_veto
from labelloop_api.final_test_store import current_qualification_context
from labelloop_api.model_store import load_registry, stored_report
from labelloop_api.qualification_policy_store import current_policy_context
from labelloop_api.schema import preprocessing_recipe
from labelloop_core.drift import AcceptanceState
from labelloop_core.qualification import QualificationContext
from labelloop_core.qualification_data import ScopeCell
from labelloop_core.qualification_wire import dumps, loads


def _scope(connection, project_key, recipe_key):
    project_id = lock_project(connection, project_key)
    recipe_id = connection.execute(
        sa.select(preprocessing_recipe.c.id).where(preprocessing_recipe.c.recipe_key == recipe_key)
    ).scalar_one()
    return project_id, recipe_id


def load_acceptance(
    connection: sa.Connection, *, project_key: str, recipe_key: str
) -> AcceptanceState:
    project_id, recipe_id = _scope(connection, project_key, recipe_key)
    row = (
        connection.execute(
            sa.select(qualification_schema.acceptance_event)
            .where(
                qualification_schema.acceptance_event.c.project_id == project_id,
                qualification_schema.acceptance_event.c.recipe_id == recipe_id,
            )
            .order_by(qualification_schema.acceptance_event.c.revision.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return AcceptanceState(project_key, recipe_key)
    state = loads(json.dumps(row["record"]), AcceptanceState)
    if (state.project_id, state.recipe_id, state.revision) != (
        project_key,
        recipe_key,
        row["revision"],
    ):
        raise ValueError("acceptance journal identity or revision mismatch")
    return state


def _append(connection, project_id, recipe_id, actor_id, action, state, qualification=None):
    connection.execute(
        sa.insert(qualification_schema.acceptance_event).values(
            project_id=project_id,
            recipe_id=recipe_id,
            revision=state.revision,
            actor_id=actor_id,
            action=action,
            record=json.loads(dumps(state)),
            qualification=json.loads(dumps(qualification)) if qualification is not None else None,
        )
    )


def _actor(actor_id):
    if not actor_id.strip():
        raise ValueError("acceptance events require an authenticated actor")


def pause_acceptance(
    connection: sa.Connection,
    *,
    project_key: str,
    recipe_key: str,
    actor_id: str,
    expected_revision: int,
    pause_id: str,
    qualification_id: str,
    scope: tuple[ScopeCell, ...],
    reason: str,
) -> AcceptanceState:
    """Pause the current champion without requiring its now-invalid quality gates.

    The service derives qualification_id and expanded cells from trusted records.
    A missing/invalid qualification must not prevent containment of known drift.
    """
    _actor(actor_id)
    with connection.begin_nested():
        project_id, recipe_id = _scope(connection, project_key, recipe_key)
        registry = load_registry(connection, project_key, recipe_key)
        if registry.champion is None:
            raise ValueError("drift pause requires a current champion")
        state = load_acceptance(connection, project_key=project_key, recipe_key=recipe_key)
        changed = state.pause(
            registry.champion,
            pause_id=pause_id,
            qualification_id=qualification_id,
            scope=scope,
            reason=reason,
            expected_revision=expected_revision,
        )
        _append(connection, project_id, recipe_id, actor_id, "pause", changed)
    return changed


def _qualified_champion(connection, project_key, recipe_key, evaluation_key, qualification):
    if qualification is None:
        raise ValueError("automatic acceptance requires trusted qualification")
    if (
        qualification.evidence.pipeline.project_id,
        qualification.evidence.pipeline.recipe_id,
        qualification.evidence.final_test.dataset.project_id,
    ) != (project_key, recipe_key, project_key):
        raise ValueError("acceptance qualification cannot cross project or recipe boundaries")
    registry = load_registry(connection, project_key, recipe_key)
    release = registry.champion
    if release is None:
        raise ValueError("automatic acceptance requires a current champion")
    model_id = connection.execute(
        sa.select(model_schema.model_release.c.id).where(
            model_schema.model_release.c.release_key == release.release_id
        )
    ).scalar_one()
    project_id = lock_project(connection, project_key)
    require_no_annotation_veto(connection, project_id=project_id, model_id=model_id)
    context = current_policy_context(connection, qualification)
    context = current_qualification_context(connection, context)
    report = stored_report(connection, release, evaluation_key)
    return release, report, context


def resume_acceptance(
    connection: sa.Connection,
    *,
    project_key: str,
    recipe_key: str,
    actor_id: str,
    expected_revision: int,
    pause_ids: tuple[str, ...],
    evaluation_key: str,
    qualification: QualificationContext | None = None,
) -> AcceptanceState:
    """Resume only with incident-linked evidence for the current champion.

    Policy/pipeline/audit/canary provenance remains the authenticated service's
    trusted read port. Production callers omit it until those loaders exist.
    """
    _actor(actor_id)
    with connection.begin_nested():
        project_id, recipe_id = _scope(connection, project_key, recipe_key)
        state = load_acceptance(connection, project_key=project_key, recipe_key=recipe_key)
        release, report, context = _qualified_champion(
            connection, project_key, recipe_key, evaluation_key, qualification
        )
        changed = state.resume(
            release, report, context, pause_ids=pause_ids, expected_revision=expected_revision
        )
        _append(connection, project_id, recipe_id, actor_id, "resume", changed, context)
    return changed


def require_automatic_acceptance(
    connection: sa.Connection,
    *,
    project_key: str,
    recipe_key: str,
    scope: ScopeCell,
    evaluation_key: str,
    qualification: QualificationContext | None = None,
) -> None:
    """Guard each acceptance in the same outer transaction as its annotation write."""
    _scope(connection, project_key, recipe_key)
    state = load_acceptance(connection, project_key=project_key, recipe_key=recipe_key)
    release, report, context = _qualified_champion(
        connection, project_key, recipe_key, evaluation_key, qualification
    )
    state.require_acceptance(release, report, context, scope)
