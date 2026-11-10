"""Resumption accepts incident selectors, never caller qualification or release grants."""

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
from labelloop_core.drift import AcceptanceState

PATH = "/projects/synthetic-qualification/registry/full-letterbox-320-v1/resumptions"
PAYLOAD = {"expected_revision": 2, "expected_registry_revision": 7, "pause_ids": ["layout"]}


@pytest.fixture
def http(monkeypatch, store):
    calls = []
    monkeypatch.setattr(registry_api, "auth_store", lambda: store)

    def resume(connection, **kwargs):
        assert connection.in_transaction()
        calls.append(kwargs)
        return AcceptanceState(kwargs["project_key"], kwargs["recipe_key"])

    monkeypatch.setattr(registry_api.drift_service, "resume_current_champion", resume)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, calls


def admin_headers(client):
    csrf = login(client, "admin").json()["csrf_token"]
    return {"origin": ORIGIN, "x-csrf-token": csrf}


@pytest.mark.parametrize("side", [320, 640])
def test_resume_uses_session_actor_and_immutable_incident_selectors(http, store, side):
    client, calls = http
    response = client.post(
        PATH.replace("320", str(side)), headers=admin_headers(client), json=PAYLOAD
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "project_id": "synthetic-qualification",
        "recipe_id": f"full-letterbox-{side}-v1",
        "revision": 0,
        "pauses": [],
    }
    with store.engine.connect() as connection:
        actor = connection.execute(
            select(principal.c.id).where(principal.c.name == "admin")
        ).scalar_one()
    assert calls == [
        dict(
            project_key="synthetic-qualification",
            recipe_key=f"full-letterbox-{side}-v1",
            actor_id=actor,
            **(PAYLOAD | {"pause_ids": ("layout",)}),
        )
    ]


def test_anonymous_and_reviewer_cannot_resume(http):
    client, calls = http
    assert client.post(PATH, json=PAYLOAD).status_code == 401
    csrf = login(client).json()["csrf_token"]
    assert (
        client.post(
            PATH, headers={"origin": ORIGIN, "x-csrf-token": csrf}, json=PAYLOAD
        ).status_code
        == 403
    )
    assert calls == []


@pytest.mark.parametrize("worker", ["worker-core", "worker-ml", "worker-llm", "worker-platform"])
def test_services_cannot_resume(http, store, worker):
    client, calls = http
    assert (
        client.post(
            PATH, headers={"authorization": "Bearer " + worker_token(store, worker)}, json=PAYLOAD
        ).status_code
        == 403
    )
    assert calls == []


@pytest.mark.parametrize(
    "headers", [{}, {"origin": "https://untrusted.invalid"}, {"x-csrf-token": "wrong"}]
)
def test_resume_requires_same_origin_and_csrf(http, headers):
    client, calls = http
    admin_headers(client)
    assert client.post(PATH, headers=headers, json=PAYLOAD).status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"actor_id": "forged"},
        {"qualification": {}},
        {"evaluation_key": "invented"},
        {"candidate_key": "invented"},
        {"scope": []},
        {"qualification_id": "invented"},
        {"expected_revision": True},
        {"expected_revision": -1},
        {"expected_revision": "2"},
        {"expected_registry_revision": True},
        {"expected_registry_revision": -1},
        {"pause_ids": []},
        {"pause_ids": [" "]},
        {"pause_ids": ["x" * 129]},
        {"pause_ids": "layout"},
        {"pause_ids": ["layout"] * 101},
    ],
)
def test_resume_rejects_authority_fields_and_invalid_selectors(http, change):
    client, calls = http
    response = client.post(PATH, headers=admin_headers(client), json=PAYLOAD | change)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert calls == []


@pytest.mark.parametrize(
    "error,status,detail",
    [
        (ValueError("private stale revision"), 409, "drift_resume_rejected"),
        (KeyError("private evidence"), 409, "drift_resume_rejected"),
        (NoResultFound("private missing scope"), 404, "drift_scope_not_found"),
        (SQLAlchemyError("private database context"), 503, "drift_unavailable"),
    ],
)
def test_resume_failures_redact_and_rollback_outer_transaction(
    http, store, monkeypatch, error, status, detail
):
    client, _ = http
    headers = admin_headers(client)
    probe_id = str(uuid4())

    def fail(connection, **kwargs):
        connection.execute(
            insert(auth_event).values(
                id=probe_id, principal_id=kwargs["actor_id"], occurred_at=NOW, action="logout"
            )
        )
        raise error

    monkeypatch.setattr(registry_api.drift_service, "resume_current_champion", fail)
    response = client.post(PATH, headers=headers, json=PAYLOAD)
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    with store.engine.connect() as connection:
        assert (
            connection.execute(select(auth_event.c.id).where(auth_event.c.id == probe_id)).all()
            == []
        )
