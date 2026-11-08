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
    annotation_replay,
    bounded_read,
    capture_objects,
    check_chains,
    fresh_fixture_bucket,
    registry_continuity,
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


def test_replay_interleaves_reviews_between_their_expected_revisions():
    tables = parse_manifest(
        empty_snapshot().manifest, expected_sha256=empty_snapshot().sha256
    ).tables
    tables["annotation_revision"].rows = [
        {"asset_id": "a", "revision": 1, "parent_revision": None},
        {"asset_id": "a", "revision": 2, "parent_revision": 1},
    ]
    tables["review_event"].rows = [{"asset_id": "a", "expected_revision": 1}]
    assert [name for name, _ in annotation_replay(tables)] == [
        "annotation_revision",
        "review_event",
        "annotation_revision",
    ]
    tables["review_event"].rows[0]["expected_revision"] = 0
    with pytest.raises(ValueError, match="review event"):
        check_chains(tables)


def test_registry_continuity_allows_only_append_only_evaluation_enrichment():
    model = {"release_id": "model", "weights_sha256": "a" * 64, "evaluation_run_ids": ["initial"]}
    previous = {"revision": 1, "champion": model, "rollback": None}
    current = previous | {"champion": model | {"evaluation_run_ids": ["initial", "appended"]}}
    assert registry_continuity(previous, current)
    assert not registry_continuity(current, previous)
    assert not registry_continuity(
        previous,
        current
        | {
            "champion": current["champion"] | {"weights_sha256": "b" * 64},
        },
    )


def test_missing_registry_head_fails_even_with_intact_events():
    snapshot = empty_snapshot()
    tables = parse_manifest(snapshot.manifest, expected_sha256=snapshot.sha256).tables
    tables["promotion_event"].rows = [
        {"project_id": "p", "recipe_id": "r", "revision": 1, "record": {"before": {}, "after": {}}}
    ]
    with pytest.raises(ValueError, match="registry head"):
        check_chains(tables)


def test_bounded_reader_never_requests_an_unbounded_body():
    response = MagicMock()
    response.read.return_value = b"oversized"
    with pytest.raises(ValueError, match="byte bound"):
        bounded_read(response, max_bytes=4)
    response.read.assert_called_once_with(5)


def test_capture_applies_remaining_aggregate_budget_before_reading_next_object():
    store = MagicMock()
    store.read_bounded.side_effect = [b"1234", b"5678"]
    with pytest.raises(ValueError, match="byte bound"):
        capture_objects({"a": {"original"}, "b": {"original"}}, {}, store, byte_budget=6)
    assert [call.kwargs["max_bytes"] for call in store.read_bounded.call_args_list] == [6, 2]


def test_rehydration_rejects_foreign_project_ontology_before_constructing_inputs():
    from labelloop_api.recovery_release import load_canonical_release

    release = MagicMock()
    release.mappings.return_value.one.return_value = {"project_id": "a", "ontology_id": "o"}
    project = MagicMock()
    project.scalar_one.return_value = "project-a"
    ontology = MagicMock()
    ontology.mappings.return_value.one.return_value = {"project_id": "b"}
    connection = MagicMock()
    connection.execute.side_effect = [release, project, ontology]
    with pytest.raises(ValueError, match="ontology.*project"):
        load_canonical_release(connection, "release")
    assert connection.execute.call_count == 3


def test_fixture_bucket_refuses_remote_or_mismatched_client_before_creation():
    client = MagicMock()
    for endpoint in ("storage.example:9000", "127.0.0.1:9000"):
        with pytest.raises(ValueError):
            with fresh_fixture_bucket(client, endpoint=endpoint):
                pytest.fail("unreachable")
    client.make_bucket.assert_not_called()
