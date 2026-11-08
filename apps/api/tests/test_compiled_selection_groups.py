"""Compiled validation influence is not independent final or fresh canary evidence."""

from unittest.mock import MagicMock

import pytest
from test_qualification_context_store import setup
from test_training_group_store import ancestor, fixture

from labelloop_api import final_test_store, training_group_store
from labelloop_api.training_group_store import ModelInputGroups


@pytest.mark.parametrize("edge", ["candidate", "checkpoint", "teacher", "mixed-cycle"])
def test_compiled_selection_includes_reachable_ancestors_not_tests_or_unrelated_runs(
    monkeypatch, edge
):
    connection, row, current = fixture(monkeypatch)
    parent = ancestor(row, "parent", "external", ["unselected"])
    teacher = ancestor(row, "teacher", "external", ["teacher-checkpoint"])
    unrelated = ancestor(row, "unrelated", "external", ["other"])
    expected = {"val-group"}
    for item in (parent, teacher, unrelated):
        key = item["id"] + "-val"
        item["sample_revisions"][key] = 1
        item["input_manifest"]["samples"].append(
            dict(asset_id=key, group_id=key + "-group", split="val", revision=1)
        )
    if edge in {"checkpoint", "mixed-cycle"}:
        row["record"]["request"]["initialization_sha256"] = "unselected"
        expected.add("parent-val-group")
    if edge in {"teacher", "mixed-cycle"}:
        training_group_store.label_teacher_runs.return_value = {"train": [(1, "teacher")]}
        expected.add("teacher-val-group")
    if edge == "mixed-cycle":
        row["record"]["checkpoints"] = [dict(sha256="child-checkpoint")]
        parent["record"]["request"]["initialization_sha256"] = "child-checkpoint"
    connection.execute.return_value.mappings.return_value.all.return_value = [
        row,
        parent,
        teacher,
        unrelated,
    ]
    current.side_effect = lambda *args, asset_keys, **kwargs: {
        key: key + "-group" for key in asset_keys
    }
    inputs = training_group_store.model_input_groups(
        connection, project_id="project", model_id="model"
    )
    assert set(inputs.selection) == expected
    assert "val-group" not in inputs.training
    assert "test-group" not in (*inputs.training, *inputs.selection)
    assert all("unrelated" not in call.kwargs["asset_keys"] for call in current.call_args_list)
    assert connection.execute.call_count == 2  # One ancestry read, not one graph per use.


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("port", ["assembly", "consumer"])
@pytest.mark.parametrize("role", ["final", "canary", "development", "unrelated"])
def test_compiled_selection_cannot_be_replayed_as_independent_final_or_fresh_canary(
    monkeypatch, side, port, role
):
    connection, arguments, expected, readers = setup(monkeypatch, side)
    evidence = expected.evidence
    group = {
        "final": evidence.final_test.dataset.group_ids[0],
        "canary": evidence.canary_audit.sources[0].group_id,
        "development": evidence.lineage.development.group_ids[0],
        "unrelated": "prior-development-group",
    }[role]
    inputs = ModelInputGroups(evidence.lineage.training_group_ids, (group,))
    readers["model_input_groups"].return_value = inputs
    if port == "assembly":
        from labelloop_api.qualification_context_store import load_qualification_context

        def consume():
            return load_qualification_context(connection, **arguments)

    else:
        monkeypatch.setattr(final_test_store, "lock_project", readers["lock_project"])
        monkeypatch.setattr(final_test_store, "load_final_test", readers["load_final_test"])
        monkeypatch.setattr(
            final_test_store, "require_development_uses", readers["require_development_uses"]
        )
        from labelloop_api import evaluation_group_store

        monkeypatch.setattr(
            evaluation_group_store, "load_evaluation_set", readers["load_evaluation_set"]
        )
        monkeypatch.setattr(
            training_group_store, "model_input_groups", MagicMock(return_value=inputs)
        )

        def consume():
            return final_test_store.current_qualification_context(
                connection, expected, model_id="model-id"
            )

    if role in {"final", "canary"}:
        with pytest.raises(ValueError, match="overlap compiled checkpoint selection"):
            consume()
    else:
        # Development may be shared across generations, but never become final or canary.
        assert consume() == expected
    assert not any(
        getattr(call.args[0], "is_insert", False) for call in connection.execute.call_args_list
    )
