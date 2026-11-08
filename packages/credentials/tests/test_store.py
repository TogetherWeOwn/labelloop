import json
from dataclasses import asdict, replace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from labelloop_api.main import app
from labelloop_credentials.envelope import CredentialError, PublicSealer, SecretType
from labelloop_credentials.store import audit_event, connection_head, credential_version
from labelloop_worker_llm.credentials import LLMCredentials
from labelloop_worker_platform.credentials import PlatformCredentials

PLAINTEXT = b"fixture-platform-value-7239"


def worker(key_material):
    return PlatformCredentials(
        {"LABELLOOP_PLATFORM_UNWRAP_PRIVATE_PEM": key_material[SecretType.PLATFORM][0].decode()}
    )


def stage(store, sealer, scope, connection_id, version=1, expected=0):
    return store.stage(
        sealer.seal(scope.identity(connection_id, version), PLAINTEXT),
        expected_active=expected,
        actor_id="fixture-admin-principal",
    )


def resolve_storage_intent(store, scope, connection_id, version, valid=True):
    # Storage state-machine input only, not a vendor response or worker qualification.
    return store.finish_validation(
        scope, connection_id, version, valid=valid, actor_id="fixture-storage-principal"
    )


def test_stage_survives_restart_but_is_not_active(store, sealer, scope, db, key_material):
    connection_id = uuid4()
    public = stage(store, sealer, scope, connection_id)
    with pytest.raises(CredentialError):
        store.lookup(scope, connection_id)
    assert resolve_storage_intent(store, scope, connection_id, 1) == "activated"
    # New store object, same persisted database; no process-local credential cache.
    restarted = type(store)(db)
    value = worker(key_material).lookup(restarted, scope, connection_id)
    assert value.use(lambda key: key == PLAINTEXT)
    assert restarted.public(scope, connection_id) == (public,)
    rows = db.execute(sa.select(audit_event).order_by(audit_event.c.action)).mappings().all()
    assert {r["action"] for r in rows} == {"validation_requested", "activated"}
    assert {r["actor_id"] for r in rows} == {"fixture-admin-principal", "fixture-storage-principal"}
    assert {r["version"] for r in rows} == {1}
    assert PLAINTEXT.decode() not in json.dumps(asdict(public), default=str)
    for table in (connection_head, credential_version, audit_event):
        assert PLAINTEXT.decode() not in repr(db.execute(sa.select(table)).all())


@pytest.mark.parametrize("active_version", [0, 1])
def test_platform_identity_unverifiable_is_terminal_and_audited(
    active_version, store, sealer, scope, db, key_material, monkeypatch, caplog
):
    connection_id = uuid4()
    adapter = worker(key_material)
    stage(store, sealer, scope, connection_id)
    if active_version:
        # Simulate an already authorized historical key at the persistence boundary.
        # No fixture claims the current vendor contract can enroll it.
        resolve_storage_intent(store, scope, connection_id, 1)
        stage(store, sealer, scope, connection_id, 2, 1)
    version = active_version + 1

    def unexpected_unwrap(*args):
        pytest.fail("unverifiable identity must not decrypt the candidate")

    monkeypatch.setattr(adapter._unwrap, "unwrap", unexpected_unwrap)
    assert adapter.validate(store, scope, connection_id, version) == "identity_unverifiable"
    assert db.execute(sa.select(connection_head.c.active_version)).scalar_one() == active_version
    assert (
        db.execute(
            sa.select(credential_version.c.intent_state).where(
                credential_version.c.version == version
            )
        ).scalar_one()
        == "identity_unverifiable"
    )
    event = (
        db.execute(sa.select(audit_event).where(audit_event.c.action == "identity_unverifiable"))
        .mappings()
        .one()
    )
    assert event["actor_id"] == "worker-platform"
    assert event["connection_id"] == connection_id
    assert event["version"] == version
    assert PLAINTEXT.decode() not in repr(event)
    assert PLAINTEXT.decode() not in caplog.text
    restarted = type(store)(db)
    with pytest.raises(CredentialError):
        adapter.validate(restarted, scope, connection_id, version)
    with pytest.raises(CredentialError):
        restarted.lookup(scope, connection_id, pending_version=version)
    if active_version:
        assert restarted.lookup(scope, connection_id).identity.version == active_version
    else:
        with pytest.raises(CredentialError):
            restarted.lookup(scope, connection_id)


