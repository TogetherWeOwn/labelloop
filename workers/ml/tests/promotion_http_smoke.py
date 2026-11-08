"""Synthetic HTTP promotion on Actions' disposable PostgreSQL, never domain quality.

Savepoint controls and separate committed/reconnected controls adapt only connection
configuration: production sessions, CSRF, service, stores and gates run.
"""

import json
from contextlib import contextmanager
from dataclasses import replace

import sqlalchemy as sa
from registry_http_smoke import (
    authenticated_client,
    login,
    qualification_journals,
    snapshot,
)

from labelloop_api.model_store import json_record
from labelloop_core.registry import RegistryState


class FixtureEngine:
    """Borrow the outer fixture connection without committing or closing its history."""

    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection

    @contextmanager
    def begin(self):
        with self.connection.begin_nested():
            yield self.connection


def exercise_http_promotion(connection, candidate, before, arguments):
    engine = FixtureEngine(connection)
    path = f"/projects/{candidate.project_id}/promotions"
    payload = {
        key: value for key, value in arguments.items() if key not in {"actor_id", "project_key"}
    }
    payload["action"] = "promote"
    original = snapshot(engine, candidate.project_id)
    journals = qualification_journals(engine)
    with connection.begin_nested() as fixture:
        with authenticated_client(engine) as (client, identities, workers):
            assert client.post(path, json=payload).status_code == 401
            reviewer = login(client, identities["reviewer"][1])
            assert client.post(path, headers=reviewer, json=payload).status_code == 403
            client.cookies.clear()
            for token in workers.values():
                assert (
                    client.post(
                        path, headers={"authorization": "Bearer " + token}, json=payload
                    ).status_code
                    == 403
                )
            headers = login(client, identities["admin"][1])
            for denied in (
                {},
                headers | {"origin": "https://untrusted.invalid"},
                headers | {"x-csrf-token": "wrong"},
            ):
                assert client.post(path, headers=denied, json=payload).status_code == 403
            for extra in (
                {"actor_id": "forged"},
                {"qualification": {}},
                {"evaluation_key": "chosen"},
                {"require_fixture_policy": False},
            ):
                response = client.post(path, headers=headers, json=payload | extra)
                assert response.status_code == 422
                assert response.json() == {"detail": "invalid_request"}
            for target, extra, status in (
                (path, {"expected_revision": before.revision - 1}, 409),
                ("/projects/missing-http-project/promotions", {}, 404),
                (path, {"recipe_key": "missing-http-recipe", "expected_revision": 0}, 404),
                (path, {"model_key": "missing-http-model"}, 404),
                (path, {"annotation_release_key": "missing-http-population"}, 409),
                (path, {"canary_release_key": "missing-http-canary"}, 409),
            ):
                response = client.post(target, headers=headers, json=payload | extra)
                assert response.status_code == status
                assert response.json() == {
                    "detail": "registry_scope_not_found"
                    if status == 404
                    else "registry_change_rejected"
                }
                assert response.headers["cache-control"] == "no-store"
            assert snapshot(engine, candidate.project_id) == original
            assert qualification_journals(engine) == journals

            injected = []

            def fail_event(_conn, _cursor, statement, _parameters, _context, _many):
                if statement.startswith("INSERT INTO promotion_event"):
                    injected.append(True)
                    raise sa.exc.SQLAlchemyError("private fixture promotion event failure")

            sa.event.listen(connection, "before_cursor_execute", fail_event)
            try:
                response = client.post(path, headers=headers, json=payload)
            finally:
                sa.event.remove(connection, "before_cursor_execute", fail_event)
            assert injected == [True]
            assert response.status_code == 503
            assert response.json() == {"detail": "registry_unavailable"}
            assert snapshot(engine, candidate.project_id) == original
            assert qualification_journals(engine) == journals

            response = client.post(path, headers=headers, json=payload)
            expected = RegistryState(
                candidate.project_id,
                candidate.recipe_id,
                before.revision + 1,
                candidate,
                before.champion,
            )
            assert response.status_code == 200
            assert response.json() == json_record(expected)
            assert response.headers["cache-control"] == "no-store"
            after = snapshot(engine, candidate.project_id)
            index = 0 if candidate.policy.imgsz == 320 else 1
            assert after[0][index] == expected
            assert after[0][1 - index] == original[0][1 - index]
            assert len(after[1]) == len(original[1]) + 1
            added = next(row for row in after[1] if row not in original[1])
            assert added["actor_id"] == identities["admin"][0]
            assert added["action"] == "promote"
            assert added["record"]["after"] == json_record(expected)
            assert qualification_journals(engine) == journals
            for change in ({}, {"expected_revision": expected.revision}):
                replay = client.post(path, headers=headers, json=payload | change)
                assert replay.status_code == 409
                assert replay.json() == {"detail": "registry_change_rejected"}
                assert snapshot(engine, candidate.project_id) == after
        fixture.rollback()
    assert snapshot(engine, candidate.project_id) == original
    assert qualification_journals(engine) == journals


