"""Session-bound audit adapter; persisted sample/veto controls run in ML smoke."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError, NoResultFound, SQLAlchemyError
from test_auth import NOW, ORIGIN, login, worker_token
from test_auth import password_hash as password_hash
from test_auth import store as store

from labelloop_api import registry_api
from labelloop_api.auth_schema import auth_event, principal
from labelloop_api.main import app
from labelloop_core.audit import AuditObservation

PATH = "/projects/fixture-project/dataset-releases/fixture-release/audits"
OBSERVATION = {
    "asset_id": "ordinary-positive",
    "revision": 1,
    "blind_full_image_inspected": True,
    "labels_correct": False,
    "scope_complete": True,
}
PAYLOAD = {
    "audit_key": "fixture-audit",
    "sample_key": "persisted-sample",
    "observations": [OBSERVATION],
}


@pytest.fixture
def http(monkeypatch, store):
    calls = []
    monkeypatch.setattr(registry_api, "auth_store", lambda: store)

    def record(connection, **kwargs):
        assert connection.in_transaction()
        calls.append(kwargs)

    monkeypatch.setattr(registry_api.annotation_audit_store, "record_annotation_audit", record)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, calls


def headers(client, name="reviewer"):
    csrf = login(client, name).json()["csrf_token"]
    return {"origin": ORIGIN, "x-csrf-token": csrf}


@pytest.mark.parametrize("name", ["reviewer", "admin"])
def test_human_observations_are_bound_to_session_not_caller(http, store, name):
    client, calls = http
    response = client.post(PATH, headers=headers(client, name), json=PAYLOAD)
    assert response.status_code == 201
    assert response.json() == {"audit_key": "fixture-audit", "recorded": True}
    assert response.headers["cache-control"] == "no-store"
    with store.engine.connect() as connection:
        actor_id = connection.execute(
            select(principal.c.id).where(principal.c.name == name)
        ).scalar_one()
    assert calls == [
        {
            "project_key": "fixture-project",
            "release_key": "fixture-release",
            "audit_key": "fixture-audit",
            "sample_key": "persisted-sample",
            "actor_id": actor_id,
            "observations": (AuditObservation(reviewer_id=actor_id, **OBSERVATION),),
        }
    ]


def test_anonymous_cannot_record_audit(http):
    client, calls = http
    assert client.post(PATH, json=PAYLOAD).status_code == 401
    assert calls == []


@pytest.mark.parametrize("worker", ["worker-core", "worker-ml", "worker-llm", "worker-platform"])
def test_workers_cannot_assert_independent_human_observations(http, store, worker):
    client, calls = http
    response = client.post(
        PATH, headers={"authorization": "Bearer " + worker_token(store, worker)}, json=PAYLOAD
    )
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    "denied", [{}, {"origin": "https://untrusted.invalid"}, {"x-csrf-token": "wrong"}]
)
def test_audit_requires_same_origin_and_csrf(http, denied):
    client, calls = http
    headers(client)
    assert client.post(PATH, headers=denied, json=PAYLOAD).status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"actor_id": "invented"},
        {"qualification": {}},
        {"passed": True},
        {"sample_key": None},
        {"sample_key": " "},
        {"sample_key": "x" * 129},
        {"audit_key": ""},
        {"observations": []},
        {"observations": [OBSERVATION | {"reviewer_id": "invented"}]},
        {"observations": [OBSERVATION | {"revision": True}]},
        {"observations": [OBSERVATION | {"revision": 0}]},
        {"observations": [OBSERVATION | {"labels_correct": "true"}]},
        {"observations": [OBSERVATION | {"asset_id": " "}]},
        {"observations": [OBSERVATION] * 1001},
    ],
)
def test_audit_rejects_caller_authority_and_invalid_observations(http, change):
    client, calls = http
    response = client.post(PATH, headers=headers(client), json=PAYLOAD | change)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert calls == []


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (ValueError("dependent, stale or outside sample"), 409, "audit_rejected"),
        (IntegrityError("private duplicate", {}, Exception("driver")), 409, "audit_rejected"),
        (NoResultFound("private missing scope"), 404, "audit_scope_not_found"),
        (SQLAlchemyError("private driver failure"), 503, "audit_unavailable"),
    ],
)
def test_failed_audit_is_redacted_and_outer_transaction_rolls_back(
    http, store, monkeypatch, error, status, detail
):
    client, _ = http
    authorized = headers(client)
    probe_id = str(uuid4())

    def fail(connection, **kwargs):
        connection.execute(
            insert(auth_event).values(
                id=probe_id, principal_id=kwargs["actor_id"], occurred_at=NOW, action="logout"
            )
        )
        raise error

    monkeypatch.setattr(registry_api.annotation_audit_store, "record_annotation_audit", fail)
    response = client.post(PATH, headers=authorized, json=PAYLOAD)
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    with store.engine.connect() as connection:
        assert (
            connection.execute(select(auth_event.c.id).where(auth_event.c.id == probe_id)).all()
            == []
        )
