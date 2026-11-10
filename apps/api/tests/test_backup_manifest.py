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
    check_evaluation_lineage,
    check_promotion_transition,
    check_recipe_lineage,
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


def _model_record(**overrides):
    return {
        "release_id": "model",
        "project_id": "p",
        "recipe_id": "r",
        "weights_sha256": "a" * 64,
        "preprocessing_sha256": "1" * 64,
        "class_map_sha256": "2" * 64,
        "class_ids": ["a"],
        "policy": {"interpolation": "linear"},
        "ultralytics_version": "8.4.204",
        "torch_version": "2.0",
        "export_sha256": "3" * 64,
        "export_format": "onnx",
        "static_input_signature": [1, 3, 320, 320],
        "evaluation_run_ids": ["eval-1"],
        "tiling_mode": "off",
        **overrides,
    }


def _registry_state(revision, champion=None, rollback=None):
    return {
        "project_id": "p",
        "recipe_id": "r",
        "revision": revision,
        "champion": champion,
        "rollback": rollback,
    }


def test_first_promotion_must_start_from_the_empty_registry_state():
    row = {
        "project_id": "p",
        "recipe_id": "r",
        "revision": 1,
        "action": "promote",
        "record": {
            "before": _registry_state(4, champion=_model_record()),
            "after": _registry_state(1, champion=_model_record()),
        },
    }
    with pytest.raises(ValueError, match="empty revision-zero"):
        check_promotion_transition(row, first=True, scope=("p", "r"))


def test_promotion_must_advance_to_a_distinct_champion():
    model = _model_record()
    row = {
        "project_id": "p",
        "recipe_id": "r",
        "revision": 2,
        "action": "promote",
        "record": {
            "before": _registry_state(1, champion=model),
            "after": _registry_state(2, champion=model, rollback=model),
        },
    }
    with pytest.raises(ValueError, match="distinct champion"):
        check_promotion_transition(row, first=False, scope=("p", "r"))


def test_valid_promotion_and_rollback_transitions_pass():
    first = _model_record(release_id="first")
    second = _model_record(release_id="second")
    check_promotion_transition(
        {
            "project_id": "p",
            "recipe_id": "r",
            "revision": 1,
            "action": "promote",
            "record": {
                "before": _registry_state(0),
                "after": _registry_state(1, champion=first),
            },
        },
        first=True,
        scope=("p", "r"),
    )
    check_promotion_transition(
        {
            "project_id": "p",
            "recipe_id": "r",
            "revision": 2,
            "action": "promote",
            "record": {
                "before": _registry_state(1, champion=first),
                "after": _registry_state(2, champion=second, rollback=first),
            },
        },
        first=False,
        scope=("p", "r"),
    )
    check_promotion_transition(
        {
            "project_id": "p",
            "recipe_id": "r",
            "revision": 3,
            "action": "rollback",
            "record": {
                "before": _registry_state(2, champion=second, rollback=first),
                "after": _registry_state(3, champion=first, rollback=second),
            },
        },
        first=False,
        scope=("p", "r"),
    )


def test_registry_payload_must_restate_the_restored_model_record():
    snapshot = empty_snapshot()
    tables = parse_manifest(snapshot.manifest, expected_sha256=snapshot.sha256).tables
    tables["evaluation_run"].rows = [
        {
            "id": "e1",
            "run_key": "eval-1",
            "training_run_id": "t",
            "variant_id": "v",
            "record": {
                "run_id": "eval-1",
                "weights_sha256": "a" * 64,
                "policy": {"interpolation": "linear"},
            },
        }
    ]
    model_row = {
        "id": "m",
        "release_key": "model",
        "training_run_id": "t",
        "project_id": "p",
        "recipe_id": "r",
        "record": _model_record(),
    }
    tables["model_release"].rows = [model_row]
    tables["model_evaluation"].rows = [{"model_id": "m", "evaluation_id": "e1"}]
    tables["promotion_event"].rows = [
        {
            "project_id": "p",
            "recipe_id": "r",
            "revision": 1,
            "action": "promote",
            "record": {
                "before": _registry_state(0),
                "after": _registry_state(1, champion=_model_record(weights_sha256="f" * 64)),
            },
        }
    ]
    with pytest.raises(ValueError, match="registry payload"):
        check_evaluation_lineage(tables)


