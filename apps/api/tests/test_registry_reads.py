"""Session/admin and single-transaction registry/drift read adapter controls."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import NoResultFound, SQLAlchemyError
from test_auth import ORIGIN, login, worker_token
from test_auth import password_hash as password_hash
from test_auth import store as store
from test_m5_integration_contract import make_release

from labelloop_api import registry_api
from labelloop_api.main import app
from labelloop_api.model_store import json_record
from labelloop_core.drift import AcceptanceState
from labelloop_core.qualification_data import ScopeCell
from labelloop_core.registry import RegistryState

PROJECT = "synthetic-qualification"


def path(side=320, project=PROJECT):
    return f"/projects/{project}/registry/full-letterbox-{side}-v1"


@pytest.fixture(params=[320, 640])
def reads(request, monkeypatch, store):
    side = request.param
    release = make_release(side, "challenger")
    registry = RegistryState(
        PROJECT, release.recipe_id, 2, release, make_release(side, "incumbent")
    )
    acceptance = AcceptanceState(PROJECT, release.recipe_id).pause(
        release,
        pause_id="drift-incident",
        qualification_id="historical-qualification",
        scope=(ScopeCell(release.class_ids[0], "source-camera", "low-light"),),
        reason="independent audit regression",
        expected_revision=0,
    )
    calls = []

    def load_acceptance(connection, **kwargs):
        assert connection.in_transaction()
        assert kwargs == {"project_key": PROJECT, "recipe_key": release.recipe_id}
        assert calls == []
        calls.append(connection)
        return acceptance

    def load_registry(connection, project_key, recipe_key):
        assert connection.in_transaction()
        assert (project_key, recipe_key) == (PROJECT, release.recipe_id)
        assert calls == [connection]
        calls.append(connection)
        return registry

    monkeypatch.setattr(registry_api, "auth_store", lambda: store)
    monkeypatch.setattr(registry_api.acceptance_store, "load_acceptance", load_acceptance)
    monkeypatch.setattr(registry_api.model_store, "load_registry", load_registry)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, side, registry, acceptance, calls


def test_admin_reads_complete_registry_and_drift_in_one_transaction(reads):
    client, side, registry, acceptance, calls = reads
    login(client, "admin")
    response = client.get(path(side))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "registry": json_record(registry),
        "acceptance": json_record(acceptance),
    }
    assert len(calls) == 2
    assert not calls[0].in_transaction()


def test_empty_history_does_not_claim_qualification(reads, monkeypatch):
    client, side, registry, _, _ = reads
    monkeypatch.setattr(
        registry_api.acceptance_store,
        "load_acceptance",
        lambda *args, **kwargs: AcceptanceState(PROJECT, registry.recipe_id),
    )
    monkeypatch.setattr(
        registry_api.model_store,
        "load_registry",
        lambda *args: RegistryState(PROJECT, registry.recipe_id),
    )
    login(client, "admin")
    response = client.get(path(side))
    assert response.status_code == 200
    assert response.json() == {
        "registry": json_record(RegistryState(PROJECT, registry.recipe_id)),
        "acceptance": json_record(AcceptanceState(PROJECT, registry.recipe_id)),
    }
    assert "qualified" not in response.text


def test_resumed_incident_retains_lineage(reads, monkeypatch):
    client, side, _, acceptance, calls = reads
    resumed = replace(
        acceptance,
        revision=2,
        pauses=(
            replace(
                acceptance.pauses[0],
                resumed_by_qualification_id="new-qualification",
                resumed_by_release_id="new-release",
            ),
        ),
    )

    def load(connection, **kwargs):
        calls.append(connection)
        return resumed

    monkeypatch.setattr(registry_api.acceptance_store, "load_acceptance", load)
    login(client, "admin")
    response = client.get(path(side))
    assert response.status_code == 200
    assert response.json()["acceptance"] == json_record(resumed)


def test_anonymous_cannot_read_registry(reads):
    client, side, _, _, calls = reads
    assert client.get(path(side)).status_code == 401
    assert calls == []


def test_reviewer_cannot_read_admin_operations_even_with_role_query(reads):
    client, side, _, _, calls = reads
    login(client)
    assert client.get(path(side) + "?role=admin&principal=admin").status_code == 403
    assert calls == []


@pytest.mark.parametrize("worker", ["worker-core", "worker-ml", "worker-llm", "worker-platform"])
def test_worker_cannot_read_admin_operations(reads, store, worker):
    client, side, _, _, calls = reads
    response = client.get(
        path(side), headers={"authorization": "Bearer " + worker_token(store, worker)}
    )
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize("field", ["project", "recipe"])
def test_path_identity_is_validated_before_read(reads, field):
    client, side, _, _, calls = reads
    login(client, "admin")
    target = path(side, project="x" * 129) if field == "project" else path(side) + "x" * 129
    response = client.get(target)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert calls == []


@pytest.mark.parametrize("port", ["acceptance", "registry"])
@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (NoResultFound("private scope"), 404, "registry_scope_not_found"),
        (ValueError("private journal context"), 409, "registry_state_rejected"),
        (SQLAlchemyError("private driver context"), 503, "registry_unavailable"),
    ],
)
def test_partial_read_never_returns_partial_or_private_data(
    reads, monkeypatch, port, error, status, detail
):
    client, side, _, _, _ = reads

    def fail(*args, **kwargs):
        raise error

    owner = registry_api.acceptance_store if port == "acceptance" else registry_api.model_store
    monkeypatch.setattr(owner, "load_" + port, fail)
    login(client, "admin")
    response = client.get(path(side))
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == "no-store"
