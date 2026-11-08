"""Authenticated invalidation adapter; persisted transitions run in Actions ML smoke."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.exc import NoResultFound, SQLAlchemyError
from test_auth import NOW, ORIGIN, login, worker_token
from test_auth import password_hash as password_hash
from test_auth import store as store

from labelloop_api import registry_api
from labelloop_api.auth_schema import auth_event, principal
from labelloop_api.main import app

PATH = "/projects/synthetic-qualification/dataset-releases/fixture-final/final-test-events"
PAYLOAD = {"action": "adapt", "reason": "result guided later change", "expected_revision": 1}


@pytest.fixture
def http(monkeypatch, store):
    calls = []
    monkeypatch.setattr(registry_api, "auth_store", lambda: store)

    def invalidate(connection, **kwargs):
        assert connection.in_transaction()
        calls.append(kwargs)
        return SimpleNamespace(
            revision=kwargs["expected_revision"] + 1,
            effective_role="development" if kwargs["action"] == "adapt" else "locked_final_test",
            quarantine_reasons=(kwargs["reason"],) if kwargs["action"] == "quarantine" else (),
            assessments="not an invalidation response or a final-result read port",
        )

    monkeypatch.setattr(registry_api.final_test_store, "invalidate_final_test", invalidate)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, calls


def admin_headers(client):
    csrf = login(client, "admin").json()["csrf_token"]
    return {"origin": ORIGIN, "x-csrf-token": csrf}


@pytest.mark.parametrize("action", ["adapt", "quarantine"])
def test_admin_invalidates_whole_persisted_test_with_session_actor(http, store, action):
    client, calls = http
    response = client.post(PATH, headers=admin_headers(client), json=PAYLOAD | {"action": action})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "release_key": "fixture-final",
        "revision": 2,
        "effective_role": "development" if action == "adapt" else "locked_final_test",
        "quarantined": action == "quarantine",
    }
    with store.engine.connect() as connection:
        actor_id = connection.execute(
            select(principal.c.id).where(principal.c.name == "admin")
        ).scalar_one()
    assert calls == [
        {
            "project_key": "synthetic-qualification",
            "dataset_key": "fixture-final",
            "actor_id": actor_id,
            **(PAYLOAD | {"action": action}),
        }
    ]


def test_anonymous_cannot_invalidate_final_test(http):
    client, calls = http
    assert client.post(PATH, json=PAYLOAD).status_code == 401
    assert calls == []


def test_reviewer_cannot_invalidate_final_test(http):
    client, calls = http
    csrf = login(client).json()["csrf_token"]
    response = client.post(PATH, headers={"origin": ORIGIN, "x-csrf-token": csrf}, json=PAYLOAD)
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize("worker", ["worker-core", "worker-ml", "worker-llm", "worker-platform"])
def test_services_cannot_invalidate_final_test(http, store, worker):
    client, calls = http
    response = client.post(
        PATH,
        headers={"authorization": "Bearer " + worker_token(store, worker)},
        json=PAYLOAD,
    )
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    "headers", [{}, {"origin": "https://untrusted.invalid"}, {"x-csrf-token": "incorrect"}]
)
def test_final_test_invalidation_requires_same_origin_and_csrf(http, headers):
    client, calls = http
    admin_headers(client)
    assert client.post(PATH, headers=headers, json=PAYLOAD).status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"actor_id": "invented-admin"},
        {"qualification": {}},
        {"dataset": {}},
        {"role": "locked_final_test"},
        {"group_ids": ["invented"]},
        {"action": "reset"},
        {"action": "expose"},
        {"action": "register"},
        {"action": "resume"},
        {"expected_revision": -1},
        {"expected_revision": True},
        {"expected_revision": "1"},
        {"reason": ""},
        {"reason": " \t\n"},
        {"reason": "x" * 1001},
    ],
)
def test_no_caller_membership_reset_authority_or_invalid_revision(http, change):
    client, calls = http
    response = client.post(PATH, headers=admin_headers(client), json=PAYLOAD | change)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert calls == []


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (ValueError("stale final-test revision"), 409, "final_test_change_rejected"),
        (ValueError("unexposed final test"), 409, "final_test_change_rejected"),
        (ValueError("duplicate invalidation"), 409, "final_test_change_rejected"),
        (NoResultFound("foreign or missing scope"), 404, "final_test_scope_not_found"),
        (SQLAlchemyError("private driver context"), 503, "final_test_unavailable"),
    ],
)
def test_final_test_failure_is_redacted_and_rolls_back_outer_transaction(
    http, store, monkeypatch, error, status, detail
):
    client, _ = http
    headers = admin_headers(client)
    probe_id = str(uuid4())

    def fail(connection, **kwargs):
        connection.execute(
            insert(auth_event).values(
                id=probe_id,
                principal_id=kwargs["actor_id"],
                occurred_at=NOW,
                action="logout",
            )
        )
        raise error

    monkeypatch.setattr(registry_api.final_test_store, "invalidate_final_test", fail)
    response = client.post(PATH, headers=headers, json=PAYLOAD)
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    with store.engine.connect() as connection:
        assert (
            connection.execute(select(auth_event.c.id).where(auth_event.c.id == probe_id)).all()
            == []
        )
