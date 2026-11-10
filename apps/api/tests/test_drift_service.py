"""Containment uses historical server evidence, not a new qualification grant."""

import json
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from qualification_fixture import qualification_context, synthetic_final
from test_m5_integration_contract import make_release, make_report

from labelloop_api import drift_service as service
from labelloop_api.model_store import json_record
from labelloop_core.qualification import QualificationContext
from labelloop_core.qualification_wire import dumps, loads
from labelloop_core.registry import PromotionPolicy, RegistryState


def setup(monkeypatch, side=320):
    release = make_release(side, "challenger")
    report = synthetic_final(make_report(release), run_id="final", dataset_id="final-set")
    release = replace(release, evaluation_run_ids=(*release.evaluation_run_ids, report.run_id))
    context = qualification_context(release, report, PromotionPolicy(1, 0.9))
    registry = RegistryState(release.project_id, release.recipe_id, 7, release)
    record = {"after": json_record(registry), "qualification": json.loads(dumps(context))}
    connection = MagicMock()
    connection.execute.return_value.scalars.return_value = [record]
    monkeypatch.setattr(service.acceptance_store, "_scope", lambda *args: ("p-id", "r-id"))
    monkeypatch.setattr(service.model_store, "load_registry", lambda *args: registry)
    writer = MagicMock(return_value="paused-state")
    monkeypatch.setattr(service.acceptance_store, "pause_acceptance", writer)
    args = dict(
        project_key=release.project_id,
        recipe_key=release.recipe_id,
        actor_id="session-actor",
        expected_revision=2,
        expected_registry_revision=7,
        reason="camera layout changed",
    )
    return connection, args, context, registry, record, writer


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("axis", [None, "class_id", "source_id", "condition"])
def test_expands_selected_axes_from_actual_promotion_and_generates_incident(
    monkeypatch, side, axis
):
    connection, args, context, _, _, writer = setup(monkeypatch, side)
    cell = context.evidence.scope[0]
    if axis:
        args[axis] = getattr(cell, axis)
    assert service.pause_current_champion(connection, **args) == "paused-state"
    written = writer.call_args.kwargs
    assert written["actor_id"] == "session-actor"
    assert written["qualification_id"] == context.evidence.qualification_id
    assert written["scope"] == tuple(
        item
        for item in context.evidence.scope
        if axis is None or getattr(item, axis) == getattr(cell, axis)
    )
    assert written["pause_id"] and written["expected_revision"] == 2
    params = connection.execute.call_args.args[0].compile().params
    assert set(params.values()) == {"p-id", "r-id", "promote"}


def test_later_evaluation_links_and_rollback_do_not_erase_original_lineage(monkeypatch):
    connection, args, context, registry, _, writer = setup(monkeypatch)
    extended = replace(
        registry.champion, evaluation_run_ids=(*registry.champion.evaluation_run_ids, "later")
    )
    monkeypatch.setattr(
        service.model_store, "load_registry", lambda *args: replace(registry, champion=extended)
    )
    assert service.pause_current_champion(connection, **args) == "paused-state"
    assert writer.call_args.kwargs["qualification_id"] == context.evidence.qualification_id


@pytest.mark.parametrize("side", [320, 640])
def test_resume_uses_only_current_champion_recorded_context_and_evaluation(monkeypatch, side):
    connection, args, context, _, record, pause_writer = setup(monkeypatch, side)
    record["evaluation_key"] = "committed-final-report"
    writer = MagicMock(return_value="resumed-state")
    monkeypatch.setattr(service.acceptance_store, "resume_acceptance", writer)
    args.pop("reason")
    args["pause_ids"] = ("camera-layout",)
    assert service.resume_current_champion(connection, **args) == "resumed-state"
    assert writer.call_args.kwargs == {
        key: value for key, value in args.items() if key != "expected_registry_revision"
    } | {"evaluation_key": "committed-final-report", "qualification": context}
    pause_writer.assert_not_called()


@pytest.mark.parametrize(
    "mutation", ["stale", "missing", "contract", "pipeline", "digest", "no-report"]
)
def test_resume_cannot_substitute_historical_authority(monkeypatch, mutation):
    connection, args, context, _, record, _ = setup(monkeypatch)
    record["evaluation_key"] = "committed-final-report"
    writer = MagicMock()
    monkeypatch.setattr(service.acceptance_store, "resume_acceptance", writer)
    args.pop("reason")
    args["pause_ids"] = ("camera-layout",)
    if mutation == "stale":
        args["expected_registry_revision"] -= 1
    elif mutation == "missing":
        connection.execute.return_value.scalars.return_value = []
    elif mutation == "contract":
        record["after"]["champion"]["weights_sha256"] = "a" * 64
    elif mutation == "no-report":
        record["evaluation_key"] = None
    else:
        evidence = context.evidence
        evidence = (
            replace(evidence, pipeline=replace(evidence.pipeline, recipe_id="foreign"))
            if mutation == "pipeline"
            else replace(evidence, release_sha256="b" * 64)
        )
        record["qualification"] = json.loads(dumps(replace(context, evidence=evidence)))
    with pytest.raises(ValueError):
        service.resume_current_champion(connection, **args)
    writer.assert_not_called()


