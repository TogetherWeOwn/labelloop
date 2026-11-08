"""HTTP auth/transaction adapter controls; persisted rollback is covered by ML smoke."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound, SQLAlchemyError
from test_auth import ORIGIN, login, worker_token
from test_auth import password_hash as password_hash
from test_auth import store as store
from test_m5_integration_contract import make_release

from labelloop_api import registry_api
from labelloop_api.auth_schema import principal
from labelloop_api.main import app
from labelloop_api.model_store import json_record
from labelloop_core.registry import RegistryState

PATH = "/projects/synthetic-qualification/promotions"
PAYLOAD = {"action": "rollback", "recipe_key": "full-letterbox-320-v1", "expected_revision": 2}


@pytest.fixture
def http(monkeypatch, store):
    calls = []
    monkeypatch.setattr(registry_api, "auth_store", lambda: store)

    def change(connection, **kwargs):
        assert connection.in_transaction()
        calls.append(kwargs)
        side = 320 if kwargs["recipe_key"] == "full-letterbox-320-v1" else 640
        return RegistryState(
            kwargs["project_key"],
            kwargs["recipe_key"],
            kwargs["expected_revision"],
            make_release(side, "challenger"),
            make_release(side, "incumbent"),
        ).roll_back(expected_revision=kwargs["expected_revision"])

    monkeypatch.setattr(registry_api.model_store, "change_registry", change)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, calls


def admin_headers(client):
    csrf = login(client, "admin").json()["csrf_token"]
    return {"origin": ORIGIN, "x-csrf-token": csrf}


def test_anonymous_cannot_change_registry(http):
    client, calls = http
    assert client.post(PATH, json=PAYLOAD).status_code == 401
    assert calls == []


@pytest.mark.parametrize("side", [320, 640])
def test_admin_rolls_back_exact_recipe_with_authenticated_actor(http, store, side):
    client, calls = http
    headers = admin_headers(client)
    recipe = f"full-letterbox-{side}-v1"
    response = client.post(PATH, headers=headers, json=PAYLOAD | {"recipe_key": recipe})
    expected = RegistryState(
        "synthetic-qualification",
        recipe,
        3,
        make_release(side, "incumbent"),
        make_release(side, "challenger"),
    )
    assert response.status_code == 200
    assert response.json() == json_record(expected)
    assert response.headers["cache-control"] == "no-store"
    with store.engine.connect() as connection:
        actor_id = connection.execute(
            select(principal.c.id).where(principal.c.name == "admin")
        ).scalar_one()
    assert calls == [
        {
            "project_key": "synthetic-qualification",
            "recipe_key": recipe,
            "actor_id": actor_id,
            "expected_revision": 2,
        }
    ]


def test_reviewer_cannot_roll_back(http):
    client, calls = http
    csrf = login(client).json()["csrf_token"]
    response = client.post(PATH, headers={"origin": ORIGIN, "x-csrf-token": csrf}, json=PAYLOAD)
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize("worker", ["worker-core", "worker-ml", "worker-llm", "worker-platform"])
def test_service_principals_cannot_roll_back(http, store, worker):
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
def test_admin_requires_same_origin_and_csrf(http, headers):
    client, calls = http
    admin_headers(client)
    assert client.post(PATH, headers=headers, json=PAYLOAD).status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"actor_id": "invented-admin"},
        {"qualification": {}},
        {"policy": {}},
        {"candidate_key": "invented-release"},
        {"evaluation_key": "invented-report"},
        {"action": "promote"},
        {"expected_revision": -1},
        {"expected_revision": True},
        {"expected_revision": "2"},
        {"recipe_key": " "},
        {"recipe_key": "x" * 129},
    ],
)
def test_no_caller_supplied_authority_or_invalid_revision(http, change):
    client, calls = http
    response = client.post(PATH, headers=admin_headers(client), json=PAYLOAD | change)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert calls == []


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (ValueError("stale registry revision"), 409, "registry_change_rejected"),
        (ValueError("no known-good rollback release"), 409, "registry_change_rejected"),
        (ValueError("adverse annotation veto"), 409, "registry_change_rejected"),
        (NoResultFound("foreign or missing scope"), 404, "registry_scope_not_found"),
        (SQLAlchemyError("private driver context"), 503, "registry_unavailable"),
    ],
)
def test_store_failure_is_redacted_and_rolls_back_outer_transaction(
    http, store, monkeypatch, error, status, detail
):
    from uuid import uuid4

    from sqlalchemy import insert
    from test_auth import NOW

    from labelloop_api.auth_schema import auth_event

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

    monkeypatch.setattr(registry_api.model_store, "change_registry", fail)
    response = client.post(PATH, headers=headers, json=PAYLOAD)
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    with store.engine.connect() as connection:
        assert (
            connection.execute(select(auth_event.c.id).where(auth_event.c.id == probe_id)).all()
            == []
        )
