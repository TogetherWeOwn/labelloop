"""Admin historical metadata adapter; no new acceptance or execution authority."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from test_annotation_reads import BASE, NOW, as_human, fixture_token, read_path
from test_annotation_reads import authentication as authentication
from test_annotation_reads import canonical as canonical
from test_annotation_reads import client as client
from test_automatic_review_store import fixture

from labelloop_api import annotation_reads, auth, automatic_review_store
from labelloop_api.schema import annotation_revision
from labelloop_core.qualification import QualificationPolicy
from labelloop_core.qualification_data import record_sha256

PATH = BASE + "/annotation-history/2/automatic-acceptance"


@pytest.fixture(params=[320, 640])
def original(request, monkeypatch):
    _, context, _ = fixture(request.param)
    saved = automatic_review_store.StoredAutomaticAcceptance(
        context.evidence.qualification_id,
        context.evidence.release_sha256,
        "original-final-evaluation",
        QualificationPolicy(
            context.current_pipeline, context.evidence.scope, context.evidence.targets
        ),
        context.evidence.scope,
    )
    calls = []

    def load(connection, **arguments):
        assert connection.in_transaction()
        assert arguments == dict(project_key="domain-a", asset_key="source-a", revision=2)
        calls.append(connection)
        return saved

    monkeypatch.setattr(automatic_review_store, "load_automatic_acceptance", load)
    return saved, calls


def test_admin_reads_only_original_policy_and_release_metadata(client, original):
    saved, calls = original
    as_human(client, "admin")
    response = client.get(PATH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == dict(
        revision=2,
        historical_only=True,
        qualification_id=saved.qualification_id,
        release_sha256=saved.release_sha256,
        evaluation_key=saved.evaluation_key,
        pipeline_id=saved.policy.pipeline.pipeline_id,
        policy_sha256=record_sha256(saved.policy),
        fixture_only=True,
    )
    assert len(calls) == 1 and not calls[0].in_transaction()


@pytest.mark.parametrize(
    "role", [None, "reviewer", "worker-core", "worker-ml", "worker-llm", "worker-platform"]
)
def test_unauthorized_reads_never_open_canonical_database(client, original, monkeypatch, role):
    _, calls = original
    engine = MagicMock(side_effect=AssertionError("unauthorized canonical access"))
    monkeypatch.setattr(annotation_reads, "annotation_engine", engine)
    headers = {}
    if role == "reviewer":
        as_human(client)
    elif role:
        headers["authorization"] = "Bearer " + fixture_token(role)
    response = client.get(PATH + "?role=admin&principal=admin", headers=headers)
    assert response.status_code == (401 if role is None else 403)
    assert response.headers["cache-control"] == "no-store"
    assert calls == []
    engine.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        read_path("domain-b", "source-a"),
        read_path("absent", "source-a"),
        read_path("domain-a", "absent"),
    ],
)
def test_absent_or_foreign_source_never_reaches_original_reader(client, original, path):
    _, calls = original
    as_human(client, "admin")
    response = client.get(path + "/annotation-history/2/automatic-acceptance")
    assert response.status_code == 404
    assert response.json() == {"detail": "annotation_not_found"}
    assert calls == []


@pytest.mark.parametrize("revision", ["0", "1", "-1", "not-a-revision"])
def test_invalid_revision_is_rejected_before_original_read(client, original, revision):
    _, calls = original
    as_human(client, "admin")
    response = client.get(BASE + f"/annotation-history/{revision}/automatic-acceptance")
    assert response.status_code == 422
    assert calls == []


@pytest.mark.parametrize(
    "error,status,detail",
    [
        (ValueError, 409, "automatic_acceptance_provenance_unavailable"),
        (KeyError, 409, "automatic_acceptance_provenance_unavailable"),
        (TypeError, 409, "automatic_acceptance_provenance_unavailable"),
        (SQLAlchemyError, 503, "annotations_unavailable"),
    ],
)
def test_missing_or_malformed_provenance_fails_closed_with_outer_rollback(
    client, canonical, monkeypatch, error, status, detail
):
    with canonical.connect() as connection:
        before = connection.execute(select(annotation_revision.c.actor_id)).all()

    def fail(connection, **arguments):
        connection.execute(
            text("UPDATE annotation_revision SET actor_id = 'private-fixture-change'")
        )
        raise error("private provider payload must not be displayed")

    monkeypatch.setattr(automatic_review_store, "load_automatic_acceptance", fail)
    as_human(client, "admin")
    response = client.get(PATH)
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == "no-store"
    with canonical.connect() as connection:
        assert connection.execute(select(annotation_revision.c.actor_id)).all() == before


def test_expired_admin_session_cannot_reuse_metadata_read(client, original, monkeypatch):
    _, calls = original
    as_human(client, "admin")
    monkeypatch.setattr(auth, "clock", lambda: NOW + timedelta(hours=2))
    response = client.get(PATH)
    assert response.status_code == 401
    assert calls == []
