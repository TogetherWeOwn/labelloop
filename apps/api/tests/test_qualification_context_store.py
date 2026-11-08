"""Current assembly with real pure gates; mocked reads are not HTTP/DB acceptance."""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from qualification_fixture import qualification_context, synthetic_final
from test_m5_integration_contract import make_release, make_report

from labelloop_api import qualification_context_store as store
from labelloop_api.annotation_evidence_store import StoredAnnotationEvidence
from labelloop_api.qualification_policy_store import StoredQualificationPolicy
from labelloop_core.qualification import QualificationPolicy, QualificationTargets
from labelloop_core.qualification_data import record_sha256
from labelloop_core.registry import PromotionPolicy


def setup(monkeypatch, side=320):
    release = make_release(side, "challenger")
    report = synthetic_final(make_report(release), run_id="final", dataset_id="final-set")
    release = replace(release, evaluation_run_ids=(*release.evaluation_run_ids, report.run_id))
    context = qualification_context(release, report, PromotionPolicy(1, 0.9))
    evidence = context.evidence
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = "model-id"
    readers = {
        "lock_project": MagicMock(return_value="project-id"),
        "load_release": MagicMock(return_value=release),
        "model_training_groups": MagicMock(return_value=evidence.lineage.training_group_ids),
        "stored_report": MagicMock(return_value=report),
        "load_qualification_policy": MagicMock(
            return_value=StoredQualificationPolicy(
                1, QualificationPolicy(evidence.pipeline, evidence.scope, evidence.targets)
            )
        ),
        "load_final_test": MagicMock(return_value=evidence.final_test),
        "require_development_uses": MagicMock(),
        "load_annotation_evidence": MagicMock(
            side_effect=[
                StoredAnnotationEvidence(evidence.annotation_audit, None),
                StoredAnnotationEvidence(evidence.canary_audit, record_sha256(release)),
            ]
        ),
    }
    for name, reader in readers.items():
        monkeypatch.setattr(store, name, reader)
    args = dict(
        project_key=release.project_id,
        recipe_key=release.recipe_id,
        model_key=release.release_id,
        evaluation_key=report.run_id,
        qualification_key=evidence.qualification_id,
        annotation=store.AnnotationOutcomeRef("audit-release", "audit", "measured-audit"),
        canary=store.AnnotationOutcomeRef("canary-release", "canary", "measured-canary"),
        lineage=evidence.lineage,
    )
    return connection, args, context, readers


@pytest.mark.parametrize("side", [320, 640])
def test_assembly_uses_only_scoped_current_stored_inputs_and_real_quality_gates(monkeypatch, side):
    connection, args, expected, readers = setup(monkeypatch, side)
    assert store.load_qualification_context(connection, **args) == expected
    readers["lock_project"].assert_called_once_with(connection, args["project_key"])
    assert connection.execute.call_args.args[0].compile().params == dict(
        project_id_1="project-id", release_key_1=args["model_key"]
    )
    readers["model_training_groups"].assert_called_once_with(
        connection, project_id="project-id", model_id="model-id"
    )
    readers["stored_report"].assert_called_once_with(
        connection, readers["load_release"].return_value, args["evaluation_key"]
    )
    readers["load_final_test"].assert_called_once_with(
        connection, project_key=args["project_key"], dataset_key="final-set"
    )
    selection = expected.evidence.final_test.assessments[0].selection
    readers["require_development_uses"].assert_called_once_with(
        connection,
        project_key=args["project_key"],
        development_set_key=selection.development_set_id,
        calibration_set_key=selection.calibration_set_id,
    )
    calls = readers["load_annotation_evidence"].call_args_list
    for call, reference, model in zip(
        calls, (args["annotation"], args["canary"]), (None, args["model_key"]), strict=True
    ):
        assert call.args == (connection,)
        assert call.kwargs == dict(
            project_key=args["project_key"],
            release_key=reference.release_key,
            audit_key=reference.audit_key,
            evidence_key=reference.evidence_key,
            canary_model_key=model,
            require_current_groups=True,
        )
    assert connection.execute.call_count == 1  # SELECT only; assembly grants no state change.