def exercise_http_quality_vetoes(engine, release, final, context, actor):
    import pytest
    from qualification_context_smoke import _outcome, prepare_sole_final_candidate

    from labelloop_api.model_store import load_registry, save_evaluation
    from labelloop_api.promotion_service import promote_candidate

    target = context.evidence.targets.critical_slices[0]
    class_id = target.class_id
    metrics = final.overall.per_class[class_id]
    critical = final.slices[target.slice_name]
    sliced = critical.per_class[class_id]
    unsupported = replace(
        metrics, supported=False, support=0, map50=None, map50_95=None, precision=None, recall=None
    )
    cases = (
        (
            "unsupported-class",
            replace(
                final,
                overall=replace(
                    final.overall, per_class=final.overall.per_class | {class_id: unsupported}
                ),
            ),
            "unsupported class or insufficient qualification support",
        ),
        (
            "insufficient-class-support",
            replace(
                final,
                overall=replace(
                    final.overall,
                    per_class=final.overall.per_class | {class_id: replace(metrics, support=0)},
                ),
            ),
            "unsupported class or insufficient qualification support",
        ),
        (
            "missing-critical-slice",
            replace(
                final,
                slices={
                    key: value for key, value in final.slices.items() if key != target.slice_name
                },
            ),
            "missing critical-slice evidence",
        ),
        (
            "insufficient-critical-support",
            replace(
                final,
                slices=final.slices
                | {
                    target.slice_name: replace(
                        critical,
                        per_class=critical.per_class | {class_id: replace(sliced, support=0)},
                    )
                },
            ),
            "unsupported class or insufficient qualification support",
        ),
        (
            "critical-ap50-regression",
            replace(
                final,
                slices=final.slices
                | {
                    target.slice_name: replace(
                        critical,
                        per_class=critical.per_class | {class_id: replace(sliced, map50=0.0)},
                    )
                },
            ),
            "unacceptable critical-slice AP50 regression",
        ),
    )
    scope = dict(project_key=release.project_id, recipe_key=release.recipe_id)
    for name, adverse, message in cases:
        # Only one metric dimension changes; preserve the aggregate quality numbers.
        assert adverse.overall.map50_95 == final.overall.map50_95
        with engine.begin() as connection:
            candidate, report, variant = prepare_sole_final_candidate(
                connection, release, adverse, context, actor, suffix="-http-" + name
            )
            incumbent = load_registry(connection, **scope).champion
            assert incumbent is not None
            comparison = replace(
                final,
                run_id=report.run_id + "-incumbent",
                dataset_release_id=report.dataset_release_id,
                weights_sha256=incumbent.weights_sha256,
                class_map_sha256=incumbent.class_map_sha256,
                policy=incumbent.policy,
            )
            if name == "critical-ap50-regression":
                comparison = replace(
                    comparison,
                    slices=comparison.slices
                    | {
                        target.slice_name: replace(
                            critical,
                            per_class=critical.per_class | {class_id: replace(sliced, map50=1.0)},
                        )
                    },
                )
            save_evaluation(
                connection, model_key=incumbent.release_id, variant_id=variant, report=comparison
            )
            annotation = _outcome(connection, candidate, context, actor, canary=False)
            canary = _outcome(
                connection, candidate, context, actor, canary=True, alternatives=False
            )
        # All adverse evidence is committed, scoped and uniquely selectable. Reject
        # for its specific quality gate, not an absent report, stale digest or ambiguity.
        original = snapshot(engine, release.project_id)
        journals = qualification_journals(engine)
        arguments = dict(
            **scope,
            model_key=candidate.release_id,
            annotation_release_key=annotation.release_key,
            canary_release_key=canary.release_key,
            expected_revision=original[0][0 if release.policy.imgsz == 320 else 1].revision,
        )
        with engine.begin() as connection:
            with pytest.raises(ValueError, match=message):
                promote_candidate(
                    connection, **arguments, actor_id=actor, require_fixture_policy=True
                )
        with authenticated_client(engine) as (client, identities, _workers):
            headers = login(client, identities["admin"][1])
            response = client.post(
                f"/projects/{release.project_id}/promotions",
                headers=headers,
                json={key: value for key, value in arguments.items() if key != "project_key"}
                | {"action": "promote"},
            )
            assert response.status_code == 409
            assert response.json() == {"detail": "registry_change_rejected"}
            assert response.headers["cache-control"] == "no-store"
        assert snapshot(engine, release.project_id) == original
        assert qualification_journals(engine) == journals
    return len(cases)


