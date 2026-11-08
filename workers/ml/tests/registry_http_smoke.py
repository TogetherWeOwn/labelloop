"""Real session/HTTP/registry transactions on Actions' disposable PostgreSQL only."""

import secrets
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from labelloop_api import auth, model_schema, registry_api
from labelloop_api.auth_schema import principal, service_token
from labelloop_api.auth_store import PASSWORDS, AuthStore, digest
from labelloop_api.main import app
from labelloop_api.model_store import json_record, load_registry

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


def exercise_http_rollback(engine, request):
    path = f"/projects/{request.project_id}/promotions"
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
    return {
        "scope": "fixture-only HTTP recovery mechanics, not domain quality",
        "before": json_record(original),
        "after": json_record(current),
        "authenticated_actor_and_complete_event": True,
        "denials_and_stale_replays_unchanged": True,
        "event_failure_rolled_back_pointer": True,
        "other_recipe_unchanged": True,
    }


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
