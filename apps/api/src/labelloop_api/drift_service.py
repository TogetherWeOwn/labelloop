"""Authenticated containment selectors; historical qualification is never re-granted."""

import json
from dataclasses import replace
from uuid import uuid4

import sqlalchemy as sa

from labelloop_api import acceptance_store, model_schema, model_store
from labelloop_core.drift import AcceptanceState
from labelloop_core.qualification import QualificationContext
from labelloop_core.qualification_data import record_sha256
from labelloop_core.qualification_wire import loads


def _champion_promotion(connection, project_key, recipe_key, expected_registry_revision):
    """Read historical identity under the project lock, not a current quality grant."""
    project_id, recipe_id = acceptance_store._scope(connection, project_key, recipe_key)
    registry = model_store.load_registry(connection, project_key, recipe_key)
    champion = registry.champion
    if registry.revision != expected_registry_revision:
        raise ValueError("stale registry revision")
    if champion is None:
        raise ValueError("drift pause requires a current champion")
    rows = connection.execute(
        sa.select(model_schema.promotion_event.c.record)
        .where(
            model_schema.promotion_event.c.project_id == project_id,
            model_schema.promotion_event.c.recipe_id == recipe_id,
            model_schema.promotion_event.c.action == "promote",
        )
        .order_by(model_schema.promotion_event.c.revision.desc())
    ).scalars()
    for record in rows:
        promoted = model_store.decode_release(record["after"]["champion"])
        if promoted.release_id != champion.release_id:
            continue
        if replace(promoted, evaluation_run_ids=champion.evaluation_run_ids) != champion or not set(
            promoted.evaluation_run_ids
        ) <= set(champion.evaluation_run_ids):
            raise ValueError("champion differs from its recorded promotion contract")
        if record.get("qualification") is None:
            raise ValueError("champion has no recorded promotion qualification")
        context = loads(json.dumps(record["qualification"]), QualificationContext)
        evidence = context.evidence
        if (
            (evidence.pipeline.project_id, evidence.pipeline.recipe_id) != (project_key, recipe_key)
            or evidence.release_sha256 != record_sha256(promoted)
            or evidence.canary_release_sha256 != record_sha256(promoted)
            or evidence.final_test.dataset.project_id != project_key
        ):
            raise ValueError("promotion qualification identity mismatch")
        return context, record
    raise ValueError("missing champion promotion provenance")


def pause_current_champion(
    connection: sa.Connection,
    *,
    project_key: str,
    recipe_key: str,
    actor_id: str,
    expected_revision: int,
    expected_registry_revision: int,
    reason: str,
    class_id: str | None = None,
    source_id: str | None = None,
    condition: str | None = None,
) -> AcceptanceState:
    """Expand admin-selected axes from the champion's recorded promotion scope.

    No current quality gate is required for containment of an invalidated champion.
    A rollback keeps the original promotion lineage; later evaluation links may
    extend the release but cannot replace its model/preprocessing contract. Missing
    historical promotion provenance fails closed here, never inferred from today's
    policy. This port cannot resume, clear incidents or grant automatic acceptance.
    """
    context, _ = _champion_promotion(
        connection, project_key, recipe_key, expected_registry_revision
    )
    scope = tuple(
        cell
        for cell in context.evidence.scope
        if (class_id is None or cell.class_id == class_id)
        and (source_id is None or cell.source_id == source_id)
        and (condition is None or cell.condition == condition)
    )
    if not scope:
        raise ValueError("drift selection is outside recorded champion scope")
    return acceptance_store.pause_acceptance(
        connection,
        project_key=project_key,
        recipe_key=recipe_key,
        actor_id=actor_id,
        expected_revision=expected_revision,
        pause_id=str(uuid4()),
        qualification_id=context.evidence.qualification_id,
        scope=scope,
        reason=reason,
    )


def resume_current_champion(
    connection: sa.Connection,
    *,
    project_key: str,
    recipe_key: str,
    actor_id: str,
    expected_revision: int,
    expected_registry_revision: int,
    pause_ids: tuple[str, ...],
) -> AcceptanceState:
    """Revalidate the current champion's committed promotion, never request evidence.

    The existing transition reloads current policy, canonical influence, final state
    and durable vetoes, then requires a new qualified release and incident-bound
    fresh canary covering every selected pause. A historical promotion alone cannot
    clear incidents; missing or invalidated evidence fails closed.
    """
    context, record = _champion_promotion(
        connection, project_key, recipe_key, expected_registry_revision
    )
    evaluation_key = record.get("evaluation_key")
    if not isinstance(evaluation_key, str) or not evaluation_key.strip():
        raise ValueError("champion promotion has no final evaluation identity")
    return acceptance_store.resume_acceptance(
        connection,
        project_key=project_key,
        recipe_key=recipe_key,
        actor_id=actor_id,
        expected_revision=expected_revision,
        pause_ids=pause_ids,
        evaluation_key=evaluation_key,
        qualification=context,
    )
