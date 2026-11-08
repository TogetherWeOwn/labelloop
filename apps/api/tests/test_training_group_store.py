"""Compiled training group provenance; adapters are not PostgreSQL acceptance."""

from copy import deepcopy
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
        id="run",
        record=dict(request=dict(initialization_sha256="external"), checkpoints=[]),
        input_manifest=dict(release_id="release", split_assignment_version="v1", samples=samples),
        release_key="release",
        assignment_version="v1",
        sample_revisions={item["asset_id"]: 1 for item in samples},
        frozen=True,
    )
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = "run"
    connection.execute.return_value.mappings.return_value.all.return_value = [row]
    current = MagicMock(return_value={item["asset_id"]: item["group_id"] for item in samples})
    monkeypatch.setattr(store, "current_release_groups", current)
    monkeypatch.setattr(store, "label_teacher_runs", MagicMock(return_value={}))
    return connection, row, current


def test_loads_candidate_scoped_compiled_train_only_and_checks_all_current_sources(monkeypatch):
    connection, row, current = fixture(monkeypatch)
    row["input_manifest"]["samples"].append(dict(row["input_manifest"]["samples"][0]))
    assert store.model_training_groups(connection, project_id="project", model_id="model") == (
        "train-group",
    )
    statements = [call.args[0] for call in connection.execute.call_args_list]
    assert statements[0].compile().params == dict(id_1="model", project_id_1="project")
    assert statements[1].compile().params == dict(project_id_1="project")
    assert "canonical_dataset_release.frozen" in str(statements[1])
    current.assert_called_once_with(
        connection,
        project_id="project",
        assignment_version="v1",
        asset_keys=frozenset({"train", "val", "test"}),
    )
    store.label_teacher_runs.assert_called_once_with(connection, "project")
    assert connection.execute.call_count == 2


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
        "not-frozen",
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
    elif mutation == "not-frozen":
        row["frozen"] = False
    else:
        sample["split"] = "val"
    with pytest.raises(ValueError):
        store.model_training_groups(connection, project_id="project", model_id="model")
    assert not any(
        getattr(call.args[0], "is_insert", False) for call in connection.execute.call_args_list
    )


def ancestor(row, key, initialization, checkpoints):
    result = deepcopy(row)
    result["id"] = key
    result["record"] = dict(
        request=dict(initialization_sha256=initialization),
        checkpoints=[dict(sha256=digest) for digest in checkpoints],
    )
    # Distinct canonical inputs let an omitted ancestor's exposure be observable.
    result["sample_revisions"] = {key: 1}
    result["input_manifest"]["samples"] = [
        dict(asset_id=key, group_id=key + "-group", split="train", revision=1)
    ]
    return result


@pytest.mark.parametrize("edge", ["checkpoint", "teacher", "mixed-cycle", "duplicate-hash"])
def test_inherited_groups_include_unregistered_runs_and_all_teachers(monkeypatch, edge):
    connection, row, current = fixture(monkeypatch)
    parent = ancestor(row, "parent", "external", ["selected", "unselected"])
    teacher = ancestor(row, "teacher", "external", ["teacher-checkpoint"])
    unrelated = ancestor(row, "unrelated", "external", ["other"])
    expected = {"train-group"}
    if edge in {"checkpoint", "mixed-cycle", "duplicate-hash"}:
        row["record"]["request"]["initialization_sha256"] = "unselected"
        expected.add("parent-group")
    if edge in {"teacher", "mixed-cycle"}:
        store.label_teacher_runs.return_value = {"train": [(1, "teacher"), (1, "parent")]}
        expected.update({"teacher-group", "parent-group"})
    if edge == "mixed-cycle":
        teacher["record"]["request"]["initialization_sha256"] = "unselected"
        row["record"]["checkpoints"] = [dict(sha256="child-checkpoint")]
        parent["record"]["request"]["initialization_sha256"] = "child-checkpoint"
    if edge == "duplicate-hash":
        teacher["record"]["checkpoints"] = [dict(sha256="unselected")]
        expected.add("teacher-group")
    connection.execute.return_value.mappings.return_value.all.return_value = [
        row,
        parent,
        teacher,
        unrelated,
    ]
    current.side_effect = lambda *args, asset_keys, **kwargs: {
        key: key + "-group" for key in asset_keys
    }
    assert (
        set(store.model_training_groups(connection, project_id="project", model_id="model"))
        == expected
    )
    assert current.call_count == len(expected)
    assert all("unrelated" not in call.kwargs["asset_keys"] for call in current.call_args_list)


@pytest.mark.parametrize(
    "mutation", ["future-teacher", "missing-teacher", "bad-ancestor", "missing-run"]
)
def test_revision_boundaries_and_incomplete_ancestor_inputs(monkeypatch, mutation):
    connection, row, current = fixture(monkeypatch)
    if mutation == "missing-run":
        connection.execute.return_value.scalar_one.return_value = "foreign"
    elif mutation == "bad-ancestor":
        parent = ancestor(row, "parent", "external", ["parent-checkpoint"])
        parent["input_manifest"]["samples"][0]["revision"] = 2
        row["record"]["request"]["initialization_sha256"] = "parent-checkpoint"
        connection.execute.return_value.mappings.return_value.all.return_value = [row, parent]
    else:
        store.label_teacher_runs.return_value = {
            "train": [(2 if mutation == "future-teacher" else 1, "foreign")]
        }
    if mutation == "future-teacher":
        assert store.model_training_groups(connection, project_id="project", model_id="model") == (
            "train-group",
        )
        current.assert_called_once()
    else:
        with pytest.raises(ValueError):
            store.model_training_groups(connection, project_id="project", model_id="model")
