"""Synthetic current-policy persistence and real registry/acceptance rejection controls."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
import sqlalchemy as sa

from labelloop_api import model_schema, qualification_schema
from labelloop_api.acceptance_store import require_automatic_acceptance
from labelloop_api.model_store import change_registry, load_registry
from labelloop_api.qualification_policy_store import (
    append_qualification_policy,
    current_policy_context,
    load_qualification_policy,
)
from labelloop_core.qualification import QualificationPolicy, QualificationTargets
from labelloop_core.qualification_data import record_sha256
from labelloop_core.registry import PromotionPolicy


def fixture_policy(connection, context, actor):
    """Explicit fixture-only configuration; never inferred by production stores."""
    evidence = context.evidence
    assert evidence.targets.fixture_only
    policy = QualificationPolicy(evidence.pipeline, evidence.scope, evidence.targets)
    scope = dict(project_key=policy.pipeline.project_id, recipe_key=policy.pipeline.recipe_id)
    try:
        current = load_qualification_policy(connection, **scope)
    except ValueError as error:
        assert "missing current qualification policy" in str(error)
        revision = 0
    else:
        if current.policy == policy:
            return current
        revision = current.revision
    return append_qualification_policy(
        connection, **scope, actor_id=actor, expected_revision=revision, policy=policy
    )


def exercise_policy(engine, context, actor, evaluation_key):
    evidence = context.evidence
    scope = dict(project_key=evidence.pipeline.project_id, recipe_key=evidence.pipeline.recipe_id)
    with engine.begin() as connection:
        original = load_qualification_policy(connection, **scope)
        assert current_policy_context(connection, context) == context
        before = load_registry(connection, **scope)
        event_count = connection.execute(
            sa.select(sa.func.count()).select_from(model_schema.promotion_event)
        ).scalar_one()

    def append(connection, policy, revision):
        return append_qualification_policy(
            connection, **scope, actor_id=actor, expected_revision=revision, policy=policy
        )

    changes = (
        replace(
            original.policy, pipeline=replace(original.policy.pipeline, prompt_sha256="1" * 64)
        ),
        replace(original.policy, scope=original.policy.scope[:1]),
    )
    unconfigured = QualificationTargets()
    changes += (
        replace(
            original.policy,
            targets=unconfigured,
            pipeline=replace(
                original.policy.pipeline, acceptance_policy_sha256=record_sha256(unconfigured)
            ),
        ),
    )
    for policy in changes:
        with engine.begin() as connection:
            current = load_qualification_policy(connection, **scope)
            updated = append(connection, policy, current.revision)
        with engine.begin() as connection:
            assert load_qualification_policy(connection, **scope) == updated
            # Forging the old current_pipeline together with old evidence cannot replay it.
            with pytest.raises(ValueError, match="stale or substituted"):
                change_registry(
                    connection,
                    **scope,
                    actor_id=actor,
                    expected_revision=before.revision,
                    candidate_key=before.champion.release_id,
                    evaluation_key=evaluation_key,
                    policy=PromotionPolicy(1, 0.0),
                    qualification=context,
                )
            with pytest.raises(ValueError, match="stale or substituted"):
                require_automatic_acceptance(
                    connection,
                    **scope,
                    evaluation_key=evaluation_key,
                    scope=evidence.scope[0],
                    qualification=context,
                )
            assert load_registry(connection, **scope) == before
            assert (
                connection.execute(
                    sa.select(sa.func.count()).select_from(model_schema.promotion_event)
                ).scalar_one()
                == event_count
            )
            append(connection, original.policy, updated.revision)

    with engine.begin() as connection:
        current = load_qualification_policy(connection, **scope)
        for statement in (
            sa.update(qualification_schema.policy_event).values(actor_id="replacement"),
            sa.delete(qualification_schema.policy_event),
            sa.text("TRUNCATE qualification_policy_event"),
        ):
            with connection.begin_nested() as transaction:
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.execute(statement)
                transaction.rollback()

        def fail(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("INSERT INTO qualification_policy_event"):
                raise RuntimeError("fixture policy append failure")

        sa.event.listen(connection, "before_cursor_execute", fail)
        try:
            with pytest.raises(RuntimeError, match="policy append failure"):
                append(connection, changes[0], current.revision)
        finally:
            sa.event.remove(connection, "before_cursor_execute", fail)
        assert load_qualification_policy(connection, **scope) == current

    def compete(index):
        policy = replace(
            original.policy,
            pipeline=replace(original.policy.pipeline, reviewer_model_version=f"fixture-{index}"),
        )
        try:
            with engine.begin() as connection:
                return append(connection, policy, current.revision)
        except ValueError as error:
            assert "stale qualification policy revision" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(compete, (1, 2)))
    assert sum(item is not None for item in outcomes) == 1
    winner = next(item for item in outcomes if item is not None)
    with engine.begin() as connection:
        assert load_qualification_policy(connection, **scope) == winner
        append(connection, original.policy, winner.revision)
        assert current_policy_context(connection, context) == context
        require_automatic_acceptance(
            connection,
            **scope,
            evaluation_key=evaluation_key,
            scope=evidence.scope[0],
            qualification=context,
        )
    return {
        "scope": "synthetic configuration mechanics, not pipeline execution or domain quality",
        "changed_pipeline_scope_and_null_targets_reject_old_registry_and_acceptance": True,
        "registry_heads_and_event_count_unchanged": True,
        "reconnect_and_restored_configuration_control": True,
        "one_expected_revision_race_winner": True,
        "immutable_update_delete_truncate_and_failed_append": True,
    }