def test_resume_propagates_current_policy_final_audit_and_incident_rejection(monkeypatch):
    connection, args, _, _, record, _ = setup(monkeypatch)
    record["evaluation_key"] = "committed-final-report"
    args.pop("reason")
    args["pause_ids"] = ("camera-layout",)
    writer = MagicMock(side_effect=ValueError("post-incident qualification required"))
    monkeypatch.setattr(service.acceptance_store, "resume_acceptance", writer)
    with pytest.raises(ValueError, match="post-incident"):
        service.resume_current_champion(connection, **args)
    writer.assert_called_once()


@pytest.mark.parametrize("axis", ["class_id", "source_id", "condition"])
def test_unknown_scope_cannot_invent_new_cells(monkeypatch, axis):
    connection, args, _, _, _, writer = setup(monkeypatch)
    with pytest.raises(ValueError, match="outside recorded"):
        service.pause_current_champion(connection, **(args | {axis: "invented"}))
    writer.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    [
        "stale",
        "absent",
        "missing",
        "no-qualification",
        "contract",
        "pipeline",
        "digest",
        "canary",
        "root",
    ],
)
def test_missing_or_substituted_provenance_never_appends_a_pause(monkeypatch, mutation):
    connection, args, context, registry, record, writer = setup(monkeypatch)
    if mutation == "stale":
        args["expected_registry_revision"] = 6
    elif mutation == "absent":
        monkeypatch.setattr(
            service.model_store, "load_registry", lambda *args: replace(registry, champion=None)
        )
    elif mutation == "missing":
        connection.execute.return_value.scalars.return_value = []
    elif mutation == "no-qualification":
        record["qualification"] = None
    elif mutation == "contract":
        record["after"]["champion"]["weights_sha256"] = "a" * 64
    else:
        evidence = context.evidence
        if mutation == "pipeline":
            evidence = replace(evidence, pipeline=replace(evidence.pipeline, project_id="foreign"))
        elif mutation == "digest":
            evidence = replace(evidence, release_sha256="b" * 64)
        elif mutation == "canary":
            evidence = replace(evidence, canary_release_sha256="b" * 64)
        changed = replace(context, evidence=evidence)
        record["qualification"] = json.loads(
            dumps(changed if mutation != "root" else evidence.final_test)
        )
    with pytest.raises(ValueError):
        service.pause_current_champion(connection, **args)
    writer.assert_not_called()


@pytest.mark.parametrize("side", [320, 640])
def test_foreign_final_history_is_decodable_but_cannot_authorize_containment(monkeypatch, side):
    connection, args, context, _, record, writer = setup(monkeypatch, side)
    final = context.evidence.final_test
    foreign = replace(
        final,
        dataset=replace(final.dataset, project_id="foreign"),
        assessments=tuple(
            replace(item, selection=replace(item.selection, project_id="foreign"))
            for item in final.assessments
        ),
    )
    changed = replace(context, evidence=replace(context.evidence, final_test=foreign))
    encoded = dumps(changed)
    assert loads(encoded, QualificationContext) == changed
    record["qualification"] = json.loads(encoded)
    with pytest.raises(ValueError, match="promotion qualification identity mismatch"):
        service.pause_current_champion(connection, **args)
    writer.assert_not_called()


def test_pause_does_not_require_invalidated_champion_to_qualify_again(monkeypatch):
    connection, args, context, _, record, writer = setup(monkeypatch)
    # Historical evidence is inspected for identity/scope only. Null current targets,
    # invalid final state or vetoes must not prevent containment of known drift.
    invalidated = context.current_final_test.quarantine(
        "new relation", expected_revision=context.current_final_test.revision
    )
    record["qualification"] = json.loads(dumps(replace(context, current_final_test=invalidated)))
    monkeypatch.setattr(
        service.acceptance_store,
        "_qualified_champion",
        MagicMock(side_effect=AssertionError("not an acceptance operation")),
    )
    assert service.pause_current_champion(connection, **args) == "paused-state"
    writer.assert_called_once()
