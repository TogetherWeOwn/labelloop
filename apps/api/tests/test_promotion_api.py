"""Authenticated promotion adapters; mocks are not persisted HTTP acceptance."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.exc import NoResultFound, SQLAlchemyError
from test_auth import NOW, ORIGIN, login, worker_token
from test_auth import password_hash as password_hash
from test_auth import store as store
from test_m5_integration_contract import make_release
from test_registry_api import admin_headers

from labelloop_api import registry_api
from labelloop_api.auth_schema import auth_event, principal
from labelloop_api.main import app
from labelloop_api.model_store import json_record
from labelloop_core.registry import RegistryState

PATH = "/projects/synthetic-qualification/promotions"
PAYLOAD = {
    "action": "promote",
    "recipe_key": "full-letterbox-320-v1",
    "model_key": "candidate",
    "annotation_release_key": "ordinary-population",
    "canary_release_key": "fresh-population",
    "expected_revision": 2,
}


@pytest.fixture
def http(monkeypatch, store):
    calls = []
    rollback_calls = []
    monkeypatch.setattr(registry_api, "auth_store", lambda: store)

    def promote(connection, **kwargs):
        assert connection.in_transaction()
        calls.append(kwargs)
        side = 320 if kwargs["recipe_key"] == "full-letterbox-320-v1" else 640
        return RegistryState(
            kwargs["project_key"],
            kwargs["recipe_key"],
            kwargs["expected_revision"] + 1,
            make_release(side, "candidate"),
            make_release(side, "incumbent"),
        )

    def rollback(*args, **kwargs):
        rollback_calls.append(kwargs)
        raise AssertionError("promotion must not select the rollback branch")

    monkeypatch.setattr(registry_api.promotion_service, "promote_candidate", promote)
    monkeypatch.setattr(registry_api.model_store, "change_registry", rollback)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, calls, rollback_calls
    assert rollback_calls == []


@pytest.mark.parametrize("side", [320, 640])
def test_admin_selects_identities_but_session_owns_actor(http, store, side):
    client, calls, _ = http
    recipe = f"full-letterbox-{side}-v1"
    response = client.post(
        PATH, headers=admin_headers(client), json=PAYLOAD | {"recipe_key": recipe}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == json_record(
        RegistryState(
            "synthetic-qualification",
            recipe,
            3,
            make_release(side, "candidate"),
            make_release(side, "incumbent"),
        )
    )
    with store.engine.connect() as connection:
        actor_id = connection.execute(
            select(principal.c.id).where(principal.c.name == "admin")
        ).scalar_one()
    assert calls == [
        {key: value for key, value in PAYLOAD.items() if key not in {"action", "recipe_key"}}
        | {
            "project_key": "synthetic-qualification",
            "recipe_key": recipe,
            "actor_id": actor_id,
            "require_fixture_policy": True,
        }
    ]


def test_anonymous_cannot_promote(http):
    client, calls, _ = http
    assert client.post(PATH, json=PAYLOAD).status_code == 401
    assert calls == []


def test_reviewer_cannot_promote(http):
    client, calls, _ = http
    csrf = login(client).json()["csrf_token"]
    response = client.post(PATH, headers={"origin": ORIGIN, "x-csrf-token": csrf}, json=PAYLOAD)
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize("role", ["worker-core", "worker-ml", "worker-llm", "worker-platform"])
def test_no_service_principal_can_choose_promotion_inputs(http, store, role):
    client, calls, _ = http
    response = client.post(
        PATH, headers={"authorization": "Bearer " + worker_token(store, role)}, json=PAYLOAD
    )
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    "headers", [{}, {"origin": "https://untrusted.invalid"}, {"x-csrf-token": "wrong"}]
)
def test_admin_requires_same_origin_and_csrf(http, headers):
    client, calls, _ = http
    admin_headers(client)
    assert client.post(PATH, headers=headers, json=PAYLOAD).status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"actor_id": "forged-admin"},
        {"require_fixture_policy": False},
        {"fixture_only": True},
        {"project_key": "foreign-project"},
        {"qualification": {}},
        {"qualification_key": "chosen-qualification"},
        {"evaluation_key": "chosen-final"},
        {"incumbent_evaluation_key": "chosen-comparison"},
        {"annotation": {"audit_key": "chosen-audit", "evidence_key": "chosen-outcome"}},
        {"canary": {"evidence_key": "chosen-canary"}},
        {"lineage": {}},
        {"policy": {}},
        {"targets": {}},
        {"scope": []},
        {"action": "rollback"},
        {"action": "unknown"},
        {"expected_revision": -1},
        {"expected_revision": True},
        {"expected_revision": "2"},
        {"model_key": " "},
        {"annotation_release_key": " "},
        {"canary_release_key": " "},
        {"recipe_key": "x" * 129},
        {"model_key": "x" * 129},
        {"annotation_release_key": "x" * 129},
        {"canary_release_key": "x" * 129},
    ],
)
def test_request_cannot_supply_gates_reports_outcomes_or_invalid_identities(http, change):
    client, calls, _ = http
    response = client.post(PATH, headers=admin_headers(client), json=PAYLOAD | change)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert calls == []


@pytest.mark.parametrize("field", list(PAYLOAD))
def test_promotion_fields_are_required(http, field):
    client, calls, _ = http
    payload = {key: value for key, value in PAYLOAD.items() if key != field}
    response = client.post(PATH, headers=admin_headers(client), json=payload)
    assert response.status_code == 422
    assert calls == []


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (NoResultFound("private missing scope"), 404, "registry_scope_not_found"),
        (
            ValueError("private stale revision or failed qualification"),
            409,
            "registry_change_rejected",
        ),
        (KeyError("private invalid persisted field"), 409, "registry_change_rejected"),
        (TypeError("private invalid persisted type"), 409, "registry_change_rejected"),
        (SQLAlchemyError("private driver context"), 503, "registry_unavailable"),
    ],
)
def test_failure_redacts_and_rolls_back_the_outer_transaction(
    http, store, monkeypatch, error, status, detail
):
    from uuid import uuid4

    client, calls, _ = http
    headers = admin_headers(client)
    probe_id = str(uuid4())

    def fail(connection, **kwargs):
        calls.append(kwargs)
        connection.execute(
            insert(auth_event).values(
                id=probe_id,
                principal_id=kwargs["actor_id"],
                occurred_at=NOW,
                action="logout",
            )
        )
        raise error

    monkeypatch.setattr(registry_api.promotion_service, "promote_candidate", fail)
    response = client.post(PATH, headers=headers, json=PAYLOAD)
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == "no-store"
    assert len(calls) == 1
    with store.engine.connect() as connection:
        assert (
            connection.execute(select(auth_event.c.id).where(auth_event.c.id == probe_id)).all()
            == []
        )
