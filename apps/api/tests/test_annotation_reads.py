"""Offline HTTP/auth and relational read tests; no live database or provider calls."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool

from labelloop_api import annotation_reads, auth
from labelloop_api.auth_schema import auth_event, metadata, principal, service_token, session
from labelloop_api.auth_store import AuthStore, digest
from labelloop_api.main import app
from labelloop_api.schema import annotation_revision

NOW = datetime(2026, 11, 8, tzinfo=UTC)
BASE = "/projects/domain-a/assets/source-a"


def fixture_token(role):
    # Match the production 43-character URL-safe token boundary, without real credentials.
    return digest("annotation-read-fixture:" + role)[:43]


def fixture_engine():
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


@pytest.fixture(scope="module")
def authentication():
    engine = fixture_engine()
    metadata.create_all(engine)
    store = AuthStore(engine)
    with engine.begin() as connection:
        for role in (
            "admin",
            "reviewer",
            "worker-core",
            "worker-ml",
            "worker-llm",
            "worker-platform",
        ):
            human = role in ("admin", "reviewer")
            connection.execute(
                insert(principal).values(
                    id=role,
                    name=role,
                    kind="human" if human else "service",
                    role=role,
                    password_hash="not-used-for-session-fixtures" if human else None,
                    enabled=True,
                    failed_attempts=0,
                )
            )
            values = {
                "token_hash": digest(fixture_token(role)),
                "principal_id": role,
                "expires_at": NOW + timedelta(hours=1),
            }
            if human:
                values["csrf_hash"] = digest("fixture-csrf")
            connection.execute(insert(session if human else service_token).values(**values))
    yield store
    engine.dispose()


@pytest.fixture
def canonical(monkeypatch):
    engine = fixture_engine()
    # Execute production SELECTs against tables, not mocked result ordering.
    # PostgreSQL defaults/guards remain covered by the migration Actions jobs.
    with engine.begin() as connection:
        for ddl in (
            "CREATE TABLE project (id TEXT PRIMARY KEY, project_key TEXT)",
            "CREATE TABLE source_asset (id TEXT PRIMARY KEY, project_id TEXT, asset_key TEXT,"
            " sha256 TEXT, width INTEGER, height INTEGER)",
            "CREATE TABLE annotation_revision (id TEXT PRIMARY KEY, asset_id TEXT,"
            " revision INTEGER, parent_revision INTEGER, actor_kind TEXT, actor_id TEXT,"
            " created_at DATETIME, objects JSON, label_state TEXT, approval_state TEXT,"
            " review_tier TEXT, scope_completeness TEXT, training_eligible BOOLEAN,"
            " gold_eligible BOOLEAN)",
        ):
            connection.execute(text(ddl))
        connection.execute(text("INSERT INTO project VALUES ('a', 'domain-a'), ('b', 'domain-b')"))
        for asset, owner in (("source-a", "a"), ("source-b", "b")):
            connection.execute(
                text("INSERT INTO source_asset VALUES (:id, :owner, :key, :sha, 200, 100)"),
                {
                    "id": uuid5(NAMESPACE_URL, asset).hex,
                    "key": asset,
                    "owner": owner,
                    "sha": owner * 64,
                },
            )
            for revision in range(1, 5):
                add_revision(connection, asset, revision)
    monkeypatch.setattr(annotation_reads, "annotation_engine", lambda: engine)
    yield engine
    engine.dispose()


def add_revision(connection, asset, revision):
    connection.execute(
        insert(annotation_revision).values(
            id=uuid5(NAMESPACE_URL, f"{asset}-{revision}"),
            asset_id=uuid5(NAMESPACE_URL, asset),
            revision=revision,
            parent_revision=revision - 1 if revision > 1 else None,
            actor_kind="system",
            actor_id="fixture-policy",
            created_at=NOW,
            objects=[{"object_id": "target", "class_id": "item", "box": [1, 2, 30, 40]}]
            if revision > 1
            else [],
            label_state="identified",
            approval_state="auto_accepted" if revision > 1 else "draft",
            review_tier="machine_verified_no_human_review" if revision > 1 else "unreviewed",
            scope_completeness="unreviewed",
            training_eligible=False,
            gold_eligible=False,
        )
    )


@pytest.fixture
def client(monkeypatch, authentication, canonical):
    monkeypatch.setattr(auth, "auth_store", lambda: authentication)
    monkeypatch.setattr(auth, "clock", lambda: NOW)
    with TestClient(app, base_url="https://testserver") as result:
        yield result


def as_human(client, role="reviewer"):
    client.cookies.set(auth.COOKIE, fixture_token(role))


@pytest.mark.parametrize("suffix", ["annotations", "annotation-history"])
def test_reads_require_authentication_before_database_access(client, monkeypatch, suffix):
    engine = MagicMock(side_effect=AssertionError("unauthenticated database access"))
    monkeypatch.setattr(annotation_reads, "annotation_engine", engine)
    response = client.get(f"{BASE}/{suffix}")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    engine.assert_not_called()


@pytest.mark.parametrize("role", ["admin", "reviewer"])
def test_snapshot_preserves_source_geometry_and_machine_tiers(client, canonical, role):
    as_human(client, role)
    response = client.get(f"{BASE}/annotations")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["width"], body["height"], body["source_sha256"]) == (200, 100, "a" * 64)
    annotation = body["annotation"]
    assert annotation["revision"] == 4 and annotation["parent_revision"] == 3
    assert annotation["actor_id"] == "fixture-policy"
    assert annotation["approval_state"] == "auto_accepted"
    assert annotation["review_tier"] == "machine_verified_no_human_review"
    assert annotation["scope_completeness"] == "unreviewed"
    assert annotation["recorded_training_eligible"] is False
    assert annotation["recorded_gold_eligible"] is False
    assert annotation["objects"][0]["box"] == [1, 2, 30, 40]
    with canonical.connect() as connection:
        assert len(connection.execute(select(annotation_revision.c.revision)).all()) == 8
    assert "object_key" not in body and "access_policy" not in body


@pytest.mark.parametrize("suffix", ["annotations", "annotation-history"])
@pytest.mark.parametrize("role", ["worker-core", "worker-ml", "worker-llm", "worker-platform"])
def test_workers_cannot_read_review_surfaces(client, authentication, monkeypatch, suffix, role):
    engine = MagicMock(side_effect=AssertionError("worker database access"))
    monkeypatch.setattr(annotation_reads, "annotation_engine", engine)
    response = client.get(
        f"{BASE}/{suffix}", headers={"authorization": f"Bearer {fixture_token(role)}"}
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "permission_denied"}
    engine.assert_not_called()
    with authentication.engine.connect() as connection:
        assert connection.execute(
            select(auth_event.c.id).where(
                auth_event.c.principal_id == role, auth_event.c.action == "permission_denied"
            )
        ).first()


@pytest.mark.parametrize("suffix", ["annotations", "annotation-history?before_revision=1"])
@pytest.mark.parametrize(
    "path",
    [
        "/projects/domain-b/assets/source-a",
        "/projects/absent/assets/source-a",
        "/projects/domain-a/assets/absent",
    ],
)
def test_absent_and_cross_project_assets_share_not_found(client, suffix, path):
    as_human(client)
    response = client.get(f"{path}/{suffix}")
    assert response.status_code == 404
    assert response.json() == {"detail": "annotation_not_found"}
    assert "source_sha256" not in response.text


def test_history_cursor_is_stable_across_new_revisions(client, canonical):
    as_human(client)
    response = client.get(f"{BASE}/annotation-history?limit=2").json()
    assert [row["revision"] for row in response["revisions"]] == [4, 3]
    assert response["next_before_revision"] == 3
    with canonical.begin() as connection:
        add_revision(connection, "source-a", 5)
    response = client.get(f"{BASE}/annotation-history?limit=2&before_revision=3").json()
    assert [row["revision"] for row in response["revisions"]] == [2, 1]
    assert response["next_before_revision"] is None
    assert response["revisions"][-1]["objects"] == []
    assert response["revisions"][-1]["scope_completeness"] == "unreviewed"
    assert client.get(f"{BASE}/annotations").json()["annotation"]["revision"] == 5
    assert client.get(f"{BASE}/annotation-history?before_revision=1").json() == {
        "revisions": [],
        "next_before_revision": None,
    }


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "before_revision=0", "limit=bad"])
def test_invalid_history_pages_never_load(client, monkeypatch, query):
    as_human(client)
    engine = MagicMock(side_effect=AssertionError("invalid query database access"))
    monkeypatch.setattr(annotation_reads, "annotation_engine", engine)
    response = client.get(f"{BASE}/annotation-history?{query}")
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    engine.assert_not_called()


@pytest.mark.parametrize("suffix", ["annotations", "annotation-history"])
def test_database_failure_redacts_driver_details(client, monkeypatch, caplog, suffix):
    as_human(client)
    engine = MagicMock()
    engine.connect.side_effect = SQLAlchemyError("fixture-driver-detail-must-not-escape")
    monkeypatch.setattr(annotation_reads, "annotation_engine", lambda: engine)
    response = client.get(f"{BASE}/{suffix}")
    assert response.status_code == 503
    assert response.json() == {"detail": "annotations_unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert "fixture-driver-detail-must-not-escape" not in response.text + caplog.text


def test_read_routes_are_in_production_openapi_without_public_docs(client):
    paths = app.openapi()["paths"]
    for suffix, model in (
        ("annotations", "AnnotationSnapshot"),
        ("annotation-history", "AnnotationHistory"),
    ):
        route = f"/projects/{{project_key}}/assets/{{asset_key}}/{suffix}"
        assert set(paths[route]) == {"get"}
        schema = paths[route]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema == {"$ref": f"#/components/schemas/{model}"}
    assert client.get("/openapi.json").status_code == 401
