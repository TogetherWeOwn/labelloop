"""Real offline SQL transactions; no accounts, credentials or paid calls outside fixtures."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.pool import StaticPool

from labelloop_api import auth
from labelloop_api.auth_schema import auth_event, metadata, principal, service_token, session
from labelloop_api.auth_store import (
    PASSWORDS,
    SESSION_CLEANUP_BATCH,
    SESSION_LIFETIME,
    AuthStore,
    digest,
)

NOW = datetime(2026, 11, 7, tzinfo=UTC)
ORIGIN = "https://testserver"
PASSWORD = "fixture-password-only"


@pytest.fixture(scope="module")
def password_hash():
    return PASSWORDS.hash(PASSWORD)


@pytest.fixture
def store(monkeypatch, password_hash):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    result = AuthStore(engine)
    with engine.begin() as connection:
        for name, kind, role in (
            ("admin", "human", "admin"),
            ("reviewer", "human", "reviewer"),
            *(
                (role, "service", role)
                for role in (
                    "worker-core",
                    "worker-ml",
                    "worker-llm",
                    "worker-platform",
                )
            ),
        ):
            connection.execute(
                insert(principal).values(
                    id=str(uuid4()),
                    name=name,
                    kind=kind,
                    role=role,
                    password_hash=password_hash if kind == "human" else None,
                    enabled=True,
                    failed_attempts=0,
                )
            )
    monkeypatch.setattr(auth, "auth_store", lambda: result)
    monkeypatch.setattr(auth, "clock", lambda: NOW)
    monkeypatch.setenv("AUTH_ORIGIN", ORIGIN)
    yield result
    engine.dispose()


@pytest.fixture
def client(store):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.middleware("http")(auth.authentication_boundary)
    app.add_exception_handler(RequestValidationError, auth.validation_error)
    app.include_router(auth.router)

    @app.post("/admin", dependencies=[Depends(auth.require_admin)])
    def admin():
        return {"ok": True}

    @app.post("/review", dependencies=[Depends(auth.require_reviewer)])
    def review():
        return {"ok": True}

    return TestClient(app, base_url=ORIGIN)


def login(client, name="reviewer", **extra):
    return client.post(
        "/auth/login",
        headers={"origin": ORIGIN},
        json={
            "name": name,
            "password": PASSWORD,
            **extra,
        },
    )


def worker_token(store, name):
    token = "w" * 43
    with store.engine.begin() as connection:
        identity = connection.execute(
            select(principal.c.id).where(principal.c.name == name)
        ).scalar_one()
        connection.execute(
            insert(service_token).values(
                token_hash=digest(token),
                principal_id=identity,
                expires_at=NOW + timedelta(hours=1),
            )
        )
    return token


def test_argon2id_and_cookie_contract(client, store):
    response = login(client)
    assert response.status_code == 200
    header = response.headers["set-cookie"]
    for expected in ("__Host-", "HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert expected in header
    assert "Domain=" not in header
    token = client.cookies.get(auth.COOKIE)
    with store.engine.connect() as connection:
        stored = connection.execute(select(session)).mappings().one()
        user = (
            connection.execute(select(principal).where(principal.c.name == "reviewer"))
            .mappings()
            .one()
        )
    assert user["password_hash"].startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert stored["token_hash"] == digest(token)
    assert stored["csrf_hash"] == digest(response.json()["csrf_token"])
    assert token not in str(stored) and response.json()["csrf_token"] not in str(stored)
    assert client.get("/auth/me").json()["role"] == "reviewer"
    assert "password" not in client.get("/auth/me").text


@pytest.mark.parametrize(
    "path", ["/auth/me", "/admin", "/review", "/docs", "/openapi.json", "/new-route"]
)
def test_every_nonpublic_route_is_guarded(client, path):
    assert client.get(path).status_code == 401


def test_reviewer_denied_admin_with_durable_principal_event(client, store):
    csrf = login(client).json()["csrf_token"]
    headers = {"origin": ORIGIN, "x-csrf-token": csrf}
    assert client.post("/review", headers=headers).status_code == 200
    assert client.post("/admin", headers=headers).status_code == 403
    with store.engine.connect() as connection:
        row = (
            connection.execute(
                select(auth_event, principal.c.name)
                .join(
                    principal,
                    auth_event.c.principal_id == principal.c.id,
                )
                .where(auth_event.c.action == "permission_denied")
            )
            .mappings()
            .one()
        )
    assert row["name"] == "reviewer"


def test_admin_can_review_and_administer(client):
    csrf = login(client, "admin").json()["csrf_token"]
    for path in ("/admin", "/review"):
        assert (
            client.post(path, headers={"origin": ORIGIN, "x-csrf-token": csrf}).status_code == 200
        )


@pytest.mark.parametrize("name", ["worker-core", "worker-ml", "worker-llm", "worker-platform"])
def test_workers_never_receive_human_or_admin_authority(client, store, name):
    token = worker_token(store, name)
    headers = {"authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=headers).json()["kind"] == "service"
    for path in ("/review", "/admin"):
        assert client.post(path, headers=headers).status_code == 403
    authenticated = store.authenticate(token, service=True, now=NOW)
    assert authenticated.principal.actor_kind == "model"
    assert not authenticated.principal.permits("invented")
    assert store.login(name, PASSWORD, NOW) is None


@pytest.mark.parametrize("field", ["actor_kind", "review_tier", "role", "kind"])
def test_client_cannot_supply_identity_or_human_fields(client, field):
    response = login(client, **{field: "human"})
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert PASSWORD not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {"name": "reviewer", "password": "sensitive" * 200},
        {"name": "reviewer", "password": {"secret": "must-not-echo"}},
        {"name": ["must-not-echo"], "password": "must-not-echo"},
    ],
)
def test_validation_never_echoes_input(client, body, caplog):
    response = client.post("/auth/login", json=body, headers={"origin": ORIGIN})
    assert response.status_code == 422
    assert "must-not-echo" not in response.text + caplog.text
    assert "sensitive" not in response.text + caplog.text


def test_failed_login_is_generic_and_locks_after_five(client, store):
    for name in ("nonexistent", "reviewer"):
        response = client.post(
            "/auth/login", json={"name": name, "password": "wrong"}, headers={"origin": ORIGIN}
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "invalid_credentials"}
    for _ in range(4):
        assert store.login("reviewer", "wrong", NOW) is None
    assert store.login("reviewer", PASSWORD, NOW) is None
    assert store.login("reviewer", PASSWORD, NOW + timedelta(minutes=16)) is not None


@pytest.mark.parametrize("password", [PASSWORD, "wrong"])
def test_locked_retries_preserve_original_expiry(store, password):
    for _ in range(5):
        assert store.login("reviewer", "wrong", NOW) is None
    assert store.login("reviewer", password, NOW + timedelta(minutes=14)) is None
    with store.engine.connect() as connection:
        row = (
            connection.execute(select(principal).where(principal.c.name == "reviewer"))
            .mappings()
            .one()
        )
    assert row["locked_until"].replace(tzinfo=UTC) == NOW + timedelta(minutes=15)
    assert row["failed_attempts"] == 5
    assert store.login("reviewer", PASSWORD, NOW + timedelta(minutes=15)) is not None


def test_failure_counter_restarts_after_lockout_expiry(store):
    for _ in range(5):
        assert store.login("reviewer", "wrong", NOW) is None
    expired = NOW + timedelta(minutes=15)
    assert store.login("reviewer", "wrong", expired) is None
    with store.engine.connect() as connection:
        row = (
            connection.execute(select(principal).where(principal.c.name == "reviewer"))
            .mappings()
            .one()
        )
    assert row["failed_attempts"] == 1
    assert row["locked_until"] is None
    assert store.login("reviewer", PASSWORD, expired) is not None


@pytest.mark.parametrize("name,path", [("reviewer", "/admin"), ("worker-ml", "/auth/logout")])
def test_permission_audit_failure_is_generic_unavailable(
    client, store, monkeypatch, caplog, name, path
):
    if name == "reviewer":
        csrf = login(client).json()["csrf_token"]
        headers = {"origin": ORIGIN, "x-csrf-token": csrf}
    else:
        headers = {"authorization": f"Bearer {worker_token(store, name)}"}

    def unavailable(*args):
        raise SQLAlchemyError("must-not-echo audit database detail")

    monkeypatch.setattr(store, "event", unavailable)
    response = client.post(path, headers=headers)
    assert response.status_code == 503
    assert response.json() == {"detail": "authentication_unavailable"}
    assert "must-not-echo" not in response.text + caplog.text


def test_failed_session_insert_rolls_back_login_and_counter_reset(client, store):
    assert store.login("reviewer", "wrong", NOW) is None

    def unavailable(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO auth_session"):
            raise SQLAlchemyError("must-not-echo session database detail")

    event.listen(store.engine, "before_cursor_execute", unavailable)
    try:
        response = login(client)
    finally:
        event.remove(store.engine, "before_cursor_execute", unavailable)
    assert response.status_code == 503
    assert "must-not-echo" not in response.text
    assert auth.COOKIE not in client.cookies
    with store.engine.connect() as connection:
        assert connection.execute(select(session)).all() == []
        events = connection.execute(select(auth_event.c.action)).scalars().all()
        assert events == ["login_failed"]
        assert (
            connection.execute(
                select(principal.c.failed_attempts).where(principal.c.name == "reviewer")
            ).scalar_one()
            == 1
        )


def test_failed_logout_audit_rolls_back_revocation(client, store):
    csrf = login(client).json()["csrf_token"]
    token = client.cookies.get(auth.COOKIE)

    def unavailable(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO auth_event"):
            raise SQLAlchemyError("must-not-echo logout database detail")

    event.listen(store.engine, "before_cursor_execute", unavailable)
    try:
        response = client.post("/auth/logout", headers={"origin": ORIGIN, "x-csrf-token": csrf})
    finally:
        event.remove(store.engine, "before_cursor_execute", unavailable)
    assert response.status_code == 503
    assert "must-not-echo" not in response.text
    assert "set-cookie" not in response.headers
    assert store.authenticate(token, service=False, now=NOW) is not None
    with store.engine.connect() as connection:
        assert connection.execute(select(auth_event.c.action)).scalars().all() == [
            "login_succeeded"
        ]


@pytest.mark.parametrize(
    "headers", [{}, {"origin": "https://other.test"}, {"x-csrf-token": "wrong"}]
)
def test_cookie_writes_need_origin_and_matching_csrf(client, headers):
    login(client)
    assert client.post("/review", headers=headers).status_code == 403


def test_csrf_is_bound_to_session_and_login_requires_configured_origin(client, monkeypatch):
    first = login(client).json()["csrf_token"]
    second = login(client).json()["csrf_token"]
    assert first != second
    assert (
        client.post("/review", headers={"origin": ORIGIN, "x-csrf-token": first}).status_code == 403
    )
    monkeypatch.delenv("AUTH_ORIGIN")
    assert login(client).status_code == 403


def test_logout_revokes_session_and_is_not_get(client, store):
    csrf = login(client).json()["csrf_token"]
    token = client.cookies.get(auth.COOKIE)
    assert client.get("/auth/logout").status_code == 405
    assert (
        client.post("/auth/logout", headers={"origin": ORIGIN, "x-csrf-token": csrf}).status_code
        == 204
    )
    assert client.get("/auth/me").status_code == 401
    assert store.authenticate(token, service=False, now=NOW) is None


def test_expiry_disable_and_role_changes_take_effect(client, store, monkeypatch):
    login(client)
    with store.engine.begin() as connection:
        connection.execute(
            update(principal).where(principal.c.name == "reviewer").values(enabled=False)
        )
    assert client.get("/auth/me").status_code == 401
    with store.engine.begin() as connection:
        connection.execute(
            update(principal)
            .where(principal.c.name == "reviewer")
            .values(enabled=True, role="admin")
        )
    assert client.get("/auth/me").json()["role"] == "admin"
    monkeypatch.setattr(auth, "clock", lambda: NOW + SESSION_LIFETIME)
    assert client.get("/auth/me").status_code == 401


def test_service_and_browser_credentials_cannot_be_interchanged(client, store):
    token = worker_token(store, "worker-ml")
    assert store.authenticate(token, service=False, now=NOW) is None
    login(client)
    browser_token = client.cookies.get(auth.COOKIE)
    assert store.authenticate(browser_token, service=True, now=NOW) is None
    assert client.get("/auth/me", headers={"authorization": f"Bearer {token}"}).status_code == 401
    assert store.authenticate(token, service=True, now=NOW + timedelta(hours=1)) is None


@pytest.mark.parametrize(
    "value", ["", "Bearer", "Basic test", "Bearer invalid", "Bearer " + "x" * 44]
)
def test_malformed_credentials_fail_closed(client, value):
    assert client.get("/auth/me", headers={"authorization": value}).status_code == 401


def test_database_rejects_service_admin_and_human_worker_roles(store):
    for kind, role, encoded in (("service", "admin", None), ("human", "worker-ml", "invalid")):
        with pytest.raises(IntegrityError), store.engine.begin() as connection:
            connection.execute(
                insert(principal).values(
                    id=str(uuid4()),
                    name="invalid",
                    kind=kind,
                    role=role,
                    password_hash=encoded,
                    enabled=True,
                    failed_attempts=0,
                )
            )


def test_production_application_uses_authentication_boundary(store):
    from labelloop_api.main import app

    with TestClient(app, base_url=ORIGIN) as production:
        assert app.docs_url is None and app.redoc_url is None and app.openapi_url is None
        for path in ("/auth/me", "/docs", "/openapi.json", "/new-route"):
            assert production.get(path).status_code == 401
        assert login(production, role="admin").json() == {"detail": "invalid_request"}
        assert login(production).status_code == 200
        assert production.get("/auth/me").json()["role"] == "reviewer"


def test_verification_releases_connection_and_rechecks_password(store, monkeypatch):
    checked_out = set()

    def checkout(dbapi_connection, record, proxy):
        checked_out.add(id(record))

    def checkin(dbapi_connection, record):
        checked_out.discard(id(record))

    event.listen(store.engine, "checkout", checkout)
    event.listen(store.engine, "checkin", checkin)
    verify = PASSWORDS.verify
    replacement = PASSWORDS.hash("changed-fixture-password")

    def concurrent_change(self, encoded, password):
        assert not checked_out
        valid = verify(encoded, password)
        with store.engine.begin() as connection:
            connection.execute(
                update(principal)
                .where(principal.c.name == "reviewer")
                .values(password_hash=replacement)
            )
        return valid

    monkeypatch.setattr(type(PASSWORDS), "verify", concurrent_change)
    try:
        assert store.login("reviewer", PASSWORD, NOW) is None
    finally:
        event.remove(store.engine, "checkout", checkout)
        event.remove(store.engine, "checkin", checkin)
    with store.engine.connect() as connection:
        assert connection.execute(select(session)).all() == []
        assert connection.execute(select(auth_event.c.action)).scalars().all() == ["login_failed"]


def test_reload_recovers_csrf_without_revealing_session(client):
    assert login(client).status_code == 200
    # A new browser context retains cookies but not the previous login response.
    with TestClient(client.app, base_url=ORIGIN, cookies=client.cookies) as reloaded:
        assert reloaded.get("/auth/me").status_code == 200
        proof = reloaded.cookies.get(auth.CSRF_COOKIE)
        assert proof and proof != reloaded.cookies.get(auth.COOKIE)
        headers = {"origin": ORIGIN, "x-csrf-token": proof}
        assert reloaded.post("/review", headers=headers).status_code == 200
        assert reloaded.post("/auth/logout", headers=headers).status_code == 204
        assert auth.COOKIE not in reloaded.cookies
        assert auth.CSRF_COOKIE not in reloaded.cookies


def test_readable_csrf_cookie_cannot_authenticate_or_replace_session_proof(client):
    first = login(client).json()["csrf_token"]
    response = login(client)
    cookies = response.headers.get_list("set-cookie")
    csrf_cookie = next(value for value in cookies if value.startswith(auth.CSRF_COOKIE + "="))
    for expected in ("Secure", "SameSite=strict", "Path=/", "Max-Age=28800"):
        assert expected in csrf_cookie
    assert "HttpOnly" not in csrf_cookie and "Domain=" not in csrf_cookie
    # Even a supplied cookie/header pair must match the database's current session proof.
    client.cookies.set(auth.CSRF_COOKIE, first)
    assert (
        client.post("/review", headers={"origin": ORIGIN, "x-csrf-token": first}).status_code == 403
    )
    proof = response.json()["csrf_token"]
    client.cookies.clear()
    client.cookies.set(auth.COOKIE, proof)
    assert client.get("/auth/me").status_code == 401


@pytest.mark.parametrize("failure", ["missing", "invalid", "csrf", "database"])
def test_boundary_rejections_are_never_stored(client, store, monkeypatch, failure):
    if failure == "csrf":
        login(client)
        response = client.post("/review", headers={"origin": ORIGIN})
        assert response.status_code == 403
    else:
        if failure == "invalid":
            client.cookies.set(auth.COOKIE, "x" * 43)
        elif failure == "database":
            client.cookies.set(auth.COOKIE, "x" * 43)

            def unavailable(*args, **kwargs):
                raise SQLAlchemyError("database detail")

            monkeypatch.setattr(store, "authenticate", unavailable)
        response = client.get("/auth/me")
        assert response.status_code == (503 if failure == "database" else 401)
    assert response.headers["cache-control"] == "no-store"


def test_login_reaps_only_bounded_expired_sessions_and_preserves_events(store):
    token, _ = store.login("reviewer", PASSWORD, NOW)
    with store.engine.begin() as connection:
        identity = connection.execute(
            select(principal.c.id).where(principal.c.name == "reviewer")
        ).scalar_one()
        connection.execute(
            insert(session),
            [
                {
                    "token_hash": digest(f"expired-{index}"),
                    "principal_id": identity,
                    "csrf_hash": digest("fixture-csrf"),
                    "expires_at": NOW,
                }
                for index in range(SESSION_CLEANUP_BATCH + 1)
            ],
        )
    assert store.login("reviewer", PASSWORD, NOW) is not None
    with store.engine.connect() as connection:
        assert (
            len(connection.execute(select(session).where(session.c.expires_at <= NOW)).all()) == 1
        )
        assert connection.execute(select(auth_event.c.action)).scalars().all() == [
            "login_succeeded",
            "login_succeeded",
        ]
    assert store.authenticate(token, service=False, now=NOW) is not None
    assert store.login("reviewer", PASSWORD, NOW) is not None
    with store.engine.connect() as connection:
        assert connection.execute(select(session).where(session.c.expires_at <= NOW)).all() == []


def test_store_does_not_retain_password_or_session_plaintext(client, store, caplog):
    response = login(client)
    token = client.cookies.get(auth.COOKIE)
    with store.engine.connect() as connection:
        events = connection.execute(select(auth_event)).all()
    for secret in (PASSWORD, token, response.json()["csrf_token"]):
        assert secret not in str(events) + caplog.text