def test_unresolved_embedded_evaluation_lineage_fails_before_target_access():
    snapshot = empty_snapshot()
    tables = parse_manifest(snapshot.manifest, expected_sha256=snapshot.sha256).tables
    tables["training_run"].rows = [
        {
            "id": "t",
            "run_key": "train-1",
            "variant_id": "v",
            "record": {
                "request": {"run_id": "train-1"},
                "checkpoints": [{"sha256": "a" * 64, "evaluation_run_id": "missing"}],
                "selected": {"evaluation_run_id": "missing"},
            },
        }
    ]
    with pytest.raises(ValueError, match="unresolved checkpoint evaluation"):
        check_evaluation_lineage(tables)


def test_model_evaluation_must_resolve():
    snapshot = empty_snapshot()
    tables = parse_manifest(snapshot.manifest, expected_sha256=snapshot.sha256).tables
    tables["model_release"].rows = [
        {
            "id": "m",
            "release_key": "model",
            "training_run_id": "t",
            "project_id": "p",
            "recipe_id": "r",
            "record": _model_record(evaluation_run_ids=["missing"]),
        }
    ]
    with pytest.raises(ValueError, match="unresolved model evaluation"):
        check_evaluation_lineage(tables)


def test_variant_recipe_policy_mismatch_fails_validation():
    snapshot = empty_snapshot()
    tables = parse_manifest(snapshot.manifest, expected_sha256=snapshot.sha256).tables
    policy = {"interpolation": "linear", "padding_color": [0, 0, 0]}
    tables["preprocessing_recipe"].rows = [{"id": "recipe", "recipe_key": "k", "policy": policy}]
    tables["yolo_dataset_variant"].rows = [
        {
            "id": "v",
            "recipe_id": "recipe",
            "class_map": {"a": 0},
            "file_manifest": {
                "recipe": policy | {"interpolation": "tampered"},
                "class_map": {"a": 0},
            },
        }
    ]
    with pytest.raises(ValueError, match="recipe"):
        check_recipe_lineage(tables)


def test_restored_recipe_chain_rejects_tampered_preprocessing_identity():
    policy = {"interpolation": "linear", "padding_color": [0, 0, 0]}
    file_manifest = {
        "recipe": policy,
        "class_map": {"a": 0},
        "file_hashes": {"class-map.json": "c" * 64},
    }
    manifest_digest = digest((json.dumps(file_manifest, sort_keys=True, indent=2) + "\n").encode())
    snapshot = empty_snapshot()
    tables = parse_manifest(snapshot.manifest, expected_sha256=snapshot.sha256).tables
    tables["preprocessing_recipe"].rows = [{"id": "recipe", "recipe_key": "k", "policy": policy}]
    tables["yolo_dataset_variant"].rows = [
        {"id": "v", "recipe_id": "recipe", "class_map": {"a": 0}, "file_manifest": file_manifest}
    ]
    tables["training_run"].rows = [
        {
            "id": "t",
            "run_key": "train-1",
            "variant_id": "v",
            "input_manifest": file_manifest,
            "record": {
                "request": {
                    "dataset_manifest_sha256": manifest_digest,
                    "class_map_sha256": "c" * 64,
                }
            },
        }
    ]
    tables["model_release"].rows = [
        {
            "id": "m",
            "release_key": "model",
            "training_run_id": "t",
            "project_id": "p",
            "recipe_id": "recipe",
            "record": _model_record(preprocessing_sha256="0" * 64, class_map_sha256="c" * 64),
        }
    ]
    with pytest.raises(ValueError, match="preprocessing identity"):
        check_recipe_lineage(tables)
    tables["model_release"].rows[0]["record"]["preprocessing_sha256"] = digest(encode(policy))
    check_recipe_lineage(tables)
    tables["training_run"].rows[0]["input_manifest"] = {
        **file_manifest,
        "class_map": {"a": 1},
    }
    with pytest.raises(ValueError, match="training input manifest"):
        check_recipe_lineage(tables)