@pytest.mark.parametrize(
    "field", ["project_key", "recipe_key", "model_key", "evaluation_key", "qualification_key"]
)
def test_bad_selector_is_rejected_before_lock_or_read(monkeypatch, field):
    connection, args, _, readers = setup(monkeypatch)
    with pytest.raises(ValueError):
        store.load_qualification_context(connection, **(args | {field: " "}))
    readers["lock_project"].assert_not_called()
    connection.execute.assert_not_called()


@pytest.mark.parametrize("field", ["release_key", "audit_key", "evidence_key"])
def test_outcome_refs_reject_empty_identity(field):
    with pytest.raises(ValueError):
        store.AnnotationOutcomeRef(
            **(dict(release_key="release", audit_key="audit", evidence_key="outcome") | {field: ""})
        )


@pytest.mark.parametrize(
    "reader",
    [
        "load_qualification_policy",
        "load_release",
        "model_training_groups",
        "stored_report",
        "load_final_test",
        "require_development_uses",
        "load_annotation_evidence",
    ],
)
def test_missing_or_conflicting_record_never_yields_a_context(monkeypatch, reader):
    connection, args, _, readers = setup(monkeypatch)
    readers[reader].side_effect = ValueError("missing or current group conflict")
    with pytest.raises(ValueError, match="missing or current group conflict"):
        store.load_qualification_context(connection, **args)
    assert not any(
        getattr(call.args[0], "is_insert", False) for call in connection.execute.call_args_list
    )


@pytest.mark.parametrize("side", [320, 640])
def test_asserted_lineage_cannot_omit_compiled_training_groups(monkeypatch, side):
    connection, args, _, readers = setup(monkeypatch, side)
    readers["model_training_groups"].return_value = ("unreported-training-group",)
    with pytest.raises(ValueError, match="omits compiled training groups"):
        store.load_qualification_context(connection, **args)
    readers["stored_report"].assert_not_called()
    readers["load_annotation_evidence"].assert_not_called()


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("role", ["development", "final", "canary"])
def test_inherited_training_groups_cannot_be_hidden_in_other_roles(monkeypatch, side, role):
    connection, args, expected, readers = setup(monkeypatch, side)
    evidence = expected.evidence
    group = {
        "development": evidence.lineage.development.group_ids[0],
        "final": evidence.final_test.dataset.group_ids[0],
        "canary": evidence.canary_audit.sources[0].group_id,
    }[role]
    groups = (*evidence.lineage.training_group_ids, group)
    readers["model_training_groups"].return_value = groups
    # Reporting the inherited exposure satisfies completeness but cannot make the
    # same group independent selection/final/canary evidence.
    args["lineage"] = replace(evidence.lineage, training_group_ids=groups)
    with pytest.raises(ValueError, match="evaluation groups overlap|canary groups must be fresh"):
        store.load_qualification_context(connection, **args)
    assert not any(
        getattr(call.args[0], "is_insert", False) for call in connection.execute.call_args_list
    )


