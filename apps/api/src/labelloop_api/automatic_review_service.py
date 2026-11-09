"""Trusted fixture annotation orchestration, not an HTTP or model authorization port."""

from typing import Any

import sqlalchemy as sa

from labelloop_api import drift_service, revision_store
from labelloop_api.qualification_store import lock_project
from labelloop_api.schema import source_asset
from labelloop_core.qualification_data import ScopeCell, require_count, require_name, require_scope


def append_current_review(
    connection: sa.Connection,
    *,
    project_key: str,
    recipe_key: str,
    asset_key: str,
    actor_id: str,
    expected_revision: int,
    expected_registry_revision: int,
    scope: tuple[ScopeCell, ...],
    objects: list[dict[str, Any]],
    scope_completeness: str,
    training_eligible: bool = False,
    proposal_run_keys: tuple[str, ...] = (),
) -> int:
    """Derive qualification from the current champion and reuse the atomic writer.

    Callers own authentication, original reviewer execution, source/condition truth
    and complete proposal influence. None may be inferred from a model response.
    Until those producers are connected, this port permits explicit fixture policy
    only; it is not mounted as a route. It accepts no report, context, targets,
    champion, human-review fields or gold eligibility. The existing writer reloads
    current policy/final/groups/vetoes/drift before appending anything. Retain the
    outer transaction through commit; no provider I/O belongs inside it.
    """
    for value in (project_key, recipe_key, asset_key, actor_id):
        require_name(value)
    require_count(expected_revision, minimum=1)
    require_count(expected_registry_revision)
    require_scope(scope)
    with connection.begin_nested():
        project_id = lock_project(connection, project_key)
        connection.execute(
            sa.select(source_asset.c.id).where(
                source_asset.c.project_id == project_id,
                source_asset.c.asset_key == asset_key,
            )
        ).scalar_one()
        context, record = drift_service._champion_promotion(
            connection, project_key, recipe_key, expected_registry_revision
        )
        if not context.evidence.targets.fixture_only:
            raise ValueError("automatic annotation service requires explicit fixture-only policy")
        if not set(scope) <= set(context.evidence.scope):
            raise ValueError("automatic annotation scope is outside recorded champion scope")
        evaluation_key = record.get("evaluation_key")
        require_name(evaluation_key)
        return revision_store.append_review(
            connection,
            asset_id=asset_key,
            actor_kind="system",
            actor_id=actor_id,
            expected_revision=expected_revision,
            approval_state="auto_accepted",
            review_tier="machine_verified_no_human_review",
            scope_completeness=scope_completeness,
            objects=objects,
            training_eligible=training_eligible,
            automatic=revision_store.AutomaticReview(recipe_key, evaluation_key, scope, context),
            proposal_run_keys=proposal_run_keys,
        )