def test_populated_chain_validates_with_uuid_foreign_keys_and_key_scoped_states():
    # Row columns carry UUID foreign keys; serialized states carry public keys.
    # Both domains must validate together on a populated capture.
    policy = {"interpolation": "linear", "padding_color": [0, 0, 0]}
    file_manifest = {
        "recipe": policy,
        "class_map": {"a": 0},
        "file_hashes": {"class-map.json": "c" * 64},
    }
    manifest_digest = digest((json.dumps(file_manifest, sort_keys=True, indent=2) + "\n").encode())
    model_record = _model_record(
        project_id="demo",
        recipe_id="full-320",
        class_map_sha256="c" * 64,
        preprocessing_sha256=digest(encode(policy)),
    )
    snapshot = empty_snapshot()
    tables = parse_manifest(snapshot.manifest, expected_sha256=snapshot.sha256).tables
    tables["project"].rows = [{"id": "11111111-1111-1111-1111-111111111111", "project_key": "demo"}]
    tables["preprocessing_recipe"].rows = [
        {"id": "22222222-2222-2222-2222-222222222222", "recipe_key": "full-320", "policy": policy}
    ]
    tables["yolo_dataset_variant"].rows = [
        {
            "id": "v",
            "recipe_id": "22222222-2222-2222-2222-222222222222",
            "class_map": {"a": 0},
            "file_manifest": file_manifest,
        }
    ]
    tables["training_run"].rows = [
        {
            "id": "t",
            "run_key": "train-1",
            "variant_id": "v",
            "input_manifest": file_manifest,
            "record": {
                "request": {
                    "run_id": "train-1",
                    "dataset_manifest_sha256": manifest_digest,
                    "class_map_sha256": "c" * 64,
                },
                "checkpoints": [
                    {
                        "epoch": 0,
                        "sha256": "a" * 64,
                        "validation_map": 0.5,
                        "evaluation_run_id": "eval-1",
                        "role": "development",
                    }
                ],
                "selected": {
                    "epoch": 0,
                    "sha256": "a" * 64,
                    "validation_map": 0.5,
                    "evaluation_run_id": "eval-1",
                    "role": "development",
                },
            },
        }
    ]
    tables["evaluation_run"].rows = [
        {
            "id": "e1",
            "run_key": "eval-1",
            "training_run_id": "t",
            "variant_id": "v",
            "record": {
                "run_id": "eval-1",
                "weights_sha256": "a" * 64,
                "policy": {"interpolation": "linear"},
            },
        }
    ]
    tables["model_release"].rows = [
        {
            "id": "m",
            "release_key": "model",
            "training_run_id": "t",
            "project_id": "11111111-1111-1111-1111-111111111111",
            "recipe_id": "22222222-2222-2222-2222-222222222222",
            "record": model_record,
        }
    ]
    tables["model_evaluation"].rows = [{"model_id": "m", "evaluation_id": "e1"}]
    tables["promotion_event"].rows = [
        {
            "project_id": "11111111-1111-1111-1111-111111111111",
            "recipe_id": "22222222-2222-2222-2222-222222222222",
            "revision": 1,
            "action": "promote",
            "record": {
                "before": {
                    "project_id": "demo",
                    "recipe_id": "full-320",
                    "revision": 0,
                    "champion": None,
                    "rollback": None,
                },
                "after": {
                    "project_id": "demo",
                    "recipe_id": "full-320",
                    "revision": 1,
                    "champion": dict(model_record),
                    "rollback": None,
                },
            },
        }
    ]
    tables["registry_head"].rows = [
        {
            "project_id": "11111111-1111-1111-1111-111111111111",
            "recipe_id": "22222222-2222-2222-2222-222222222222",
            "revision": 1,
            "champion_id": "m",
            "rollback_id": None,
        }
    ]
    check_chains(tables)


