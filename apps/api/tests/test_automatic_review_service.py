"""Current-champion orchestration is fixture-only, never original execution proof."""

import json
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from test_drift_service import setup as promotion_setup

from labelloop_api import automatic_review_service as service
from labelloop_core.qualification_data import record_sha256
from labelloop_core.qualification_wire import dumps


def setup(monkeypatch, side=320):
    connection, pause, context, registry, record, _ = promotion_setup(monkeypatch, side)
    record["evaluation_key"] = "committed-final"
    lock = MagicMock(return_value="p-id")
    monkeypatch.setattr(service, "lock_project", lock)
    writer = MagicMock(return_value=4)
    monkeypatch.setattr(service.revision_store, "append_review", writer)
    args = dict(
        project_key=registry.project_id,
        recipe_key=registry.recipe_id,
        asset_key="canonical-source",
        actor_id=pause["actor_id"],
        expected_revision=3,
        expected_registry_revision=registry.revision,
        scope=context.evidence.scope,
        objects=[],
        scope_completeness="exhaustive_for_scope",
        training_eligible=True,
        proposal_run_keys=("first-proposal", "second-proposal"),
    )
    return connection, args, context, record, writer, lock


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("positive", [True, False])
def test_derives_fixed_machine_fields_and_actual_champion_evidence(monkeypatch, side, positive):
    connection, args, context, _, writer, lock = setup(monkeypatch, side)
    if positive:
        args["objects"] = [{"object_id": "o", "class_id": "c", "box": [1, 2, 3, 4]}]
    assert service.append_current_review(connection, **args) == 4
    lock.assert_called_once_with(connection, args["project_key"])
    calls = connection.execute.call_args_list
    params = calls[0].args[0].compile().params
    assert set(params.values()) == {"p-id", "canonical-source"}
    written = writer.call_args.kwargs
    assert written == {
        "asset_id": args["asset_key"],
        "actor_kind": "system",
        "actor_id": args["actor_id"],
        "expected_revision": 3,
        "approval_state": "auto_accepted",
        "review_tier": "machine_verified_no_human_review",
        "scope_completeness": args["scope_completeness"],
        "objects": args["objects"],
        "training_eligible": True,
        "automatic": service.revision_store.AutomaticReview(
            args["recipe_key"], "committed-final", args["scope"], context
        ),
        "proposal_run_keys": args["proposal_run_keys"],
    }
    assert "gold_eligible" not in written
    connection.begin_nested.assert_called_once()


@pytest.mark.parametrize("mutation", ["stale", "missing", "report", "scope", "nonfixture"])
def test_rejects_missing_stale_or_substituted_authority_before_writer(monkeypatch, mutation):
    connection, args, context, record, writer, _ = setup(monkeypatch)
    if mutation == "stale":
        args["expected_registry_revision"] -= 1
    elif mutation == "missing":
        connection.execute.return_value.scalars.return_value = []
    elif mutation == "report":
        record["evaluation_key"] = None
    elif mutation == "scope":
        args["scope"] = (replace(context.evidence.scope[0], condition="invented"),)
    else:
        targets = replace(context.evidence.targets, fixture_only=False)
        changed = replace(
            context.evidence,
            targets=targets,
            pipeline=replace(
                context.evidence.pipeline, acceptance_policy_sha256=record_sha256(targets)
            ),
        )
        record["qualification"] = json.loads(dumps(replace(context, evidence=changed)))
    with pytest.raises(ValueError, match="fixture-only" if mutation == "nonfixture" else None):
        service.append_current_review(connection, **args)
    writer.assert_not_called()


def test_foreign_asset_is_not_resolved_outside_the_selected_project(monkeypatch):
    connection, args, _, _, writer, _ = setup(monkeypatch)
    from sqlalchemy.exc import NoResultFound

    connection.execute.return_value.scalar_one.side_effect = NoResultFound()
    with pytest.raises(NoResultFound):
        service.append_current_review(connection, **args)
    writer.assert_not_called()
    assert connection.execute.call_count == 1


@pytest.mark.parametrize(
    "reason", ["paused", "current policy", "human correction", "origin append"]
)
def test_writer_rejections_abort_the_enclosing_savepoint(monkeypatch, reason):
    connection, args, _, _, writer, _ = setup(monkeypatch)
    writer.side_effect = ValueError(reason)
    with pytest.raises(ValueError, match=reason):
        service.append_current_review(connection, **args)
    nested = connection.begin_nested.return_value
    assert nested.__exit__.call_args.args[0] is ValueError
    writer.assert_called_once()


@pytest.mark.parametrize("field", ["project_key", "recipe_key", "asset_key", "actor_id"])
def test_invalid_names_do_not_start_database_work(monkeypatch, field):
    connection, args, _, _, writer, lock = setup(monkeypatch)
    args[field] = ""
    with pytest.raises(ValueError):
        service.append_current_review(connection, **args)
    connection.begin_nested.assert_not_called()
    lock.assert_not_called()
    writer.assert_not_called()


@pytest.mark.parametrize("field", ["expected_revision", "expected_registry_revision"])
def test_boolean_revisions_are_not_accepted_as_integers(monkeypatch, field):
    connection, args, _, _, writer, lock = setup(monkeypatch)
    args[field] = True
    with pytest.raises(ValueError):
        service.append_current_review(connection, **args)
    lock.assert_not_called()
    writer.assert_not_called()