def test_failed_replacement_never_displaces_working_key(store, sealer, scope, db):
    connection_id = uuid4()
    stage(store, sealer, scope, connection_id)
    resolve_storage_intent(store, scope, connection_id, 1)
    stage(store, sealer, scope, connection_id, 2, 1)
    assert resolve_storage_intent(store, scope, connection_id, 2, valid=False) == "rejected"
    assert store.lookup(scope, connection_id).identity.version == 1
    assert (
        db.execute(
            sa.select(credential_version.c.intent_state).where(credential_version.c.version == 2)
        ).scalar_one()
        == "rejected"
    )
    with pytest.raises(CredentialError):
        resolve_storage_intent(store, scope, connection_id, 2)


def test_racing_rotation_cannot_replay_or_roll_back(store, sealer, scope):
    connection_id = uuid4()
    stage(store, sealer, scope, connection_id)
    resolve_storage_intent(store, scope, connection_id, 1)
    stage(store, sealer, scope, connection_id, 2, 1)
    stage(store, sealer, scope, connection_id, 3, 1)
    assert resolve_storage_intent(store, scope, connection_id, 3) == "activated"
    assert resolve_storage_intent(store, scope, connection_id, 2) == "stale"
    assert store.lookup(scope, connection_id).identity.version == 3
    with pytest.raises(CredentialError):
        resolve_storage_intent(store, scope, connection_id, 3)
    with pytest.raises(CredentialError):
        stage(store, sealer, scope, connection_id, 4, 1)


@pytest.mark.parametrize("valid,denial", [(True, "identity_unverifiable"), (False, "unsafe text")])
def test_denial_code_cannot_activate_or_persist_arbitrary_text(
    valid, denial, store, sealer, scope, db
):
    connection_id = uuid4()
    stage(store, sealer, scope, connection_id)
    with pytest.raises(CredentialError):
        store.finish_validation(
            scope, connection_id, 1, valid=valid, actor_id="worker-platform", denial=denial
        )
    assert db.execute(sa.select(credential_version.c.intent_state)).scalar_one() == "pending"
    assert db.execute(sa.select(sa.func.count()).select_from(audit_event)).scalar_one() == 1


@pytest.mark.parametrize("field", ["project_id", "workspace_id", "secret_type"])
def test_scope_substitution_fails_before_validation(field, store, sealer, scope, key_material):
    connection_id = uuid4()
    stage(store, sealer, scope, connection_id)
    substituted = replace(scope, **{field: SecretType.LLM if field == "secret_type" else uuid4()})
    with pytest.raises(CredentialError):
        worker(key_material).validate(store, substituted, connection_id, 1)
    with pytest.raises(CredentialError):
        store.public(substituted, connection_id)
    # A rejected scope has not consumed the actual pending intent.
    assert store.lookup(scope, connection_id, pending_version=1).identity.version == 1


def test_failed_stage_is_atomic(store, sealer, scope, db):
    connection_id = uuid4()
    with pytest.raises(CredentialError):
        store.stage(
            sealer.seal(scope.identity(connection_id, 1), PLAINTEXT), expected_active=0, actor_id=""
        )
    assert db.execute(sa.select(sa.func.count()).select_from(connection_head)).scalar_one() == 0
    assert db.execute(sa.select(sa.func.count()).select_from(credential_version)).scalar_one() == 0
    assert db.execute(sa.select(sa.func.count()).select_from(audit_event)).scalar_one() == 0


def test_llm_worker_only_uses_its_own_half(store, scope, key_material):
    scope = replace(scope, secret_type=SecretType.LLM)
    sealer = PublicSealer(SecretType.LLM, key_material[SecretType.LLM][1])
    connection_id = uuid4()
    stage(store, sealer, scope, connection_id)
    adapter = LLMCredentials(
        {"LABELLOOP_LLM_UNWRAP_PRIVATE_PEM": key_material[SecretType.LLM][0].decode()}
    )
    assert adapter.validate(store, scope, connection_id, 1) == "activated"
    assert adapter.lookup(store, scope, connection_id).use(lambda key: key == PLAINTEXT)
    with pytest.raises(CredentialError):
        worker(key_material).lookup(store, scope, connection_id)


def test_privileged_http_routes_remain_unavailable():
    client = TestClient(app)
    for path in ("/connections", "/credentials", f"/projects/{uuid4()}/connections"):
        response = client.post(path, json={"key": PLAINTEXT.decode()})
        assert response.status_code in (401, 403, 404, 405)
        assert PLAINTEXT.decode() not in response.text
