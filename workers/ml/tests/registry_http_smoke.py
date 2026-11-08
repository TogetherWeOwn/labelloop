"""Real session/HTTP/registry transactions on Actions' disposable PostgreSQL only."""

import json
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from labelloop_api import auth, model_schema, qualification_schema, registry_api
from labelloop_api.acceptance_store import load_acceptance
from labelloop_api.auth_schema import principal, service_token
from labelloop_api.auth_store import PASSWORDS, AuthStore, digest
from labelloop_api.final_test_store import load_final_test
from labelloop_api.main import app
from labelloop_api.model_store import json_record, load_registry
from labelloop_core.qualification import QualificationContext
from labelloop_core.qualification_data import record_sha256
from labelloop_core.qualification_wire import dumps, loads

ORIGIN = "https://testserver"
PASSWORD = "fixture-password-only"


@contextmanager
def authenticated_client(engine):
    store = AuthStore(engine)
    identities = {}
    password_hash = PASSWORDS.hash(PASSWORD)
    workers = {}
    with engine.begin() as connection:
        for role in (
            "admin",
            "reviewer",
            "worker-core",
            "worker-ml",
            "worker-llm",
            "worker-platform",
        ):
            actor_id = str(uuid4())
            name = "registry-fixture-" + actor_id
            identities[role] = (actor_id, name)
            human = role in ("admin", "reviewer")
            connection.execute(
                sa.insert(principal).values(
                    id=actor_id,
                    name=name,
                    kind="human" if human else "service",
                    role=role,
                    password_hash=password_hash if human else None,
                    enabled=True,
                    failed_attempts=0,
                )
            )
            if not human:
                token = secrets.token_urlsafe(32)
                workers[role] = token
                connection.execute(
                    sa.insert(service_token).values(
                        token_hash=digest(token),
                        principal_id=actor_id,
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                    )
                )
    # Replace connection configuration only. Middleware, sessions, CSRF, permissions,
    # registry reads/writes, savepoints and commit/rollback all use production code.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(auth, "auth_store", lambda: store)
        patch.setattr(registry_api, "auth_store", lambda: store)
        patch.setenv("AUTH_ORIGIN", ORIGIN)
        with TestClient(app, base_url=ORIGIN) as client:
            yield client, identities, workers


def login(client, name):
    response = client.post(
        "/auth/login",
        headers={"origin": ORIGIN},
        json={"name": name, "password": PASSWORD},
    )
    assert response.status_code == 200
    return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}


def snapshot(engine, project_key):
    with engine.connect() as connection:
        states = tuple(
            load_registry(connection, project_key, f"full-letterbox-{side}-v1")
            for side in (320, 640)
        )
        events = tuple(
            dict(row)
            for row in connection.execute(
                sa.select(model_schema.promotion_event).order_by(model_schema.promotion_event.c.id)
            ).mappings()
        )
    return states, events


def assert_operations_read(client, engine, expected):
    scope = {"project_key": expected.project_id, "recipe_key": expected.recipe_id}
    with engine.begin() as connection:
        acceptance = load_acceptance(connection, **scope)
    before = snapshot(engine, expected.project_id)
    response = client.get(f"/projects/{expected.project_id}/registry/{expected.recipe_id}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "registry": json_record(expected),
        "acceptance": json_record(acceptance),
    }
    assert snapshot(engine, expected.project_id) == before
    with engine.begin() as connection:
        assert load_acceptance(connection, **scope) == acceptance
    return acceptance