def _lineage_tables():
    """Populated chain with an appended evaluation linked to the champion."""
    snapshot = empty_snapshot()
    tables = parse_manifest(snapshot.manifest, expected_sha256=snapshot.sha256).tables
    tables["evaluation_run"].rows = [
        {
            "id": f"e{i}",
            "run_key": run_id,
            "training_run_id": "t",
            "variant_id": "v",
            "record": {
                "run_id": run_id,
                "weights_sha256": "a" * 64,
                "policy": {"interpolation": "linear"},
            },
        }
        for i, run_id in enumerate(["eval-1", "eval-2"])
    ]
    checkpoint = {
        "epoch": 0,
        "sha256": "a" * 64,
        "validation_map": 0.5,
        "evaluation_run_id": "eval-1",
        "role": "development",
    }
    tables["training_run"].rows = [
        {
            "id": "t",
            "run_key": "train-1",
            "variant_id": "v",
            "record": {
                "request": {"run_id": "train-1"},
                "checkpoints": [checkpoint],
                "selected": dict(checkpoint),
            },
        }
    ]
    model_record = _model_record(evaluation_run_ids=["eval-1"])
    tables["model_release"].rows = [
        {
            "id": "m",
            "release_key": "model",
            "training_run_id": "t",
            "project_id": "p",
            "recipe_id": "r",
            "record": model_record,
        }
    ]
    payload_record = dict(model_record, evaluation_run_ids=["eval-1", "eval-2"])
    tables["model_evaluation"].rows = [
        {"model_id": "m", "evaluation_id": "e0"},
        {"model_id": "m", "evaluation_id": "e1"},
    ]
    tables["promotion_event"].rows = [
        {
            "project_id": "p",
            "recipe_id": "r",
            "revision": 1,
            "action": "promote",
            "record": {
                "before": _registry_state(0),
                "after": _registry_state(1, champion=dict(payload_record)),
                "evaluation_key": "eval-2",
                "incumbent_evaluation_key": None,
            },
        }
    ]
    return tables


def test_appended_evaluation_link_deletion_fails_validation():
    tables = _lineage_tables()
    check_evaluation_lineage(tables)
    # Drop only the appended evaluation's link; its row and payloads remain.
    tables["model_evaluation"].rows = [{"model_id": "m", "evaluation_id": "e0"}]
    with pytest.raises(ValueError, match="not linked to the model release"):
        check_evaluation_lineage(tables)


def test_registry_payload_scope_mismatch_fails_validation():
    tables = _lineage_tables()
    # Payload/record agree with each other but belong to another registry.
    tables["model_release"].rows[0]["record"]["recipe_id"] = "other-recipe"
    tables["promotion_event"].rows[0]["record"]["after"]["champion"]["recipe_id"] = "other-recipe"
    with pytest.raises(ValueError, match="scope mismatch"):
        check_evaluation_lineage(tables)


def test_registry_payload_model_row_foreign_key_scope_mismatch_fails():
    tables = _lineage_tables()
    tables["project"].rows = [{"id": "p", "project_key": "p"}]
    tables["preprocessing_recipe"].rows = [
        {"id": "r", "recipe_key": "r", "policy": {}},
        {"id": "other", "recipe_key": "other-recipe", "policy": {}},
    ]
    # Row FK points at the other recipe while payload/record say "r".
    tables["model_release"].rows[0]["recipe_id"] = "other"
    with pytest.raises(ValueError, match="scope mismatch"):
        check_evaluation_lineage(tables)


def test_selected_checkpoint_hash_tamper_fails_validation():
    tables = _lineage_tables()
    tables["training_run"].rows[0]["record"]["selected"]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="selected checkpoint"):
        check_evaluation_lineage(tables)


def test_selected_checkpoint_must_be_deterministic_winner():
    tables = _lineage_tables()
    record = tables["training_run"].rows[0]["record"]
    # A higher-scoring checkpoint exists but is not selected.
    record["checkpoints"].append(
        {
            "epoch": 1,
            "sha256": "b" * 64,
            "validation_map": 0.9,
            "evaluation_run_id": "eval-2",
            "role": "development",
        }
    )
    record["checkpoints"][1]["sha256"] = "a" * 64
    with pytest.raises(ValueError, match="selected checkpoint"):
        check_evaluation_lineage(tables)
    # Selecting the winner (and the tie-break earliest epoch) passes.
    record["checkpoints"][0]["validation_map"] = 0.9
    record["selected"] = dict(record["checkpoints"][0])
    check_evaluation_lineage(tables)
