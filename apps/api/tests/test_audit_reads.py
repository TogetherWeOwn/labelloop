"""Real session/SQL audit projections; no paid calls or production resources."""

import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from test_auth import NOW, ORIGIN, login, worker_token
from test_auth import password_hash as password_hash
from test_auth import store as store

from labelloop_api import audit_reads
from labelloop_api import model_schema as models
from labelloop_api import schema as db
from labelloop_api.main import app

PATH = "/projects/audit-fixture/audit-log"
PROJECT = UUID("00000000-0000-0000-0000-000000000001")
FOREIGN = UUID("00000000-0000-0000-0000-000000000002")
ASSET = UUID("00000000-0000-0000-0000-000000000003")
OTHER_ASSET = UUID("00000000-0000-0000-0000-000000000004")


def review(connection, number, *, asset=ASSET, at=NOW, **changes):
    identity = UUID(int=number)
    connection.execute(
        sa.insert(db.review_event).values(
            dict(
                id=identity,
                asset_id=asset,
                actor_kind="system",
                actor_id="recorded-actor",
                decision="private-decision",
                expected_revision=1,
                correction={"private": "must-not-leak"},
                reason="must-not-leak",
                created_at=at,
            )
            | changes
        )
    )
    return identity


def promotion(connection, number, *, project=PROJECT, at=NOW):
    identity = UUID(int=number)
    connection.execute(
        sa.insert(models.promotion_event).values(
            id=identity,
            project_id=project,
            recipe_id=uuid4(),
            revision=number,
            actor_id="historical-actor",
            action="promote",
            record={"private": "must-not-leak"},
            created_at=at,
        )
    )
    return identity


@pytest.fixture
def http(store, monkeypatch):
    with store.engine.begin() as connection:
        db.project.create(connection)
        # SQLite supplies only the source identity columns used by the read. The
        # production PostgreSQL smoke retains the complete canonical schema.
        sa.Table(
            "source_asset",
            sa.MetaData(),
            sa.Column("id", sa.UUID, primary_key=True),
            sa.Column("project_id", sa.UUID, nullable=False),
        ).create(connection)
        db.review_event.create(connection)
        models.promotion_event.create(connection)
        for identity, key in ((PROJECT, "audit-fixture"), (FOREIGN, "foreign")):
            connection.execute(
                sa.insert(db.project).values(
                    id=identity,
                    project_key=key,
                    workspace_id=uuid4(),
                    task="detect",
                    scope="fixture",
                    domain="licence-clean",
                    created_at=NOW,
                )
            )
        connection.execute(
            sa.insert(db.source_asset).values(
                [
                    {"id": ASSET, "project_id": PROJECT},
                    {"id": OTHER_ASSET, "project_id": FOREIGN},
                ]
            )
        )
        review(connection, 10)
        review(connection, 20)
        promotion(connection, 30)
        review(connection, 40, asset=OTHER_ASSET)
        promotion(connection, 50, project=FOREIGN)
    monkeypatch.setattr(audit_reads, "auth_store", lambda: store)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client


def test_admin_metadata_projection_is_redacted_and_does_not_infer_promotion_actor(http):
    login(http, "admin")
    response = http.get(PATH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    page = response.json()
    assert page["total"] == 3 and page["next_after"] is None
    assert [event["id"] for event in page["events"]] == [str(UUID(int=n)) for n in (20, 10, 30)]
    assert page["events"][-1]["actor_kind"] == "unknown"
    assert page["events"][0] == {
        "id": str(UUID(int=20)),
        "at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "Review",
        "actor_id": "recorded-actor",
        "actor_kind": "system",
        "record_id": str(ASSET),
        "revision": 1,
        "sentence": "Recorded a review decision.",
    }
    assert "must-not-leak" not in response.text and "private-decision" not in response.text


def test_reviewer_restriction_applies_before_filters_counts_paging_and_export(http):
    login(http)
    page = http.get(PATH, params={"limit": 1}).json()
    assert page["total"] == 2 and len(page["events"]) == 1
    assert page["next_after"] == f"Review/{UUID(int=20)}"
    assert http.get(PATH, params={"after": page["next_after"]}).json()["events"][0]["id"] == str(
        UUID(int=10)
    )
    empty = http.get(PATH, params={"kind": "Promotion"}).json()
    assert empty["total"] == 0 and empty["events"] == [] and empty["next_after"] is None
    exported = http.get(PATH + "/export")
    assert exported.status_code == 200
    assert [json.loads(line)["kind"] for line in exported.text.splitlines()] == ["Review", "Review"]
    assert http.get(PATH + "/export", params={"kind": "Promotion"}).text == ""
    assert "must-not-leak" not in exported.text


def test_stable_keyset_ordering_and_filtered_export_equality(http, store):
    with store.engine.begin() as connection:
        review(connection, 60, at=NOW + timedelta(seconds=1))
    login(http, "admin")
    events = []
    after = None
    while True:
        page = http.get(PATH, params={"limit": 1} | ({"after": after} if after else {})).json()
        assert page["total"] == 4
        events.extend(page["events"])
        after = page["next_after"]
        if after is None:
            break
    assert [event["id"] for event in events] == [str(UUID(int=n)) for n in (60, 20, 10, 30)]
    params = {"kind": "Review", "since": (NOW + timedelta(seconds=1)).isoformat()}
    page = http.get(PATH, params=params).json()
    response = http.get(PATH + "/export", params=params)
    assert [json.loads(line) for line in response.text.splitlines()] == page["events"]
    assert len(page["events"]) == 1
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "after", ["invalid", "Review/not-a-uuid", f"Review/{UUID(int=40)}", f"Promotion/{UUID(int=30)}"]
)
def test_cursor_must_resolve_inside_authorized_filtered_query(http, after):
    login(http)
    response = http.get(PATH, params={"after": after})
    assert response.status_code == 409
    assert response.json() == {"detail": "audit_state_rejected"}