@pytest.mark.parametrize("field", ["project_id", "recipe_id", "release_id", "missing"])
def test_scoped_model_substitution_fails_before_outcome_reads(monkeypatch, field):
    connection, args, _, readers = setup(monkeypatch)
    release = readers["load_release"].return_value
    readers["load_release"].return_value = (
        None if field == "missing" else replace(release, **{field: "foreign"})
    )
    with pytest.raises(ValueError, match="exact project, recipe and model"):
        store.load_qualification_context(connection, **args)
    readers["load_annotation_evidence"].assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    ["null-targets", "pipeline", "scope", "adaptation", "quarantine", "exposure", "metadata"],
)
def test_current_configuration_lifecycle_and_complete_model_metadata_are_gates(
    monkeypatch, mutation
):
    connection, args, _, readers = setup(monkeypatch)
    stored = readers["load_qualification_policy"].return_value
    policy = stored.policy
    final = readers["load_final_test"].return_value
    if mutation == "null-targets":
        targets = QualificationTargets()
        policy = replace(
            policy,
            targets=targets,
            pipeline=replace(policy.pipeline, acceptance_policy_sha256=record_sha256(targets)),
        )
    elif mutation == "pipeline":
        policy = replace(policy, pipeline=replace(policy.pipeline, prompt_sha256="a" * 64))
    elif mutation == "scope":
        policy = replace(policy, scope=policy.scope[:1])
    elif mutation == "adaptation":
        final = final.record_adaptation("guided change", expected_revision=final.revision)
    elif mutation == "quarantine":
        final = final.quarantine("new group overlap", expected_revision=final.revision)
    elif mutation == "exposure":
        final = replace(final, assessments=())
    else:
        release = readers["load_release"].return_value
        readers["load_release"].return_value = replace(release, export_sha256="a" * 64)
    readers["load_qualification_policy"].return_value = replace(stored, policy=policy)
    readers["load_final_test"].return_value = final
    with pytest.raises(ValueError):
        store.load_qualification_context(connection, **args)


@pytest.mark.parametrize("target", ["audit", "canary"])
@pytest.mark.parametrize(
    "mutation",
    [
        "partial",
        "adverse",
        "truncated",
        "agreement",
        "precision",
        "discovery",
        "geometry",
        "model-digest",
    ],
)
def test_saved_outcomes_are_not_automatically_qualified(monkeypatch, target, mutation):
    connection, args, expected, readers = setup(monkeypatch)
    audit = expected.evidence.annotation_audit
    canary = expected.evidence.canary_audit
    outcome = audit if target == "audit" else canary
    if mutation == "partial":
        outcome = replace(outcome, observations=outcome.observations[:1])
    elif mutation in {"adverse", "truncated"}:
        field = "labels_correct" if mutation == "adverse" else "output_truncated"
        observation = replace(outcome.observations[0], **{field: mutation == "truncated"})
        outcome = replace(outcome, observations=(observation, *outcome.observations[1:]))
    elif mutation == "agreement":
        outcome = replace(outcome, truth_basis="model_agreement")
    elif mutation != "model-digest":
        field, value = {
            "precision": ("correct_labels", 0),
            "discovery": ("discovered_objects", 0),
            "geometry": ("max_geometry_error", 0.5),
        }[mutation]
        measured = replace(outcome.measurements[0], **{field: value})
        outcome = replace(outcome, measurements=(measured, *outcome.measurements[1:]))
    if target == "audit":
        audit = outcome
    else:
        canary = outcome
    # Ordinary audit/model separation is enforced by load_annotation_evidence;
    # the assembler additionally checks the complete candidate digest itself.
    readers["load_annotation_evidence"].side_effect = [
        StoredAnnotationEvidence(audit, None),
        StoredAnnotationEvidence(
            canary, "a" * 64 if mutation == "model-digest" else expected.evidence.release_sha256
        ),
    ]
    with pytest.raises(ValueError):
        store.load_qualification_context(connection, **args)


@pytest.mark.parametrize(
    "group", ["train-group", "dev-group", "final-group", "audit-group-0", "reference-group"]
)
def test_persisted_canary_still_needs_fresh_groups(monkeypatch, group):
    connection, args, expected, readers = setup(monkeypatch)
    canary = expected.evidence.canary_audit
    source = replace(canary.sources[0], group_id=group)
    measured = replace(canary.measurements[0], group_ids=(group,))
    canary = replace(
        canary,
        sources=(source, *canary.sources[1:]),
        measurements=(measured, *canary.measurements[1:]),
    )
    args["lineage"] = replace(args["lineage"], reference_group_ids=("reference-group",))
    readers["load_annotation_evidence"].side_effect = [
        StoredAnnotationEvidence(expected.evidence.annotation_audit, None),
        StoredAnnotationEvidence(canary, expected.evidence.release_sha256),
    ]
    with pytest.raises(ValueError, match="canary groups must be fresh"):
        store.load_qualification_context(connection, **args)
