"""Synthetic journal mechanics over the Actions PostgreSQL smoke database."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
import sqlalchemy as sa
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
from labelloop_core.qualification_wire import dumps
from labelloop_core.registry import PromotionPolicy


def exercise_final_test(engine, result, manifest, model, report, seed_variant):
    # These are invented groups and copied scores, NEVER independent final accuracy.
    dataset_key = result.request.dataset_release_id + f"-synthetic-final-{model.policy.imgsz}"
    final = synthetic_final(
        report,
        run_id=result.request.run_id + "-journal-final",
        dataset_id=dataset_key,
    )
    dataset = EvaluationSet(dataset_key, model.project_id, ("final-group",), "locked_final_test")
    development = EvaluationSet(
        report.dataset_release_id, model.project_id, ("dev-group",), "development"
    )
    actor = "fixture-lifecycle-admin"
    scope = dict(project_key=model.project_id, dataset_key=dataset_key)
    with engine.begin() as connection:
        variant_id = seed_variant(
            connection,
            replace(result.request, dataset_release_id=dataset_key),
            manifest | {"release_id": dataset_key},
        )
        save_evaluation(connection, model_key=model.release_id, variant_id=variant_id, report=final)
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
        context = qualification_context(release, final, PromotionPolicy(1, 0.0))
        context = replace(
            context,
            evidence=replace(
                context.evidence,
                final_test=exposed,
                lineage=replace(context.evidence.lineage, development=development),
            ),
            current_final_test=exposed,
        )
        assert current_qualification_context(connection, context) == context
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
            current_qualification_context(connection, context).require_qualified(release, final)
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
        "selection": dumps(selection),
        "exposed": dumps(exposed),
        "invalidated": dumps(invalidated),
        "racing_invalidation_winners": 1,
        "failed_write_unchanged": True,
        "historical_context_rejected_after_reload": True,
    }
