import json
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from labelloop_api.backup_manifest import (
    BackupManifest,
    EncryptionMetadata,
    TableInventory,
    audit_seal,
    digest,
    encode,
    object_key,
    parse_manifest,
    reject_transport_secrets,
)
from labelloop_api.backup_store import (
    AUDIT_TABLES,
    COLUMNS,
    TABLES,
    Snapshot,
    fresh_fixture_bucket,
    restore_fixture,
    validate_snapshot,
)
from labelloop_compiler.compiler import _toolchain


def empty_snapshot():
    manifest = BackupManifest(
        toolchain=_toolchain(),
        encryption=EncryptionMetadata(),
        tables={
            name: TableInventory(columns=columns.split(), rows=[], sha256=digest(encode([])))
            for name, columns in COLUMNS.items()
        },
        objects={},
        audit={name: audit_seal([]) for name in AUDIT_TABLES},
    )
    return Snapshot(encode(manifest.model_dump()), {})


def test_registered_inventory_covers_every_delivered_table_and_column():
    assert set(COLUMNS) == set(TABLES)
    for name, columns in COLUMNS.items():
        assert columns.split() == list(TABLES[name].c.keys())
    snapshot = empty_snapshot()
    assert validate_snapshot(snapshot, expected_sha256=snapshot.sha256).encryption.fixture_only


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(format_version=2),
        lambda d: d.update(schema_revision="stale"),
        lambda d: d.update(inventory_version="stale"),
        lambda d: d["toolchain"].update(pillow="stale"),
        lambda d: d["tables"].pop("review_event"),
        lambda d: d["tables"]["project"]["columns"].append("future"),
        lambda d: d["tables"]["project"].update(sha256="0" * 64),
        lambda d: d["audit"].pop("review_event"),
        lambda d: d["audit"]["review_event"].update(count=1),
        lambda d: d["encryption"].update(provider_and_private_keys_included=True),
        lambda d: d.update(extra="unknown"),
    ],
)
def test_stale_incomplete_or_corrupt_manifest_fails_before_target_access(mutation):
    snapshot = empty_snapshot()
    document = json.loads(snapshot.manifest)
    mutation(document)
    corrupted = replace(snapshot, manifest=encode(document))
    connection = MagicMock()
    target = MagicMock()
    with pytest.raises(ValueError):
        restore_fixture(connection, target, corrupted, expected_sha256=corrupted.sha256)
    connection.execute.assert_not_called()
    target.put.assert_not_called()


def test_manifest_root_must_match_out_of_band_digest():
    snapshot = empty_snapshot()
    with pytest.raises(ValueError, match="manifest digest"):
        parse_manifest(snapshot.manifest + b" ", expected_sha256=snapshot.sha256)


@pytest.mark.parametrize(
    "value",
    [
        {"api_key": "redacted"},
        {"payload": {"private_key": "redacted"}},
        "-----BEGIN PRIVATE KEY-----",
        "ul_fixture",
        "sk-fixture",
        "Bearer fixture",
        "https://storage.example/object?X-Amz-Signature=fixture",
        "https://user:fixture@storage.example/object",
    ],
)
def test_transport_and_key_material_are_not_durable_identity(value):
    with pytest.raises(ValueError):
        reject_transport_secrets(value)


def test_object_keys_never_accept_paths_or_urls():
    for invalid in ("../escape", "https://storage.example/file", "A" * 64, "0" * 63):
        with pytest.raises(ValueError):
            object_key(invalid)


def test_fixture_bucket_refuses_remote_or_mismatched_client_before_creation():
    client = MagicMock()
    for endpoint in ("storage.example:9000", "127.0.0.1:9000"):
        with pytest.raises(ValueError):
            with fresh_fixture_bucket(client, endpoint=endpoint):
                pytest.fail("unreachable")
    client.make_bucket.assert_not_called()