def exercise_http_final_invalidation(engine, project_key, dataset_key, action, reason, revision):
    path = f"/projects/{project_key}/dataset-releases/{dataset_key}/final-test-events"
    scope = {"project_key": project_key, "dataset_key": dataset_key}
    payload = {"action": action, "reason": reason, "expected_revision": revision}

    def journal_rows():
        with engine.connect() as connection:
            return tuple(
                tuple(
                    dict(row)
                    for row in connection.execute(
                        sa.select(table).order_by(*table.primary_key.columns)
                    ).mappings()
                )
                for table in (
                    qualification_schema.final_test_event,
                    qualification_schema.acceptance_event,
                )
            )

    with engine.begin() as connection:
        original = load_final_test(connection, **scope)
    before = journal_rows()
    registry_before = snapshot(engine, project_key)
    transition = original.record_adaptation if action == "adapt" else original.quarantine
    expected = transition(reason, expected_revision=revision)
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
            {"dataset": {}},
            {"role": "locked_final_test"},
            {"action": "reset"},
            {"group_ids": ["invented"]},
        ):
            response = client.post(path, headers=headers, json=payload | extra)
            assert response.status_code == 422
            assert response.json() == {"detail": "invalid_request"}
        for target, change in (
            (path, {"expected_revision": revision - 1}),
            (
                f"/projects/missing-http-project/dataset-releases/{dataset_key}/final-test-events",
                {},
            ),
            (f"/projects/{project_key}/dataset-releases/missing-http-final/final-test-events", {}),
        ):
            response = client.post(target, headers=headers, json=payload | change)
            assert response.status_code == (409 if change else 404)
        assert journal_rows() == before
        assert snapshot(engine, project_key) == registry_before

        injected = []

        def fail_after_insert(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("INSERT INTO qualification_final_test_event"):
                injected.append(True)
                raise sa.exc.SQLAlchemyError("private fixture invalidation failure")

        sa.event.listen(engine, "after_cursor_execute", fail_after_insert)
        try:
            response = client.post(path, headers=headers, json=payload)
        finally:
            sa.event.remove(engine, "after_cursor_execute", fail_after_insert)
        assert injected == [True]
        assert response.status_code == 503
        assert response.json() == {"detail": "final_test_unavailable"}
        assert journal_rows() == before
        with engine.begin() as connection:
            assert load_final_test(connection, **scope) == original

        response = client.post(path, headers=headers, json=payload)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "release_key": dataset_key,
            "revision": expected.revision,
            "effective_role": expected.effective_role,
            "quarantined": bool(expected.quarantine_reasons),
        }
        with engine.begin() as connection:
            assert load_final_test(connection, **scope) == expected
        after = journal_rows()
        assert after[1] == before[1]
        assert len(after[0]) == len(before[0]) + 1
        assert all(row in after[0] for row in before[0])
        added = [row for row in after[0] if row not in before[0]]
        assert len(added) == 1
        assert added[0]["actor_id"] == identities["admin"][0]
        assert added[0]["action"] == action
        assert added[0]["record"] == json.loads(dumps(expected))
        assert expected.dataset == original.dataset
        assert expected.assessments == original.assessments
        for change in ({}, {"expected_revision": expected.revision}):
            # Neither a stale retry nor a duplicate reason can append another event.
            assert client.post(path, headers=headers, json=payload | change).status_code == 409
            assert journal_rows() == after
        assert snapshot(engine, project_key) == registry_before
    return expected


def reject_http_resume(engine, registry, history, pause_ids):
    path = f"/projects/{registry.project_id}/registry/{registry.recipe_id}/resumptions"
    scope = {"project_key": registry.project_id, "recipe_key": registry.recipe_id}
    heads = snapshot(engine, registry.project_id)
    with authenticated_client(engine) as (client, identities, _):
        response = client.post(
            path,
            headers=login(client, identities["admin"][1]),
            json={
                "expected_revision": history.revision,
                "expected_registry_revision": registry.revision,
                "pause_ids": list(pause_ids),
            },
        )
        assert response.status_code == 409
        assert response.json() == {"detail": "drift_resume_rejected"}
    with engine.begin() as connection:
        assert load_acceptance(connection, **scope) == history
    assert snapshot(engine, registry.project_id) == heads


