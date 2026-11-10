"""Router-local mount, real sessions and production store on disposable PostgreSQL."""

from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from test_budget_resume_store import PROJECT, pause, resume_store, rows
from test_budget_store import NOW
from test_budget_store import store as store

from labelloop_api import auth, auth_schema, budget_resume
from labelloop_api.auth_store import PASSWORDS, AuthStore, digest

ORIGIN = "https://testserver"
PASSWORD = "fixture-only-password"
PATH = f"/projects/{PROJECT}/paid-stage-pause"


@pytest.fixture(scope="module")
def password_hash():
    return PASSWORDS.hash(PASSWORD)


@pytest.fixture
def client(store, monkeypatch, password_hash):
    # Public already contains migrated auth tables in this disposable Actions DB.
    # Force creation in the per-test search_path instead of finding public tables.
    auth_schema.metadata.create_all(store.engine, checkfirst=False)
    with store.engine.begin() as connection:
        for name, kind, role in (
            ("admin", "human", "admin"),
            ("reviewer", "human", "reviewer"),
            ("worker", "service", "worker-platform"),
        ):
            connection.execute(
                sa.insert(auth_schema.principal).values(
                    id=str(uuid4()),
                    name=name,
                    kind=kind,
                    role=role,
                    password_hash=password_hash if kind == "human" else None,
                    enabled=True,
                    failed_attempts=0,
                )
            )
    authentication = AuthStore(store.engine)
    monkeypatch.setattr(auth, "auth_store", lambda: authentication)
    monkeypatch.setattr(auth, "clock", lambda: NOW)
    monkeypatch.setenv("AUTH_ORIGIN", ORIGIN)
    # Deliberately no product auth middleware: this router owns its local guard,
    # redaction and no-store responses even before product integration.
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(budget_resume.router)
    app.dependency_overrides[budget_resume.resume_store] = lambda: resume_store(store)
    with TestClient(app, base_url=ORIGIN) as result:
        yield result


def login(client, name="admin"):
    response = client.post(
        "/auth/login", headers={"origin": ORIGIN}, json={"name": name, "password": PASSWORD}
    )
    assert response.status_code == 200
    return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}


def command(client):
    response = client.get(PATH)
    assert response.status_code == 200
    state = response.json()
    return {
        "expected_policy_revision": state["policy_revision"],
        "paused_state_token": state["state_token"],
    }


def test_real_session_resume_is_actor_bound_redacted_and_not_product_mounted(client, store):
    pause(store)
    before = rows(store)
    headers = login(client)
    payload = command(client)
    response = client.post(PATH + "/resume", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"project_id": PROJECT, "policy_revision": 2}
    assert response.headers["cache-control"] == "no-store"
    metadata = client.get(PATH)
    assert metadata.json() == {
        "project_id": PROJECT,
        "policy_revision": 2,
        "pause_reason": None,
        "state_token": None,
    }
    assert metadata.headers["cache-control"] == "no-store"
    after = rows(store)
    assert before["budget_operation"] == after["budget_operation"]
    with store.engine.connect() as connection:
        actor = connection.execute(
            sa.select(auth_schema.principal.c.id).where(auth_schema.principal.c.name == "admin")
        ).scalar_one()
    assert (
        next(e for e in after["budget_event"] if e["action"] == "resume")["principal_id"] == actor
    )
    duplicate = client.post(PATH + "/resume", json=payload, headers=headers)
    assert duplicate.status_code == 409 and duplicate.json() == {"detail": "resume_conflict"}
    assert duplicate.headers["cache-control"] == "no-store"
    from labelloop_api.main import app

    assert all("paid-stage-pause" not in route.path for route in app.routes)


@pytest.mark.parametrize(
    "denial", ["anonymous", "expired", "reviewer", "service", "csrf", "origin", "ambiguous"]
)
def test_denied_sessions_roles_csrf_are_budget_noops(client, store, denial):
    pause(store)
    headers = login(client)
    payload = command(client)
    if denial == "anonymous":
        client.cookies.clear()
    elif denial == "expired":
        with store.engine.begin() as connection:
            connection.execute(
                sa.update(auth_schema.session).values(expires_at=NOW - timedelta(seconds=1))
            )
    elif denial == "reviewer":
        headers = login(client, "reviewer")
    elif denial in {"service", "ambiguous"}:
        token = "fixture-service-token"
        with store.engine.begin() as connection:
            actor = connection.execute(
                sa.select(auth_schema.principal.c.id).where(
                    auth_schema.principal.c.name == "worker"
                )
            ).scalar_one()
            connection.execute(
                sa.insert(auth_schema.service_token).values(
                    token_hash=digest(token),
                    principal_id=actor,
                    expires_at=NOW + timedelta(hours=1),
                )
            )
        headers["authorization"] = "Bearer " + token
        if denial == "service":
            client.cookies.clear()
    elif denial == "csrf":
        headers["x-csrf-token"] = "wrong"
    else:
        headers["origin"] = "https://other.test"
    before = rows(store)
    response = client.post(PATH + "/resume", json=payload, headers=headers)
    assert response.status_code in {401, 403}
    assert response.headers["cache-control"] == "no-store"
    assert rows(store) == before
    if denial not in {"csrf", "origin"}:
        read = client.get(
            PATH, headers={key: value for key, value in headers.items() if key == "authorization"}
        )
        assert read.status_code in {401, 403}
        assert read.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "invalid",
    [
        {"actor": "caller-selected"},
        {"operation_id": "one"},
        {"price": 0},
        {"expected_policy_revision": True},
        {"expected_policy_revision": "1"},
        {"expected_policy_revision": 0},
        {"expected_policy_revision": 2**31},
        {"paused_state_token": "v2." + "a" * 64},
        {"paused_state_token": "v1." + "A" * 64},
        {"paused_state_token": "private-provider-payload"},
        {"paused_state_token": "a" * 1100},
    ],
)
def test_bounded_strict_body_redacts_rejected_inputs_without_mutation(client, store, invalid):
    pause(store)
    headers = login(client)
    payload = command(client) | invalid
    before = rows(store)
    response = client.post(PATH + "/resume", json=payload, headers=headers)
    assert response.status_code in {413, 422}
    assert response.json() == {"detail": "invalid_request"}
    assert response.headers["cache-control"] == "no-store"
    assert rows(store) == before


def test_missing_conflict_and_store_failure_are_redacted_no_store(client, store, monkeypatch):
    pause(store)
    headers = login(client)
    payload = command(client)
    before = rows(store)
    for path in ("/projects/missing/paid-stage-pause", "/projects/foreign/paid-stage-pause"):
        for response in (
            client.get(path),
            client.post(path + "/resume", json=payload, headers=headers),
        ):
            assert response.status_code == 404
            assert response.headers["cache-control"] == "no-store"
    response = client.post(
        PATH + "/resume", json=payload | {"paused_state_token": "v1." + "a" * 64}, headers=headers
    )
    assert response.status_code == 409
    assert rows(store) == before

    def unavailable(*args, **kwargs):
        raise SQLAlchemyError("private-provider-fixture-never-echo")

    monkeypatch.setattr(budget_resume.BudgetResumeStore, "paused_state", unavailable)
    monkeypatch.setattr(budget_resume.BudgetResumeStore, "resume", unavailable)
    for response in (
        client.get(PATH),
        client.post(PATH + "/resume", json=payload, headers=headers),
    ):
        assert response.status_code == 503
        assert response.json() == {"detail": "resume_unavailable"}
        assert response.headers["cache-control"] == "no-store"
    assert rows(store) == before