def test_cursor_filtered_out_is_not_reused(http):
    login(http, "admin")
    response = http.get(PATH, params={"kind": "Review", "after": f"Promotion/{UUID(int=30)}"})
    assert response.status_code == 409


@pytest.mark.parametrize("suffix", ["", "/export"])
@pytest.mark.parametrize(
    "name", [None, "worker-core", "worker-ml", "worker-llm", "worker-platform"]
)
def test_denied_before_audit_database_access(http, store, monkeypatch, name, suffix):
    def forbidden():
        raise AssertionError("unauthorized audit read")

    monkeypatch.setattr(audit_reads, "auth_store", forbidden)
    headers = {"authorization": "Bearer " + worker_token(store, name)} if name else {}
    response = http.get(PATH + suffix, headers=headers)
    assert response.status_code == (403 if name else 401)


@pytest.mark.parametrize("suffix", ["", "/export"])
def test_expired_session_cannot_read(http, monkeypatch, suffix):
    from labelloop_api import auth

    login(http, "admin")
    monkeypatch.setattr(auth, "clock", lambda: NOW + timedelta(days=1))
    assert http.get(PATH + suffix).status_code == 401


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"kind": "Connection"},
        {"after": "x" * 65},
        {"since": "not-a-date"},
    ],
)
def test_query_bounds(http, params):
    login(http)
    response = http.get(PATH, params=params)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}


@pytest.mark.parametrize(
    "params",
    [
        {"since": "2026-11-07T00:00:00"},
        {"since": "2026-11-08T00:00:00Z", "through": "2026-11-07T00:00:00Z"},
    ],
)
def test_ambiguous_or_inverted_dates_reject(http, params):
    login(http)
    assert http.get(PATH, params=params).status_code == 409
    assert http.get(PATH + "/export", params=params).status_code == 409


def test_missing_project_is_not_a_fabricated_empty_history(http):
    login(http)
    assert http.get("/projects/missing/audit-log").status_code == 404


@pytest.mark.parametrize("error", [ValueError("must-not-leak"), SQLAlchemyError("must-not-leak")])
@pytest.mark.parametrize("suffix", ["", "/export"])
def test_outer_failure_rolls_back_and_redacts(http, store, monkeypatch, error, suffix):
    def fail(connection, **kwargs):
        review(connection, 70)
        raise error

    monkeypatch.setattr(audit_reads, "load_audit_page", fail)
    login(http)
    response = http.get(PATH + suffix)
    assert response.status_code == (409 if isinstance(error, ValueError) else 503)
    assert "must-not-leak" not in response.text
    with store.engine.connect() as connection:
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(db.review_event)).scalar_one()
            == 3
        )


def test_export_refuses_overflow_instead_of_silent_truncation(http, store):
    with store.engine.begin() as connection:
        for number in range(100, 1099):
            review(connection, number)
    login(http)
    response = http.get(PATH + "/export")
    assert response.status_code == 409
    assert response.json() == {"detail": "audit_export_requires_narrower_range"}
    assert "content-disposition" not in response.headers


def test_openapi_has_typed_metadata_only_contract():
    contract = app.openapi()
    schema = contract["paths"]["/projects/{project_key}/audit-log"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/AuditMetadataPage"}
    properties = contract["components"]["schemas"]["AuditMetadata"]["properties"]
    assert set(properties) == {
        "id",
        "at",
        "kind",
        "actor_id",
        "actor_kind",
        "record_id",
        "revision",
        "sentence",
    }
