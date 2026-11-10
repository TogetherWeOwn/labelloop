"""Persisted proposal references are influence, not independent evaluation truth."""

import copy
import hashlib
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from test_proposal_origin import fixture as proposal_fixture
from test_qualification_context_store import setup

from labelloop_api import evaluation_group_store, final_test_store, generated_label_store
from labelloop_api import training_group_store as store
from labelloop_core.json_records import canonical_json


@pytest.mark.parametrize("empty", [False, True])
def test_reference_assets_come_from_completed_pinned_job(monkeypatch, empty):
    connection, args, row, _ = proposal_fixture(monkeypatch)
    row["input"]["references"] = (
        []
        if empty
        else [
            dict(reference_id="ref-b", asset_id="source", class_id="item", approved=True),
            dict(reference_id="ref-a", asset_id="source", class_id="item", approved=True),
        ]
    )
    row["record"]["evidence_ids"] = [] if empty else ["ref-a", "ref-b"]
    row["sha256"] = row["stage_sha256"] = hashlib.sha256(canonical_json(row["record"])).hexdigest()
    row["payload"] = copy.deepcopy(row["record"])
    assert generated_label_store.proposal_reference_assets(connection, **args) == (
        frozenset() if empty else frozenset({"source"})
    )
    assert connection.execute.call_count == 1
    assert "FOR SHARE OF j" in str(connection.execute.call_args.args[0])


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "collection",
        "item",
        "asset",
        "class",
        "id",
        "approved",
        "duplicate",
        "result",
        "digest",
    ],
)
def test_missing_or_substituted_proposal_reference_inputs_fail_closed(monkeypatch, mutation):
    connection, args, row, _ = proposal_fixture(monkeypatch)
    reference = dict(reference_id="ref", asset_id="source", class_id="item", approved=True)
    row["input"]["references"] = [reference]
    row["record"]["evidence_ids"] = ["ref"]
    if mutation == "missing":
        del row["input"]["references"]
    elif mutation == "collection":
        row["input"]["references"] = {}
    elif mutation == "item":
        row["input"]["references"] = [None]
    elif mutation in {"asset", "class", "id"}:
        reference[{"asset": "asset_id", "class": "class_id", "id": "reference_id"}[mutation]] = " "
    elif mutation == "approved":
        reference["approved"] = 1
    elif mutation == "duplicate":
        row["input"]["references"].append(dict(reference))
    elif mutation == "result":
        row["record"]["evidence_ids"] = []
    row["sha256"] = row["stage_sha256"] = hashlib.sha256(canonical_json(row["record"])).hexdigest()
    row["payload"] = copy.deepcopy(row["record"])
    if mutation == "digest":
        row["sha256"] = "f" * 64
    with pytest.raises(ValueError):
        generated_label_store.proposal_reference_assets(connection, **args)


def reference_fixture(monkeypatch):
    row = dict(
        project_key="project",
        asset_key="label",
        expected_revision=1,
        correction=dict(new_revision=2, proposal_run_keys=["a", "b"]),
    )
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.all.return_value = [row]
    resolver = MagicMock(side_effect=[frozenset({"ref-a"}), frozenset({"ref-b"})])
    current = MagicMock(
        side_effect=lambda c, *, asset_keys, **kw: {a: a + "-group" for a in asset_keys}
    )
    monkeypatch.setattr(store, "proposal_reference_assets", resolver)
    monkeypatch.setattr(store, "current_release_groups", current)
    inputs = [dict(assignment_version="v1", sample_revisions={"label": 2})]
    return connection, row, resolver, current, inputs


