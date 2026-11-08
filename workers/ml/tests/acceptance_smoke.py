"""Synthetic drift persistence only; these records establish no domain accuracy."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
import sqlalchemy as sa
from qualification_context_smoke import exercise_context_assembly
from qualification_fixture import qualification_context, synthetic_final
from qualification_policy_smoke import exercise_policy, fixture_policy
from registry_http_smoke import (
    exercise_http_final_invalidation,
    exercise_http_resume,
    reject_http_resume,
)

from labelloop_api import model_schema, qualification_schema
from labelloop_api.acceptance_store import (
    load_acceptance,
    pause_acceptance,
    require_automatic_acceptance,
    resume_acceptance,
)
from labelloop_api.final_test_store import (
    expose_final_test,
    record_candidate_selection,
    register_final_test,
)
from labelloop_api.model_store import (
    change_registry,
    load_registry,
    load_release,
    save_evaluation,
    save_model,
)
from labelloop_api.schema import preprocessing_recipe, project
from labelloop_core.qualification_wire import dumps
from labelloop_core.registry import PromotionPolicy


def exercise_acceptance(
    engine, result, manifest, release, final, context, development, seed_variant
):
    actor = "fixture-drift-admin"
    scope = dict(project_key=release.project_id, recipe_key=release.recipe_id)
    affected, unaffected = context.evidence.scope[:2]
    other_recipe = f"full-letterbox-{640 if release.policy.imgsz == 320 else 320}-v1"
    pause_args = dict(
        **scope,
        actor_id=actor,
        qualification_id=context.evidence.qualification_id,
        scope=(affected,),
    )
    guard_args = dict(**scope, evaluation_key=final.run_id, qualification=context)

    def load_other(connection):
        # The other recipe may not have been seeded yet in a focused first-side run.
        return (
            connection.execute(
                sa.select(qualification_schema.acceptance_event.c.record)
                .join(preprocessing_recipe)
                .join(project)
                .where(
                    project.c.project_key == release.project_id,
                    preprocessing_recipe.c.recipe_key == other_recipe,
                )
                .order_by(qualification_schema.acceptance_event.c.revision)
            )
            .scalars()
            .all()
        )

    with engine.begin() as connection:
        fixture_policy(connection, context, actor)
    policy_evidence = exercise_policy(engine, context, actor, final.run_id)
    assembly_evidence = exercise_context_assembly(engine, release, final, context, actor)
    with engine.begin() as connection:
        before = load_acceptance(connection, **scope)
        assert before.revision == 0
        other = load_other(connection)
        require_automatic_acceptance(connection, **guard_args, scope=affected)
        paused = pause_acceptance(
            connection,
            **pause_args,
            pause_id="camera-layout",
            reason="layout changed",
            expected_revision=0,
        )
    with engine.begin() as connection:
        assert load_acceptance(connection, **scope) == paused
        assert load_other(connection) == other
        with pytest.raises(ValueError, match="paused for affected"):
            require_automatic_acceptance(connection, **guard_args, scope=affected)
        require_automatic_acceptance(connection, **guard_args, scope=unaffected)
        with pytest.raises(ValueError, match="stale acceptance"):
            pause_acceptance(
                connection,
                **pause_args,
                pause_id="stale",
                reason="old revision",
                expected_revision=0,
            )
        with pytest.raises(ValueError, match="new qualified release"):
            resume_acceptance(
                connection,
                **scope,
                actor_id=actor,
                expected_revision=1,
                pause_ids=("camera-layout",),
                evaluation_key=final.run_id,
                qualification=context,
            )

        def fail_event(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("INSERT INTO qualification_acceptance_event"):
                raise RuntimeError("fixture drift event failure")

        sa.event.listen(connection, "before_cursor_execute", fail_event)
        try:
            with pytest.raises(RuntimeError, match="drift event failure"):
                pause_acceptance(
                    connection,
                    **pause_args,
                    pause_id="failed",
                    reason="failed event",
                    expected_revision=1,
                )
        finally:
            sa.event.remove(connection, "before_cursor_execute", fail_event)
        assert load_acceptance(connection, **scope) == paused
        for statement in (
            sa.delete(qualification_schema.acceptance_event),
            sa.update(qualification_schema.acceptance_event).values(actor_id="replacement"),
            sa.text("TRUNCATE qualification_acceptance_event"),
        ):
            with connection.begin_nested() as transaction:
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.execute(statement)
                transaction.rollback()

    def compete(pause_id):
        try:
            with engine.begin() as connection:
                return pause_acceptance(
                    connection,
                    **pause_args,
                    pause_id=pause_id,
                    reason="new drift signal",
                    expected_revision=1,
                )
        except ValueError as error:
            assert "stale acceptance" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(compete, ("audit-shift", "scale-shift")))
    assert sum(value is not None for value in outcomes) == 1
    paused = next(value for value in outcomes if value is not None)
    pause_ids = tuple(item.pause_id for item in paused.pauses)
    incidents = tuple(paused.incident_reference(value) for value in pause_ids)
    with engine.begin() as connection:
        original_registry = load_registry(connection, **scope)
    reject_http_resume(engine, original_registry, paused, pause_ids)

    def qualify(connection, model_key, suffix, *, incumbent=None):
        # Separately frozen synthetic membership and copied scores, not model gold.
        dataset_key = result.request.dataset_release_id + f"-drift-{release.policy.imgsz}-{suffix}"
        from evaluation_fixture import independent_evaluation_variant

        from labelloop_api.evaluation_group_store import load_evaluation_set

        source_variant_id = connection.execute(
            sa.select(model_schema.evaluation_run.c.variant_id).where(
                model_schema.evaluation_run.c.run_key == final.run_id
            )
        ).scalar_one()
        variant_id = independent_evaluation_variant(connection, source_variant_id, dataset_key)
        report = synthetic_final(
            final, run_id=model_key + "-drift-final-" + suffix, dataset_id=dataset_key
        )
        save_evaluation(connection, model_key=model_key, variant_id=variant_id, report=report)
        incumbent_report = None
        if incumbent is not None:
            incumbent_report = replace(
                report, run_id=incumbent.release_id + "-comparison-" + suffix
            )
            save_evaluation(
                connection,
                model_key=incumbent.release_id,
                variant_id=variant_id,
                report=incumbent_report,
            )
        model_id = connection.execute(
            sa.select(model_schema.model_release.c.id).where(
                model_schema.model_release.c.release_key == model_key
            )
        ).scalar_one()
        model = load_release(connection, model_id)
        register_final_test(
            connection,
            actor_id=actor,
            dataset=load_evaluation_set(
                connection,
                project_key=release.project_id,
                dataset_key=dataset_key,
                role="locked_final_test",
            ),
        )
        selection = record_candidate_selection(
            connection,
            project_key=release.project_id,
            model_key=model_key,
            selection_key=model_key + "-drift-selection-" + suffix,
            development_key=development.run_id,
            development_set=context.evidence.lineage.development,
            actor_id=actor,
        )
        exposed = expose_final_test(
            connection,
            project_key=release.project_id,
            dataset_key=dataset_key,
            model_key=model_key,
            selection_key=selection.selection_id,
            evaluation_key=report.run_id,
            expected_revision=0,
            actor_id=actor,
        )
        qualified = qualification_context(
            model,
            report,
            PromotionPolicy(1, 0.0),
            scope=context.evidence.scope,
            remediation_for=incidents,
        )
        from evaluation_fixture import compiled_training_lineage

        lineage = compiled_training_lineage(connection, model, qualified.evidence.lineage)
        qualified = replace(
            qualified,
            current_final_test=exposed,
            evidence=replace(
                qualified.evidence,
                final_test=exposed,
                selection_id=selection.selection_id,
                lineage=replace(lineage, development=context.evidence.lineage.development),
            ),
        )
        qualified.require_qualified(model, report)
        fixture_policy(connection, qualified, actor)
        return model, report, qualified, incumbent_report

    with engine.begin() as connection:
        # A new identity alone is insufficient: both qualification and canary must
        # reference the persisted incidents, constructed before this evidence.
        row = (
            connection.execute(
                sa.select(model_schema.model_release).where(
                    model_schema.model_release.c.release_key == release.release_id
                )
            )
            .mappings()
            .one()
        )
        candidate = replace(release, release_id=release.release_id + "-drift-remediated")
        save_model(
            connection,
            training_run_key=result.request.run_id,
            release=candidate,
            export_evidence=row["export_evidence"],
        )
        candidate, fresh_final, fresh_context, incumbent_report = qualify(
            connection, candidate.release_id, "remediation", incumbent=release
        )
        registry = load_registry(connection, **scope)
        promoted = change_registry(
            connection,
            **scope,
            actor_id=actor,
            expected_revision=registry.revision,
            candidate_key=candidate.release_id,
            evaluation_key=fresh_final.run_id,
            incumbent_evaluation_key=incumbent_report.run_id,
            policy=PromotionPolicy(1, 0.0),
            qualification=fresh_context,
        )
    with engine.begin() as connection:
        resume_args = dict(
            **scope,
            actor_id=actor,
            expected_revision=paused.revision,
            pause_ids=pause_ids,
            evaluation_key=fresh_final.run_id,
        )
        unlinked = replace(
            fresh_context,
            evidence=replace(
                fresh_context.evidence,
                remediation_for=(),
                canary_audit=replace(fresh_context.evidence.canary_audit, remediation_for=()),
            ),
        )
        with pytest.raises(ValueError, match="post-incident"):
            resume_acceptance(connection, **resume_args, qualification=unlinked)
    resumed = exercise_http_resume(engine, promoted, paused, pause_ids, fresh_context)
    with engine.begin() as connection:
        assert load_acceptance(connection, **scope) == resumed
        saved = connection.execute(
            sa.select(qualification_schema.acceptance_event.c.qualification)
            .join(preprocessing_recipe)
            .join(project)
            .where(
                project.c.project_key == release.project_id,
                preprocessing_recipe.c.recipe_key == release.recipe_id,
                qualification_schema.acceptance_event.c.revision == resumed.revision,
            )
        ).scalar_one()
        assert saved == json.loads(dumps(fresh_context))
        assert all(item.resumed_by_release_id == candidate.release_id for item in resumed.pauses)
        require_automatic_acceptance(
            connection,
            **scope,
            evaluation_key=fresh_final.run_id,
            qualification=fresh_context,
            scope=affected,
        )
    exercise_http_final_invalidation(
        engine,
        release.project_id,
        fresh_final.dataset_release_id,
        "adapt",
        "final result guided later change",
        1,
    )
    with engine.begin() as connection:
        with pytest.raises(ValueError, match="stale or mismatched"):
            require_automatic_acceptance(
                connection,
                **scope,
                evaluation_key=fresh_final.run_id,
                qualification=fresh_context,
                scope=affected,
            )
        # Even a fresh synthetic qualification cannot rehabilitate the invalidated
        # release identity after rollback; the pause's history remains authoritative.
        old, old_final, old_context, _ = qualify(connection, release.release_id, "rollback")
        rolled_back = change_registry(
            connection, **scope, actor_id=actor, expected_revision=promoted.revision
        )
        assert rolled_back.champion == old
        with pytest.raises(ValueError, match="drift-invalidated release"):
            require_automatic_acceptance(
                connection,
                **scope,
                evaluation_key=old_final.run_id,
                qualification=old_context,
                scope=affected,
            )
        assert load_acceptance(connection, **scope) == resumed
        assert load_other(connection) == other
    for action in ("adapt", "quarantine"):
        with engine.begin() as connection:
            # The historical context still passes the pure gates. Only the store's
            # mandatory current-journal read prevents this otherwise valid promotion.
            policy = PromotionPolicy(1, 0.0)
            fresh_context.require_qualified(candidate, fresh_final, incumbent_report, policy)
            before = load_registry(connection, **scope)
            events = connection.execute(
                sa.select(sa.func.count()).select_from(model_schema.promotion_event)
            ).scalar_one()
            with pytest.raises(ValueError, match="stale or mismatched"):
                change_registry(
                    connection,
                    **scope,
                    actor_id=actor,
                    expected_revision=before.revision,
                    candidate_key=candidate.release_id,
                    evaluation_key=fresh_final.run_id,
                    incumbent_evaluation_key=incumbent_report.run_id,
                    policy=policy,
                    qualification=fresh_context,
                )
            assert load_registry(connection, **scope) == before
            assert (
                connection.execute(
                    sa.select(sa.func.count()).select_from(model_schema.promotion_event)
                ).scalar_one()
                == events
            )
        if action == "adapt":
            exercise_http_final_invalidation(
                engine,
                release.project_id,
                fresh_final.dataset_release_id,
                "quarantine",
                "later grouping integrity finding",
                2,
            )
    return {
        "scope": "synthetic drift persistence and HTTP final-test invalidation, not domain quality",
        "authenticated_final_invalidation_recovered": True,
        "http_invalidation_denials_and_failures_unchanged": True,
        "current_policy_journal": policy_evidence,
        "persisted_context_assembly": assembly_evidence,
        "registry_reloaded_final_after_adaptation_and_quarantine": True,
        "stale_final_preserved_complete_head_and_event_count": True,
        "paused_and_resumed_across_connections": True,
        "http_resumption_reloaded_changed_pipeline_scope_and_null_targets": True,
        "http_policy_rejections_preserved_all_journals_and_registry": True,
        "unaffected_cell_and_other_recipe_unchanged": True,
        "racing_pause_winners": 1,
        "failed_event_unchanged": True,
        "preincident_evidence_rejected": True,
        "rollback_did_not_restore_acceptance": True,
    }
