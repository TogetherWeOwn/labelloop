"""Synthetic journal mechanics over the Actions PostgreSQL smoke database."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
import sqlalchemy as sa
from acceptance_smoke import exercise_acceptance
from evaluation_fixture import independent_evaluation_variant
from qualification_fixture import qualification_context, synthetic_final

from labelloop_api import model_schema, qualification_schema
from labelloop_api.final_test_store import (
    current_qualification_context,
    expose_final_test,
    invalidate_final_test,
    load_final_test,
    record_candidate_selection,
    register_final_test,
)
from labelloop_api.model_store import load_release, save_evaluation
from labelloop_core.evaluation_lifecycle import EvaluationSet
from labelloop_core.qualification_data import ScopeCell
from labelloop_core.qualification_wire import dumps
from labelloop_core.registry import PromotionPolicy


def exercise_final_test(engine, result, manifest, model, report, seed_variant):
    # These are invented canonical populations and copied scores, NEVER model gold.
    from labelloop_api.evaluation_group_store import load_evaluation_set

    dataset_key = result.request.dataset_release_id + f"-synthetic-final-{model.policy.imgsz}"
    final = synthetic_final(
        report,
        run_id=result.request.run_id + "-journal-final",
        dataset_id=dataset_key,
    )
    actor = "fixture-lifecycle-admin"
    scope = dict(project_key=model.project_id, dataset_key=dataset_key)
    with engine.begin() as connection:
        source_variant_id = connection.execute(
            sa.select(model_schema.evaluation_run.c.variant_id).where(
                model_schema.evaluation_run.c.run_key == report.run_id
            )
        ).scalar_one()
        report = replace(
            report,
            run_id=report.run_id + "-journal-development",
            dataset_release_id=dataset_key + "-development",
        )
        development_variant_id = independent_evaluation_variant(
            connection, source_variant_id, report.dataset_release_id
        )
        save_evaluation(
            connection, model_key=model.release_id, variant_id=development_variant_id, report=report
        )
        variant_id = independent_evaluation_variant(connection, source_variant_id, dataset_key)
        save_evaluation(connection, model_key=model.release_id, variant_id=variant_id, report=final)
        dataset = load_evaluation_set(connection, **scope, role="locked_final_test")
        development = load_evaluation_set(
            connection,
            project_key=model.project_id,
            dataset_key=report.dataset_release_id,
            role="development",
        )
        assert not set(dataset.group_ids) & set(development.group_ids)
        register_final_test(connection, dataset=dataset, actor_id=actor)
        selection = record_candidate_selection(
            connection,
            project_key=model.project_id,
            model_key=model.release_id,
            selection_key="selection-" + model.release_id,
            development_key=report.run_id,
            development_set=development,
            actor_id=actor,
        )
    # Registering the selection's development identity as locked after selection
    # cannot be hidden by replaying the earlier development role or report.
    with engine.begin() as connection, connection.begin_nested() as transaction:
        # Prepare a different development selection before exposing the reserved
        # identity. These copied scores exercise lifecycle mechanics, not quality.
        replay_dataset_key = dataset_key + "-role-development"
        replay_development = replace(
            report,
            run_id=report.run_id + "-role-development",
            dataset_release_id=replay_dataset_key,
        )
        replay_variant_id = seed_variant(
            connection,
            replace(result.request, dataset_release_id=replay_dataset_key),
            manifest | {"release_id": replay_dataset_key},
        )
        replay_final = synthetic_final(
            report,
            run_id=report.run_id + "-role-final",
            dataset_id=development.release_id,
        )
        development_variant_id = connection.execute(
            sa.select(model_schema.evaluation_run.c.variant_id).where(
                model_schema.evaluation_run.c.run_key == report.run_id
            )
        ).scalar_one()
        save_evaluation(
            connection,
            model_key=model.release_id,
            variant_id=replay_variant_id,
            report=replay_development,
        )
        save_evaluation(
            connection,
            model_key=model.release_id,
            variant_id=development_variant_id,
            report=replay_final,
        )
        replay_selection = record_candidate_selection(
            connection,
            project_key=model.project_id,
            model_key=model.release_id,
            selection_key="role-exposure-" + model.release_id,
            development_key=replay_development.run_id,
            development_set=EvaluationSet(
                replay_dataset_key, model.project_id, ("role-development-group",), "development"
            ),
            actor_id=actor,
        )
        register_final_test(
            connection, dataset=replace(development, role="locked_final_test"), actor_id=actor
        )
        selection_args = dict(
            project_key=model.project_id,
            model_key=model.release_id,
            selection_key="role-replay-" + model.release_id,
            development_key=report.run_id,
            development_set=development,
            actor_id=actor,
        )
        with pytest.raises(ValueError, match="role forbids"):
            record_candidate_selection(connection, **selection_args)
        with pytest.raises(ValueError, match="role forbids"):
            expose_final_test(
                connection,
                **scope,
                model_key=model.release_id,
                selection_key=selection.selection_id,
                evaluation_key=final.run_id,
                expected_revision=0,
                actor_id=actor,
            )
        assert load_final_test(connection, **scope).revision == 0
        development_scope = dict(project_key=model.project_id, dataset_key=development.release_id)
        unexposed = load_final_test(connection, **development_scope)
        with pytest.raises(ValueError, match="unexposed final test"):
            invalidate_final_test(
                connection,
                **development_scope,
                action="adapt",
                reason="must not adapt before exposure",
                expected_revision=0,
                actor_id=actor,
            )
        assert load_final_test(connection, **development_scope) == unexposed
        replay_exposed = expose_final_test(
            connection,
            **development_scope,
            model_key=model.release_id,
            selection_key=replay_selection.selection_id,
            evaluation_key=replay_final.run_id,
            expected_revision=0,
            actor_id=actor,
        )
        assert replay_exposed.assessments
        invalidate_final_test(
            connection,
            **development_scope,
            action="adapt",
            reason="explicitly reclassified as development/regression",
            expected_revision=1,
            actor_id=actor,
        )
        assert (
            record_candidate_selection(connection, **selection_args).development_set_id
            == development.release_id
        )
        invalidate_final_test(
            connection,
            project_key=model.project_id,
            dataset_key=development.release_id,
            action="quarantine",
            reason="related group integrity finding",
            expected_revision=2,
            actor_id=actor,
        )
        with pytest.raises(ValueError, match="quarantined"):
            record_candidate_selection(
                connection,
                **(selection_args | {"selection_key": "quarantined-" + model.release_id}),
            )
        transaction.rollback()
    # Selection is durable before access; exposure is its own committed transaction.
    with engine.begin() as connection:
        exposed = expose_final_test(
            connection,
            **scope,
            model_key=model.release_id,
            selection_key=selection.selection_id,
            evaluation_key=final.run_id,
            expected_revision=0,
            actor_id=actor,
        )
    with engine.begin() as connection:
        assert load_final_test(connection, **scope) == exposed
        model_id = connection.execute(
            sa.select(model_schema.model_release.c.id).where(
                model_schema.model_release.c.release_key == model.release_id
            )
        ).scalar_one()
        release = load_release(connection, model_id)
        cells = tuple(ScopeCell(value, "fixture-source", "ordinary") for value in release.class_ids)
        cells += (ScopeCell(release.class_ids[0], "fixture-source", "low-light"),)
        from evaluation_fixture import compiled_training_lineage

        context = qualification_context(release, final, PromotionPolicy(1, 0.0), scope=cells)
        lineage = compiled_training_lineage(connection, release, context.evidence.lineage)
        context = replace(
            context,
            evidence=replace(
                context.evidence,
                final_test=exposed,
                lineage=replace(lineage, development=development),
            ),
            current_final_test=exposed,
        )
        assert current_qualification_context(connection, context, model_id=model_id) == context
        context.require_qualified(release, final)
        for revision, message in ((0, "stale final-test"), (1, "already exposed")):
            with pytest.raises(ValueError, match=message):
                expose_final_test(
                    connection,
                    **scope,
                    model_key=model.release_id,
                    selection_key=selection.selection_id,
                    evaluation_key=final.run_id,
                    expected_revision=revision,
                    actor_id=actor,
                )
        with pytest.raises(sa.exc.IntegrityError):
            register_final_test(connection, dataset=dataset, actor_id=actor)
        with pytest.raises(sa.exc.NoResultFound):
            load_final_test(connection, project_key=model.project_id, dataset_key="missing")
        for table in (
            qualification_schema.candidate_selection,
            qualification_schema.final_test_event,
        ):
            for statement in (sa.delete(table), sa.update(table).values(actor_id="replacement")):
                with connection.begin_nested() as transaction:
                    with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                        connection.execute(statement)
                    transaction.rollback()

        # Failed event insertion cannot leave a partial lifecycle revision.
        def fail_event(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("INSERT INTO qualification_final_test_event"):
                raise RuntimeError("fixture lifecycle event failure")

        sa.event.listen(connection, "before_cursor_execute", fail_event)
        try:
            with pytest.raises(RuntimeError, match="lifecycle event failure"):
                invalidate_final_test(
                    connection,
                    **scope,
                    action="adapt",
                    reason="failed write",
                    expected_revision=1,
                    actor_id=actor,
                )
        finally:
            sa.event.remove(connection, "before_cursor_execute", fail_event)
        assert load_final_test(connection, **scope) == exposed

    acceptance_journal = exercise_acceptance(
        engine, result, manifest, release, final, context, report, seed_variant
    )

    def compete(reason):
        try:
            with engine.begin() as connection:
                return invalidate_final_test(
                    connection,
                    **scope,
                    action="quarantine",
                    reason=reason,
                    expected_revision=1,
                    actor_id=actor,
                )
        except ValueError as error:
            assert "stale final-test" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(compete, ("cross-split relation", "membership integrity")))
    assert sum(state is not None for state in results) == 1
    quarantined = next(state for state in results if state is not None)
    with engine.begin() as connection:
        assert load_final_test(connection, **scope) == quarantined
        invalidated = invalidate_final_test(
            connection,
            **scope,
            action="adapt",
            reason="aggregate guided later change",
            expected_revision=2,
            actor_id=actor,
        )
    with engine.begin() as connection:
        assert load_final_test(connection, **scope) == invalidated
        assert invalidated.assessments == exposed.assessments
        assert invalidated.effective_role == "development"
        assert invalidated.quarantine_reasons == quarantined.quarantine_reasons
        with pytest.raises(ValueError, match="stale or mismatched"):
            current_qualification_context(connection, context, model_id=model_id).require_qualified(
                release, final
            )
        # The original success is unchanged historical evidence, not current authority.
        assert context.current_final_test == exposed
        assert (
            connection.execute(
                sa.select(sa.func.count())
                .select_from(qualification_schema.final_test_event)
                .where(
                    qualification_schema.final_test_event.c.project_id
                    == connection.execute(
                        sa.select(model_schema.model_release.c.project_id).where(
                            model_schema.model_release.c.id == model_id
                        )
                    ).scalar_one(),
                    qualification_schema.final_test_event.c.dataset_key == dataset_key,
                )
            ).scalar_one()
            == 4
        )
    return {
        "scope": "synthetic lifecycle persistence only; no independent final accuracy",
        "acceptance_journal": acceptance_journal,
        "selection": dumps(selection),
        "exposed": dumps(exposed),
        "invalidated": dumps(invalidated),
        "racing_invalidation_winners": 1,
        "failed_write_unchanged": True,
        "historical_context_rejected_after_reload": True,
        "locked_development_identity_blocks_selection_and_later_exposure": True,
        "explicit_adaptation_allows_development_but_quarantine_blocks_it": True,
    }