@pytest.mark.parametrize("revision", [1, 2, 3])
def test_reference_influence_starts_at_exact_revision_and_survives_corrections(
    monkeypatch, revision
):
    connection, _, resolver, current, inputs = reference_fixture(monkeypatch)
    inputs[0]["sample_revisions"]["label"] = revision
    # Unselected intermediate runs and alternate assignment versions share one closure.
    inputs.append(copy.deepcopy(inputs[0]) | {"assignment_version": "v2"})
    assert store._recorded_reference_groups(connection, "project-id", inputs) == (
        () if revision == 1 else ("ref-a-group", "ref-b-group")
    )
    assert resolver.call_count == (0 if revision == 1 else 2)
    assert current.call_count == (0 if revision == 1 else 2)
    if revision > 1:
        assert [call.kwargs["assignment_version"] for call in current.call_args_list] == [
            "v1",
            "v2",
        ]
        assert all(
            call.kwargs["asset_keys"] == frozenset({"ref-a", "ref-b"})
            for call in current.call_args_list
        )
        assert [call.kwargs["proposal_run_key"] for call in resolver.call_args_list] == ["a", "b"]
    assert connection.execute.call_args.args[0].compile().params == {
        "project_id_1": "project-id",
        "decision_1": "apply_revision",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "unrelated",
        "historical",
        "revision",
        "boolean",
        "keys",
        "empty",
        "duplicate",
        "lookup",
        "current",
    ],
)
def test_reference_boundaries_and_invalid_recorded_links(monkeypatch, mutation):
    connection, row, resolver, current, inputs = reference_fixture(monkeypatch)
    if mutation == "unrelated":
        row["asset_key"] = "unrelated"
    elif mutation == "historical":
        del row["correction"]["proposal_run_keys"]
    elif mutation in {"revision", "boolean"}:
        row["correction"]["new_revision"] = 3 if mutation == "revision" else True
    elif mutation == "keys":
        row["correction"]["proposal_run_keys"] = "a"
    elif mutation == "empty":
        row["correction"]["proposal_run_keys"] = []
    elif mutation == "duplicate":
        row["correction"]["proposal_run_keys"] = ["a", "a"]
    elif mutation == "lookup":
        resolver.side_effect = ValueError("missing completed proposal")
    else:
        current.side_effect = ValueError("canonical relation conflict")
    if mutation in {"unrelated", "historical"}:
        assert store._recorded_reference_groups(connection, "project-id", inputs) == ()
        resolver.assert_not_called()  # Never infer missing historical provenance.
    else:
        with pytest.raises(ValueError):
            store._recorded_reference_groups(connection, "project-id", inputs)


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("port", ["assembly", "consumer"])
@pytest.mark.parametrize("role", ["omitted", "final", "development", "canary", "unrelated"])
def test_recorded_references_cannot_be_hidden_or_replayed_as_independent_evidence(
    monkeypatch, side, port, role
):
    from labelloop_api import qualification_context_store

    connection, args, context, readers = setup(monkeypatch, side)
    evidence = context.evidence
    group = {
        "omitted": "reference-group",
        "unrelated": "reference-group",
        "final": evidence.final_test.dataset.group_ids[0],
        "development": evidence.lineage.development.group_ids[0],
        "canary": evidence.canary_audit.sources[0].group_id,
    }[role]
    lineage = replace(evidence.lineage, reference_group_ids=() if role == "omitted" else (group,))
    context = replace(context, evidence=replace(evidence, lineage=lineage))
    inputs = store.ModelInputGroups(lineage.training_group_ids, (), references=(group,))
    readers["model_input_groups"].return_value = inputs
    if port == "assembly":

        def consume():
            return qualification_context_store.load_qualification_context(
                connection, **(args | {"lineage": lineage})
            )
    else:
        for name in ("lock_project", "load_final_test", "require_development_uses"):
            monkeypatch.setattr(final_test_store, name, readers[name])
        monkeypatch.setattr(
            evaluation_group_store, "load_evaluation_set", readers["load_evaluation_set"]
        )
        monkeypatch.setattr(store, "model_input_groups", MagicMock(return_value=inputs))

        def consume():
            return final_test_store.current_qualification_context(
                connection, context, model_id="model-id"
            )

    if role == "unrelated":
        assert consume() == context
    else:
        message = (
            "omits recorded proposal"
            if role == "omitted"
            else (
                "overlap evaluation holdouts"
                if role in {"final", "development"}
                else "overlap recorded proposal references"
            )
        )
        with pytest.raises(ValueError, match=message):
            consume()