def exercise_durable_http_promotion(engine, release, final, context, actor):
    from qualification_context_smoke import _outcome, prepare_sole_final_candidate

    from labelloop_api.model_store import load_registry, save_evaluation
    from labelloop_api.qualification_context_store import (
        AnnotationOutcomeRef,
        load_qualification_context,
    )
    from labelloop_core.qualification import QualificationContext
    from labelloop_core.qualification_wire import loads
    from labelloop_core.registry import PromotionPolicy

    quality_vetoes = exercise_http_quality_vetoes(engine, release, final, context, actor)
    scope = dict(project_key=release.project_id, recipe_key=release.recipe_id)
    with engine.begin() as connection:
        candidate, report, variant = prepare_sole_final_candidate(
            connection, release, final, context, actor, suffix="-durable-http"
        )
        incumbent = load_registry(connection, **scope).champion
        assert incumbent is not None
        comparison = replace(
            report,
            run_id=report.run_id + "-incumbent",
            weights_sha256=incumbent.weights_sha256,
            class_map_sha256=incumbent.class_map_sha256,
            policy=incumbent.policy,
        )
        save_evaluation(
            connection, model_key=incumbent.release_id, variant_id=variant, report=comparison
        )
        annotation = _outcome(connection, candidate, context, actor, canary=False)
        canary = _outcome(connection, candidate, context, actor, canary=True, alternatives=False)
    # No borrowed connection or outer savepoint: setup, requests and verification
    # commit independently. Retain the original model's complete report history.
    original = snapshot(engine, release.project_id)
    journals = qualification_journals(engine)
    index = 0 if release.policy.imgsz == 320 else 1
    before = original[0][index]
    expected = RegistryState(
        release.project_id, release.recipe_id, before.revision + 1, candidate, before.champion
    )
    payload = dict(
        action="promote",
        recipe_key=release.recipe_id,
        model_key=candidate.release_id,
        annotation_release_key=annotation.release_key,
        canary_release_key=canary.release_key,
        expected_revision=before.revision,
    )
    path = f"/projects/{release.project_id}/promotions"
    with engine.begin() as connection:
        qualified = load_qualification_context(
            connection,
            **scope,
            model_key=candidate.release_id,
            qualification_key="durable-http-preflight",
            annotation=AnnotationOutcomeRef(annotation.release_key),
            canary=AnnotationOutcomeRef(canary.release_key),
        )
        qualified.require_qualified(candidate, report, comparison, PromotionPolicy(1, 0.0))

    def reject_failed_event(client, headers, body, captured):
        injected = []

        def fail_event(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("INSERT INTO promotion_event"):
                injected.append(True)
                raise sa.exc.SQLAlchemyError("private durable promotion fixture failure")

        sa.event.listen(engine, "before_cursor_execute", fail_event)
        try:
            response = client.post(path, headers=headers, json=body)
        finally:
            sa.event.remove(engine, "before_cursor_execute", fail_event)
        assert injected == [True]
        assert response.status_code == 503
        assert response.json() == {"detail": "registry_unavailable"}
        assert response.headers["cache-control"] == "no-store"
        assert snapshot(engine, release.project_id) == captured
        assert qualification_journals(engine) == journals

    with authenticated_client(engine) as (client, identities, _workers):
        headers = login(client, identities["admin"][1])
        reject_failed_event(client, headers, payload, original)
        response = client.post(path, headers=headers, json=payload)
        assert response.status_code == 200
        assert response.json() == json_record(expected)
        assert response.headers["cache-control"] == "no-store"
        promotion_actor = identities["admin"][0]
    # Close the client as well as every request transaction before reading back.
    promoted = snapshot(engine, release.project_id)
    assert promoted[0][index] == expected
    assert promoted[0][1 - index] == original[0][1 - index]
    assert len(promoted[1]) == len(original[1]) + 1
    assert all(row in promoted[1] for row in original[1])
    event = next(row for row in promoted[1] if row not in original[1])
    assert event["actor_id"] == promotion_actor and event["action"] == "promote"
    assert event["record"]["after"] == json_record(expected)
    assert event["record"]["evaluation_key"] == report.run_id
    assert event["record"]["incumbent_evaluation_key"] == comparison.run_id
    persisted = loads(json.dumps(event["record"]["qualification"]), QualificationContext)
    persisted.require_qualified(candidate, report, comparison, PromotionPolicy(1, 0.0))
    assert qualification_journals(engine) == journals
    with authenticated_client(engine) as (client, identities, _workers):
        headers = login(client, identities["admin"][1])
        for revision in (before.revision, expected.revision):
            response = client.post(
                path, headers=headers, json=payload | {"expected_revision": revision}
            )
            assert response.status_code == 409
            assert response.json() == {"detail": "registry_change_rejected"}
            assert snapshot(engine, release.project_id) == promoted
        rollback = dict(
            action="rollback", recipe_key=release.recipe_id, expected_revision=expected.revision
        )
        reject_failed_event(client, headers, rollback, promoted)
        restored = expected.roll_back(expected_revision=expected.revision)
        response = client.post(path, headers=headers, json=rollback)
        assert response.status_code == 200
        assert response.json() == json_record(restored)
        assert response.headers["cache-control"] == "no-store"
        rollback_actor = identities["admin"][0]
        response = client.post(path, headers=headers, json=rollback)
        assert response.status_code == 409
        assert response.json() == {"detail": "registry_change_rejected"}
    recovered = snapshot(engine, release.project_id)
    assert recovered[0][index] == restored
    assert recovered[0][1 - index] == original[0][1 - index]
    assert restored.champion == before.champion and restored.rollback == candidate
    assert len(recovered[1]) == len(promoted[1]) + 1
    assert all(row in recovered[1] for row in promoted[1])
    event = next(row for row in recovered[1] if row not in promoted[1])
    assert event["actor_id"] == rollback_actor and event["action"] == "rollback"
    assert event["record"]["after"] == json_record(restored)
    assert qualification_journals(engine) == journals
    return {
        "scope": "fixture-only copied metrics, not independent measurements or domain quality",
        "committed_http_support_and_critical_slice_veto_cases": quality_vetoes,
        "committed_setup_promotion_and_rollback_recovered_after_client_close": True,
        "complete_metadata_principal_and_selected_qualification_recovered": True,
        "failed_promotion_and_rollback_events_preserved_both_heads_and_journals": True,
        "stale_and_already_champion_replays_preserved_persisted_history": True,
        "other_recipe_and_qualification_history_unchanged": True,
    }