def exercise_http_resume(engine, registry, history, pause_ids, qualification):
    path = f"/projects/{registry.project_id}/registry/{registry.recipe_id}/resumptions"
    scope = {"project_key": registry.project_id, "recipe_key": registry.recipe_id}
    payload = {
        "expected_revision": history.revision,
        "expected_registry_revision": registry.revision,
        "pause_ids": list(pause_ids),
    }

    def journal_rows():
        with engine.connect() as connection:
            return tuple(
                dict(row)
                for row in connection.execute(
                    sa.select(qualification_schema.acceptance_event).order_by(
                        *qualification_schema.acceptance_event.primary_key.columns
                    )
                ).mappings()
            )

    before = journal_rows()
    heads = snapshot(engine, registry.project_id)
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
            {"qualification": {}},
            {"evaluation_key": "invented"},
            {"actor_id": "forged"},
            {"candidate_key": "invented"},
        ):
            assert client.post(path, headers=headers, json=payload | extra).status_code == 422
        for change in (
            {"expected_revision": history.revision - 1},
            {"expected_registry_revision": registry.revision - 1},
            {"pause_ids": ["missing-http-incident"]},
            {"pause_ids": [pause_ids[0], pause_ids[0]]},
        ):
            response = client.post(path, headers=headers, json=payload | change)
            assert response.status_code == 409
            assert response.json() == {"detail": "drift_resume_rejected"}
        for missing in (
            f"/projects/missing-http-project/registry/{registry.recipe_id}/resumptions",
            f"/projects/{registry.project_id}/registry/missing-http-recipe/resumptions",
        ):
            assert client.post(missing, headers=headers, json=payload).status_code == 404
        assert journal_rows() == before
        assert snapshot(engine, registry.project_id) == heads
        injected = []

        def fail_after_insert(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("INSERT INTO qualification_acceptance_event"):
                injected.append(True)
                raise sa.exc.SQLAlchemyError("private fixture resumption failure")

        sa.event.listen(engine, "after_cursor_execute", fail_after_insert)
        try:
            response = client.post(path, headers=headers, json=payload)
        finally:
            sa.event.remove(engine, "after_cursor_execute", fail_after_insert)
        assert injected == [True]
        assert response.status_code == 503
        assert response.json() == {"detail": "drift_unavailable"}
        assert journal_rows() == before
        with engine.begin() as connection:
            assert load_acceptance(connection, **scope) == history
        response = client.post(path, headers=headers, json=payload)
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
        with engine.begin() as connection:
            changed = load_acceptance(connection, **scope)
        assert response.json() == json_record(changed)
        assert changed.revision == history.revision + 1
        assert all(
            item.resumed_by_release_id == registry.champion.release_id
            for item in changed.pauses
            if item.pause_id in pause_ids
        )
        after = journal_rows()
        assert len(after) == len(before) + 1
        assert all(row in after for row in before)
        added = [row for row in after if row not in before]
        assert len(added) == 1
        assert added[0]["actor_id"] == identities["admin"][0]
        assert added[0]["action"] == "resume"
        assert added[0]["qualification"] == json.loads(dumps(qualification))
        assert added[0]["record"] == json.loads(dumps(changed))
        for change in ({}, {"expected_revision": changed.revision}):
            assert client.post(path, headers=headers, json=payload | change).status_code == 409
            assert journal_rows() == after
        assert snapshot(engine, registry.project_id) == heads
    return changed


def exercise_http_pause(engine, registry, history):
    path = f"/projects/{registry.project_id}/registry/{registry.recipe_id}/pauses"
    cell = history.pauses[0].scope[0]
    payload = {
        "expected_revision": history.revision,
        "expected_registry_revision": registry.revision,
        "class_id": cell.class_id,
        "source_id": cell.source_id,
        "condition": cell.condition,
        "reason": "synthetic scoped HTTP containment control",
    }
    scope = {"project_key": registry.project_id, "recipe_key": registry.recipe_id}

    def journal_rows():
        with engine.connect() as connection:
            return tuple(
                dict(row)
                for row in connection.execute(
                    sa.select(qualification_schema.acceptance_event).order_by(
                        *qualification_schema.acceptance_event.primary_key.columns
                    )
                ).mappings()
            )

    before = journal_rows()
    heads = snapshot(engine, registry.project_id)
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
            {"qualification_id": "invented"},
            {"scope": []},
            {"action": "resume"},
        ):
            assert client.post(path, headers=headers, json=payload | extra).status_code == 422
        for change in (
            {"expected_revision": history.revision - 1},
            {"expected_registry_revision": registry.revision - 1},
            {"class_id": "missing-http-class"},
            {"source_id": "missing-http-source"},
            {"condition": "missing-http-condition"},
        ):
            assert client.post(path, headers=headers, json=payload | change).status_code == 409
        for missing in (
            f"/projects/missing-http-project/registry/{registry.recipe_id}/pauses",
            f"/projects/{registry.project_id}/registry/missing-http-recipe/pauses",
        ):
            assert client.post(missing, headers=headers, json=payload).status_code == 404
        assert journal_rows() == before
        assert snapshot(engine, registry.project_id) == heads
        injected = []

        def fail_after_insert(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("INSERT INTO qualification_acceptance_event"):
                injected.append(True)
                raise sa.exc.SQLAlchemyError("private fixture pause failure")

        sa.event.listen(engine, "after_cursor_execute", fail_after_insert)
        try:
            response = client.post(path, headers=headers, json=payload)
        finally:
            sa.event.remove(engine, "after_cursor_execute", fail_after_insert)
        assert injected == [True]
        assert response.status_code == 503
        assert response.json() == {"detail": "drift_unavailable"}
        assert journal_rows() == before
        with engine.begin() as connection:
            assert load_acceptance(connection, **scope) == history
        response = client.post(path, headers=headers, json=payload)
        assert response.status_code == 201
        assert response.headers["cache-control"] == "no-store"
        with engine.begin() as connection:
            changed = load_acceptance(connection, **scope)
        assert response.json() == json_record(changed)
        assert changed.revision == history.revision + 1
        assert changed.pauses[:-1] == history.pauses
        pause = changed.pauses[-1]
        assert pause.scope == (cell,)
        assert changed.is_paused(cell)
        assert pause.release_id == registry.champion.release_id
        assert pause.release_sha256 == record_sha256(registry.champion)
        assert pause.reason == payload["reason"]
        assert pause.resumed_by_qualification_id is None
        promotion = max(
            (
                row
                for row in heads[1]
                if row["action"] == "promote"
                and row["record"]["after"]["project_id"] == registry.project_id
                and row["record"]["after"]["recipe_id"] == registry.recipe_id
                and row["record"]["after"]["champion"]["release_id"] == pause.release_id
            ),
            key=lambda row: row["revision"],
        )
        original = loads(json.dumps(promotion["record"]["qualification"]), QualificationContext)
        assert pause.qualification_id == original.evidence.qualification_id
        for other in original.evidence.scope:
            if other != cell:
                assert changed.is_paused(other) == history.is_paused(other)
        after = journal_rows()
        assert len(after) == len(before) + 1
        assert all(row in after for row in before)
        added = [row for row in after if row not in before]
        assert len(added) == 1
        assert added[0]["actor_id"] == identities["admin"][0]
        assert added[0]["action"] == "pause"
        assert added[0]["qualification"] is None
        assert added[0]["record"] == json.loads(dumps(changed))
        assert client.post(path, headers=headers, json=payload).status_code == 409
        assert journal_rows() == after
        assert snapshot(engine, registry.project_id) == heads
    return changed


def exercise_http_rollback(engine, request):
    path = f"/projects/{request.project_id}/promotions"
    read_path = f"/projects/{request.project_id}/registry/{request.recipe_id}"
    before = snapshot(engine, request.project_id)
    index = 0 if request.policy.imgsz == 320 else 1
    original = before[0][index]
    payload = {
        "action": "rollback",
        "recipe_key": request.recipe_id,
        "expected_revision": original.revision,
    }
    with authenticated_client(engine) as (client, identities, workers):
        assert client.post(path, json=payload).status_code == 401
        assert client.get(read_path).status_code == 401
        reviewer = login(client, identities["reviewer"][1])
        assert client.post(path, headers=reviewer, json=payload).status_code == 403
        assert client.get(read_path + "?role=admin").status_code == 403
        client.cookies.clear()
        for token in workers.values():
            assert (
                client.post(
                    path, headers={"authorization": "Bearer " + token}, json=payload
                ).status_code
                == 403
            )
            assert (
                client.get(read_path, headers={"authorization": "Bearer " + token}).status_code
                == 403
            )
        headers = login(client, identities["admin"][1])
        history = assert_operations_read(client, engine, original)
        assert history.pauses and all(
            item.resumed_by_release_id is not None for item in history.pauses
        )
        for missing in (
            f"/projects/missing-http-project/registry/{request.recipe_id}",
            f"/projects/{request.project_id}/registry/missing-http-recipe",
        ):
            assert client.get(missing).status_code == 404
        for denied in (
            {},
            headers | {"origin": "https://untrusted.invalid"},
            headers | {"x-csrf-token": "wrong"},
        ):
            assert client.post(path, headers=denied, json=payload).status_code == 403
        for extra in (
            {"candidate_key": "invented"},
            {"qualification": {}},
            {"actor_id": "forged"},
            {"action": "promote"},
        ):
            response = client.post(path, headers=headers, json=payload | extra)
            assert response.status_code == 422
            assert response.json() == {"detail": "invalid_request"}
        for target, change in (
            (path, {"expected_revision": original.revision - 1}),
            ("/projects/missing-http-project/promotions", {}),
            (path, {"recipe_key": "missing-http-recipe"}),
        ):
            response = client.post(target, headers=headers, json=payload | change)
            assert response.status_code == (409 if "expected_revision" in change else 404)
        assert snapshot(engine, request.project_id) == before

        injected = []

        def fail_event(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("INSERT INTO promotion_event"):
                injected.append(True)
                raise sa.exc.SQLAlchemyError("fixture event append failure")

        sa.event.listen(engine, "before_cursor_execute", fail_event)
        try:
            response = client.post(path, headers=headers, json=payload)
        finally:
            sa.event.remove(engine, "before_cursor_execute", fail_event)
        assert injected == [True]
        assert response.status_code == 503
        assert response.json() == {"detail": "registry_unavailable"}
        assert snapshot(engine, request.project_id) == before

        current = original
        for offset in (1, 2):
            response = client.post(
                path, headers=headers, json=payload | {"expected_revision": current.revision}
            )
            expected = current.roll_back(expected_revision=current.revision)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.json() == json_record(expected)
            assert assert_operations_read(client, engine, expected) == history
            after = snapshot(engine, request.project_id)
            assert after[0][index] == expected
            assert after[0][1 - index] == before[0][1 - index]
            assert len(after[1]) == len(before[1]) + offset
            added = [row for row in after[1] if row["id"] not in {old["id"] for old in before[1]}]
            event = next(row for row in added if row["revision"] == expected.revision)
            assert event["actor_id"] == identities["admin"][0]
            assert event["action"] == "rollback"
            assert event["record"] == {
                "before": json_record(current),
                "after": json_record(expected),
                "policy": None,
                "evaluation_key": None,
                "incumbent_evaluation_key": None,
                "qualification": None,
            }
            replay = client.post(
                path, headers=headers, json=payload | {"expected_revision": current.revision}
            )
            assert replay.status_code == 409
            assert snapshot(engine, request.project_id) == after
            current = expected
        assert current.champion == original.champion
        assert current.rollback == original.rollback
        paused = exercise_http_pause(engine, current, history)
        assert assert_operations_read(client, engine, current) == paused
        assert paused.is_paused(history.pauses[0].scope[0])

        injected_reads = []

        def fail_read(_conn, _cursor, statement, _parameters, _context, _many):
            if "FROM qualification_acceptance_event" in statement:
                injected_reads.append(True)
                raise sa.exc.SQLAlchemyError("private fixture read failure")

        sa.event.listen(engine, "before_cursor_execute", fail_read)
        try:
            response = client.get(read_path)
        finally:
            sa.event.remove(engine, "before_cursor_execute", fail_read)
        assert injected_reads == [True]
        assert response.status_code == 503
        assert response.json() == {"detail": "registry_unavailable"}
        assert assert_operations_read(client, engine, current) == paused
    return {
        "scope": "fixture-only HTTP recovery mechanics, not domain quality",
        "before": json_record(original),
        "after": json_record(current),
        "authenticated_actor_and_complete_event": True,
        "denials_and_stale_replays_unchanged": True,
        "event_failure_rolled_back_pointer": True,
        "other_recipe_unchanged": True,
        "authenticated_registry_and_drift_reads": True,
        "resumed_and_active_drift_history_recovered": True,
        "read_failure_redacted_without_partial_state": True,
    }


def exercise_http_audit_write(engine, request, sample_key, positive, negative):
    path = f"/projects/{request.project_id}/dataset-releases/{request.dataset_release_id}/audits"
    observation = {
        "asset_id": positive,
        "revision": 1,
        "blind_full_image_inspected": True,
        "labels_correct": False,
        "scope_complete": True,
    }
    payload = {
        "audit_key": "wrong-identity",
        "sample_key": sample_key,
        "observations": [observation],
    }

    def audit_snapshot():
        with engine.connect() as connection:
            return tuple(
                dict(row)
                for row in connection.execute(
                    sa.select(qualification_schema.annotation_audit).order_by(
                        qualification_schema.annotation_audit.c.id
                    )
                ).mappings()
            )

    before = audit_snapshot()
    registry_before = snapshot(engine, request.project_id)
    with authenticated_client(engine) as (client, identities, workers):
        assert client.post(path, json=payload).status_code == 401
        for token in workers.values():
            assert (
                client.post(
                    path, headers={"authorization": "Bearer " + token}, json=payload
                ).status_code
                == 403
            )
        headers = login(client, identities["reviewer"][1])
        for denied in (
            {},
            headers | {"origin": "https://untrusted.invalid"},
            headers | {"x-csrf-token": "wrong"},
        ):
            assert client.post(path, headers=denied, json=payload).status_code == 403
        for extra in ({"actor_id": "forged"}, {"passed": True}, {"qualification": {}}):
            assert client.post(path, headers=headers, json=payload | extra).status_code == 422
        for change, status in (
            ({"sample_key": "missing-http-sample"}, 404),
            ({"observations": [observation | {"revision": 2}]}, 409),
            ({"observations": [observation | {"asset_id": "foreign-http-asset"}]}, 409),
            ({"observations": [observation, observation]}, 409),
            ({"observations": [observation | {"reviewer_id": "forged"}]}, 422),
        ):
            assert client.post(path, headers=headers, json=payload | change).status_code == status
        missing = (
            f"/projects/missing-http-project/dataset-releases/{request.dataset_release_id}/audits"
        )
        assert client.post(missing, headers=headers, json=payload).status_code == 404
        assert audit_snapshot() == before

        injected = []

        def fail_after_insert(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("INSERT INTO qualification_annotation_audit"):
                injected.append(True)
                raise sa.exc.SQLAlchemyError("private fixture post-insert failure")

        sa.event.listen(engine, "after_cursor_execute", fail_after_insert)
        try:
            response = client.post(path, headers=headers, json=payload)
        finally:
            sa.event.remove(engine, "after_cursor_execute", fail_after_insert)
        assert injected == [True]
        assert response.status_code == 503
        assert response.json() == {"detail": "audit_unavailable"}
        assert audit_snapshot() == before
        for key, item in (
            ("wrong-identity", observation),
            (
                "missed-object",
                observation
                | {"asset_id": negative, "labels_correct": True, "scope_complete": False},
            ),
        ):
            body = payload | {"audit_key": key, "observations": [item]}
            response = client.post(path, headers=headers, json=body)
            assert response.status_code == 201
            assert response.json() == {"audit_key": key, "recorded": True}
            assert response.headers["cache-control"] == "no-store"
            saved = audit_snapshot()
            replay = client.post(path, headers=headers, json=body)
            assert replay.status_code == 409
            assert replay.json() == {"detail": "audit_rejected"}
            assert audit_snapshot() == saved
        actor = identities["reviewer"][0]
    assert snapshot(engine, request.project_id) == registry_before
    with engine.begin() as connection:
        from labelloop_api.annotation_audit_store import load_annotation_audit

        for key, asset in (("wrong-identity", positive), ("missed-object", negative)):
            stored = load_annotation_audit(
                connection,
                project_key=request.project_id,
                release_key=request.dataset_release_id,
                audit_key=key,
            )
            assert len(stored.observations) == 1
            assert stored.observations[0].reviewer_id == actor
            assert stored.verdict.blocking_asset_ids == (asset,)
            assert not stored.verdict.passed
    return actor


def exercise_http_audit_veto(engine, request):
    before = snapshot(engine, request.project_id)
    checked = []
    with authenticated_client(engine) as (client, identities, _workers):
        headers = login(client, identities["admin"][1])
        for state in before[0]:
            if state.champion is None:
                continue  # A permitted focused reproduction may run only the 640 recipe.
            assert state.rollback is not None
            response = client.post(
                f"/projects/{request.project_id}/promotions",
                headers=headers,
                json={
                    "action": "rollback",
                    "recipe_key": state.recipe_id,
                    "expected_revision": state.revision,
                },
            )
            assert response.status_code == 409
            assert response.json() == {"detail": "registry_change_rejected"}
            assert snapshot(engine, request.project_id) == before
            checked.append(state.recipe_id)
    assert request.recipe_id in checked
    return {"vetoed_recipes": checked, "complete_heads_and_events_unchanged": True}
