"""Strict automatic populations use original decisions, not today's policy."""

from dataclasses import replace
from unittest.mock import MagicMock, call

import pytest
from test_audit_sample_store import canonical_row
from test_automatic_review_store import fixture

from labelloop_api import audit_sample_store as store
from labelloop_api.automatic_review_store import StoredAutomaticAcceptance
from labelloop_core.qualification import QualificationPolicy
from labelloop_core.qualification_data import record_sha256


def population(monkeypatch, side):
    arguments, context, _ = fixture(side)
    rows = [
        canonical_row(asset_uuid="first-uuid", asset_key="first", group_id="shared"),
        canonical_row(asset_uuid="second-uuid", asset_key="second", group_id="shared"),
    ]
    release, ontology, members = MagicMock(), MagicMock(), MagicMock()
    release.mappings.return_value.one.return_value = dict(
        id="release-uuid",
        sample_revisions={"first": 2, "second": 2},
        excluded_asset_ids=[],
        ontology_id="ontology-uuid",
        assignment_version="pinned",
    )
    ontology.scalar_one.return_value = {"class-a": 0}
    members.mappings.return_value.all.return_value = rows
    connection = MagicMock()
    connection.execute.side_effect = [release, ontology, members, [], MagicMock()]
    monkeypatch.setattr(store, "lock_project", MagicMock(return_value="project-uuid"))
    monkeypatch.setattr(
        store,
        "current_release_groups",
        MagicMock(return_value={"first": "shared", "second": "shared"}),
    )
    original = StoredAutomaticAcceptance(
        context.evidence.qualification_id,
        context.evidence.release_sha256,
        "final",
        QualificationPolicy(
            context.current_pipeline, context.evidence.scope, context.evidence.targets
        ),
        context.evidence.scope,
    )
    reader = MagicMock(return_value=original)
    monkeypatch.setattr(store, "load_automatic_acceptance", reader)
    return (
        connection,
        reader,
        original,
        dict(
            project_key=arguments["project_key"],
            release_key="ordinary",
            sample_key="strict-sample",
            actor_id="auditor",
            pipeline=context.current_pipeline,
            size=1,
            seed=17,
        ),
    )


@pytest.mark.parametrize("side", [320, 640])
def test_strict_population_checks_every_original_before_selection(monkeypatch, side):
    connection, reader, original, arguments = population(monkeypatch, side)
    frozen = store.freeze_automatic_audit_sample(connection, **arguments)
    assert reader.call_args_list == [
        call(connection, project_key=arguments["project_key"], asset_key=key, revision=2)
        for key in ("first", "second")
    ]
    assert len(frozen.sample.candidates) == 1
    assert frozen.sample.candidates[0].pipeline_id == original.policy.pipeline.pipeline_id
    assert frozen.independent_groups == 1
    saved = connection.execute.call_args.args[0].compile().params
    assert saved["population_sha256"] == record_sha256(
        {key: saved["record"][key] for key in ("population", "groups", "original_pipeline_sha256")}
    )
    assert len(saved["record"]["population"]["payload"]["candidates"]) == 2


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("failure", ["missing", "human", "historical", "pipeline", "later-member"])
def test_strict_population_never_relabels_or_omits_unproven_acceptances(monkeypatch, side, failure):
    connection, reader, original, arguments = population(monkeypatch, side)
    if failure == "pipeline":
        reader.return_value = replace(
            original,
            policy=replace(
                original.policy,
                pipeline=replace(original.policy.pipeline, reviewer_model_version="different"),
            ),
        )
    else:
        error = ValueError("missing exact original automatic decision: " + failure)
        reader.side_effect = [original, error] if failure == "later-member" else error
    with pytest.raises(ValueError):
        store.freeze_automatic_audit_sample(connection, **arguments)
    # No population/sample insert, even if the invalid member would not be selected.
    assert len(connection.execute.call_args_list) == 4


@pytest.mark.parametrize("change", ["foreign-project", "untyped-pipeline"])
def test_strict_population_rejects_foreign_or_untyped_policy_before_io(monkeypatch, change):
    connection, _, _, arguments = population(monkeypatch, 320)
    if change == "foreign-project":
        arguments["project_key"] = "foreign"
    else:
        arguments["pipeline"] = {"pipeline_id": arguments["pipeline"].pipeline_id}
    with pytest.raises(ValueError):
        store.freeze_automatic_audit_sample(connection, **arguments)
    connection.begin_nested.assert_not_called()
    connection.execute.assert_not_called()
