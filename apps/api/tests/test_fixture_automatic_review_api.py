"""Fixture-only HTTP authority; persisted guards run in Actions' PostgreSQL smoke."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import NoResultFound, SQLAlchemyError
from test_annotation_reads import BASE, as_human, fixture_token, read_path
from test_annotation_reads import authentication as authentication
from test_annotation_reads import canonical as canonical
from test_annotation_reads import client as client

from labelloop_api import annotation_reads, automatic_review_service
from labelloop_api.schema import annotation_revision
from labelloop_core.qualification_data import ScopeCell

PATH = BASE + "/fixture-automatic-reviews"
HEADERS = {"origin": "https://testserver", "x-csrf-token": "fixture-csrf"}


@pytest.fixture
def payload(monkeypatch):
    monkeypatch.setenv("AUTH_ORIGIN", HEADERS["origin"])
    return dict(
        recipe_key="full-letterbox-320-v1",
        expected_revision=4,
        expected_registry_revision=7,
        scope=[dict(class_id="item", source_id="camera", condition="daylight")],
        proposal_run_keys=["proposal-a", "proposal-b"],
    )


@pytest.fixture
def writer(monkeypatch):
    calls = []

    def append(connection, **arguments):
        assert connection.in_transaction()
        calls.append((connection, arguments))
        return 5

    monkeypatch.setattr(automatic_review_service, "append_current_review", append)
    return calls


@pytest.mark.parametrize("side", [320, 640])
def test_admin_uses_canonical_objects_session_actor_and_fixed_machine_authority(
    client, payload, writer, side
):
    as_human(client, "admin")
    payload["recipe_key"] = f"full-letterbox-{side}-v1"
    response = client.post(PATH, headers=HEADERS, json=payload)
    assert response.status_code == 201
    assert response.json() == {"revision": 5, "fixture_only": True}
    assert response.headers["cache-control"] == "no-store"
    connection, arguments = writer.pop()
    assert not connection.in_transaction()
    assert arguments == dict(
        project_key="domain-a",
        asset_key="source-a",
        recipe_key=payload["recipe_key"],
        actor_id="admin",
        expected_revision=4,
        expected_registry_revision=7,
        scope=(ScopeCell("item", "camera", "daylight"),),
        objects=[{"object_id": "target", "class_id": "item", "box": [1, 2, 30, 40]}],
        scope_completeness="exhaustive_for_scope",
        training_eligible=False,
        proposal_run_keys=("proposal-a", "proposal-b"),
    )


@pytest.mark.parametrize(
    "role", [None, "reviewer", "worker-core", "worker-ml", "worker-llm", "worker-platform"]
)
def test_denied_requests_never_open_canonical_database(client, payload, writer, monkeypatch, role):
    engine = MagicMock(side_effect=AssertionError("unauthorized canonical access"))
    monkeypatch.setattr(annotation_reads, "annotation_engine", engine)
    headers = dict(HEADERS)
    if role == "reviewer":
        as_human(client)
    elif role:
        headers["authorization"] = "Bearer " + fixture_token(role)
    response = client.post(PATH + "?role=admin", headers=headers, json=payload)
    assert response.status_code == (401 if role is None else 403)
    engine.assert_not_called()
    assert writer == []


@pytest.mark.parametrize(
    "headers", [{}, {"origin": "https://foreign.example", "x-csrf-token": "fixture-csrf"}]
)
def test_admin_cannot_bypass_csrf(client, payload, writer, headers):
    as_human(client, "admin")
    response = client.post(PATH, headers=headers, json=payload)
    assert response.status_code == 403
    assert writer == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_id", "another-principal"),
        ("objects", []),
        ("approval_state", "human_accepted"),
        ("review_tier", "adjudicated"),
        ("training_eligible", True),
        ("gold_eligible", True),
        ("qualification", {}),
        ("fixture_only", False),
        ("expected_revision", 0),
        ("expected_registry_revision", True),
        ("scope", []),
        (
            "scope",
            [{"class_id": "item", "source_id": "camera", "condition": "daylight", "grant": True}],
        ),
        ("proposal_run_keys", ["bad key"]),
    ],
)
def test_request_cannot_supply_geometry_or_authority(client, payload, writer, field, value):
    as_human(client, "admin")
    response = client.post(PATH, headers=HEADERS, json=payload | {field: value})
    assert response.status_code == 422
    assert writer == []


@pytest.mark.parametrize(
    "path", [read_path("domain-b", "source-a"), read_path("absent", "source-a")]
)
def test_foreign_scope_never_reaches_writer(client, payload, writer, path):
    as_human(client, "admin")
    response = client.post(path + "/fixture-automatic-reviews", headers=HEADERS, json=payload)
    assert response.status_code == 404
    assert writer == []


def test_stale_canonical_revision_never_reaches_writer(client, payload, writer):
    as_human(client, "admin")
    response = client.post(PATH, headers=HEADERS, json=payload | {"expected_revision": 3})
    assert response.status_code == 409
    assert writer == []


@pytest.mark.parametrize(
    "error,status,detail",
    [
        (ValueError, 409, "fixture_automatic_review_rejected"),
        (KeyError, 409, "fixture_automatic_review_rejected"),
        (TypeError, 409, "fixture_automatic_review_rejected"),
        (NoResultFound, 404, "automatic_review_scope_not_found"),
        (SQLAlchemyError, 503, "annotations_unavailable"),
    ],
)
def test_guard_or_post_write_failure_redacts_and_rolls_back_outer_transaction(
    client, canonical, payload, monkeypatch, error, status, detail
):
    with canonical.connect() as connection:
        before = connection.execute(select(annotation_revision.c.actor_id)).all()

    def fail(connection, **arguments):
        connection.execute(text("UPDATE annotation_revision SET actor_id = 'fixture-change'"))
        raise error("private fixture details must not be displayed")

    monkeypatch.setattr(automatic_review_service, "append_current_review", fail)
    as_human(client, "admin")
    response = client.post(PATH, headers=HEADERS, json=payload)
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == "no-store"
    with canonical.connect() as connection:
        assert connection.execute(select(annotation_revision.c.actor_id)).all() == before
