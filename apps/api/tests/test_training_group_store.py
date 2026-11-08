"""Compiled training group provenance; adapters are not PostgreSQL acceptance."""

from unittest.mock import MagicMock

import pytest

from labelloop_api import training_group_store as store


def fixture(monkeypatch):
    samples = [
        dict(asset_id=asset, group_id=group, split=split, revision=1)
        for asset, group, split in (
            ("train", "train-group", "train"),
            ("val", "val-group", "val"),
            ("test", "test-group", "test"),
        )
    ]
    row = dict(
        input_manifest=dict(release_id="release", split_assignment_version="v1", samples=samples),
        release_key="release",
        assignment_version="v1",
        sample_revisions={item["asset_id"]: 1 for item in samples},
    )
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.one.return_value = row
    current = MagicMock(return_value={item["asset_id"]: item["group_id"] for item in samples})
    monkeypatch.setattr(store, "current_release_groups", current)
    return connection, row, current


def test_loads_candidate_scoped_compiled_train_only_and_checks_all_current_sources(monkeypatch):
    connection, row, current = fixture(monkeypatch)
    row["input_manifest"]["samples"].append(dict(row["input_manifest"]["samples"][0]))
    assert store.model_training_groups(connection, project_id="project", model_id="model") == (
        "train-group",
    )
    statement = connection.execute.call_args.args[0]
    assert statement.compile().params == dict(
        id_1="model", project_id_1="project", project_id_2="project"
    )
    assert "canonical_dataset_release.frozen" in str(statement)
    current.assert_called_once_with(
        connection,
        project_id="project",
        assignment_version="v1",
        asset_keys=frozenset({"train", "val", "test"}),
    )
    assert connection.execute.call_count == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "manifest",
        "release",
        "version",
        "samples",
        "empty",
        "sample",
        "asset",
        "group",
        "split",
        "foreign",
        "revision",
        "boolean-revision",
        "membership",
        "derivative-group",
        "derivative-split",
        "current",
        "current-conflict",
        "no-train",
    ],
)
def test_invalid_or_substituted_compiled_sources_fail_closed(monkeypatch, mutation):
    connection, row, current = fixture(monkeypatch)
    manifest = row["input_manifest"]
    sample = manifest["samples"][0]
    if mutation == "manifest":
        row["input_manifest"] = None
    elif mutation in {"release", "version"}:
        manifest["release_id" if mutation == "release" else "split_assignment_version"] = "other"
    elif mutation == "samples":
        manifest["samples"] = {}
    elif mutation == "empty":
        manifest["samples"] = []
    elif mutation == "sample":
        manifest["samples"][0] = None
    elif mutation in {"asset", "group"}:
        sample["asset_id" if mutation == "asset" else "group_id"] = " "
    elif mutation == "split":
        sample["split"] = "calibration"
    elif mutation == "foreign":
        sample["asset_id"] = "foreign"
    elif mutation in {"revision", "boolean-revision"}:
        sample["revision"] = 2 if mutation == "revision" else True
    elif mutation == "membership":
        row["sample_revisions"] = []
    elif mutation.startswith("derivative-"):
        field = "group_id" if mutation == "derivative-group" else "split"
        manifest["samples"].append(sample | {field: "other" if field == "group_id" else "val"})
    elif mutation == "current":
        current.return_value = {"train": "other", "val": "val-group", "test": "test-group"}
    elif mutation == "current-conflict":
        current.side_effect = ValueError("canonical relation conflict")
    else:
        sample["split"] = "val"
    with pytest.raises(ValueError):
        store.model_training_groups(connection, project_id="project", model_id="model")
    assert not any(
        getattr(call.args[0], "is_insert", False) for call in connection.execute.call_args_list
    )
