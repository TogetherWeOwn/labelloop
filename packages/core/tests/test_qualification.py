from dataclasses import fields, replace
from typing import Any

import pytest
from qualification_fixture import qualification_context
from test_registry import model, report

from labelloop_core.qualification import (
    QualificationContext,
    QualificationTargets,
    audit_sample_size,
)
from labelloop_core.qualification_data import PipelineIdentity
from labelloop_core.registry import PromotionPolicy, RegistryState


def promote(context: QualificationContext) -> RegistryState:
    release = model(320)
    return RegistryState(release.project_id, release.recipe_id).promote(
        release,
        report(release),
        PromotionPolicy(1, 0.9),
        expected_revision=0,
        qualification=context,
    )


def context() -> QualificationContext:
    release = model(320)
    return qualification_context(release, report(release), PromotionPolicy(1, 0.9))


def test_missing_evidence_is_not_a_promotion_and_old_state_is_unchanged() -> None:
    release = model(320)
    state = RegistryState(release.project_id, release.recipe_id)
    with pytest.raises(ValueError, match="trusted qualification"):
        state.promote(release, report(release), PromotionPolicy(1, 0.9), expected_revision=0)
    assert state.champion is None and state.revision == 0
    assert promote(context()).champion == release


@pytest.mark.parametrize(
    "name",
    [
        item.name
        for item in fields(QualificationTargets)
        if item.name not in {"fixture_only", "critical_slice_regression_tolerance"}
    ],
)
def test_every_null_or_missing_target_retains_the_incumbent(name: str) -> None:
    current = context()
    changes: dict[str, Any] = {name: () if name == "critical_slices" else None}
    targets = replace(current.evidence.targets, **changes)
    with pytest.raises(ValueError, match="targets retain"):
        promote(replace(current, evidence=replace(current.evidence, targets=targets)))


@pytest.mark.parametrize("name", ["min_support", "min_map50_95", "max_regression"])
def test_null_critical_slice_target_cannot_pass(name: str) -> None:
    current = context()
    targets = current.evidence.targets
    changes: dict[str, Any] = {name: None}
    sliced = replace(targets.critical_slices[0], **changes)
    with pytest.raises(ValueError, match="null critical"):
        promote(
            replace(
                current,
                evidence=replace(
                    current.evidence,
                    targets=replace(targets, critical_slices=(sliced,)),
                ),
            )
        )


@pytest.mark.parametrize("name", [item.name for item in fields(PipelineIdentity)])
def test_every_material_pipeline_change_requires_requalification(name: str) -> None:
    current = context()
    old = getattr(current.current_pipeline, name)
    changed = "9" * 64 if name.endswith("sha256") else old + "-changed"
    with pytest.raises(ValueError, match="stale or mismatched"):
        promote(
            replace(current, current_pipeline=replace(current.current_pipeline, **{name: changed}))
        )


@pytest.mark.parametrize("audit_name", ["annotation_audit", "canary_audit"])
@pytest.mark.parametrize(
    "field", ["labels_correct", "scope_complete", "blind_full_image_inspected"]
)
def test_confidently_wrong_ordinary_acceptance_blocks_real_registry_transition(
    audit_name: str,
    field: str,
) -> None:
    current = context()
    audited = getattr(current.evidence, audit_name)
    observations = (replace(audited.observations[0], **{field: False}), *audited.observations[1:])
    changed = replace(audited, observations=observations)
    with pytest.raises(ValueError, match="audit blocks"):
        promote(replace(current, evidence=replace(current.evidence, **{audit_name: changed})))


@pytest.mark.parametrize("audit_name", ["annotation_audit", "canary_audit"])
def test_missing_truncated_dependent_and_model_agreement_audits_are_not_truth(
    audit_name: str,
) -> None:
    current = context()
    audited = getattr(current.evidence, audit_name)
    mutations = (
        replace(audited, observations=audited.observations[1:]),
        replace(
            audited,
            observations=(
                replace(audited.observations[0], output_truncated=True),
                *audited.observations[1:],
            ),
        ),
        replace(
            audited,
            observations=(
                replace(audited.observations[0], reviewer_id="fixture-teacher"),
                *audited.observations[1:],
            ),
        ),
        replace(audited, truth_basis="model_agreement"),
    )
    for changed in mutations:
        with pytest.raises(ValueError):
            promote(replace(current, evidence=replace(current.evidence, **{audit_name: changed})))


@pytest.mark.parametrize(
    "field,value",
    [
        ("correct_labels", 0),
        ("discovered_objects", 0),
        ("max_geometry_error", 0.1),
        ("accepted_labels", 0),
        ("truth_objects", 0),
    ],
)
def test_annotation_quality_dimensions_are_independent_gates(field: str, value: Any) -> None:
    current = context()
    audit = current.evidence.annotation_audit
    measurement = audit.measurements[0]
    changes = {field: value}
    if field == "accepted_labels":
        changes["correct_labels"] = 0
    if field == "truth_objects":
        changes["discovered_objects"] = 0
    changed = replace(audit, measurements=(replace(measurement, **changes),))
    with pytest.raises(ValueError, match="precision, discovery or geometry"):
        promote(replace(current, evidence=replace(current.evidence, annotation_audit=changed)))


def test_audit_source_group_support_not_repeated_object_votes() -> None:
    current = context()
    audit = current.evidence.annotation_audit
    # Large object counts in a single independent source group cannot satisfy two groups.
    targets = replace(current.evidence.targets, min_audit_groups=2)
    with pytest.raises(ValueError, match="support"):
        audit.require_quality(current.current_pipeline, current.evidence.scope, targets)
    wrong_group = replace(audit.measurements[0], group_ids=("unobserved-group",))
    with pytest.raises(ValueError, match="support"):
        replace(audit, measurements=(wrong_group,)).require_quality(
            current.current_pipeline,
            current.evidence.scope,
            current.evidence.targets,
        )


@pytest.mark.parametrize("group", ["train-group", "dev-group", "final-group", "audit-group-0"])
def test_canary_must_be_fresh_from_training_all_evaluation_and_prior_audit(group: str) -> None:
    current = context()
    canary = current.evidence.canary_audit
    sources = (replace(canary.sources[0], group_id=group), *canary.sources[1:])
    measured = (replace(canary.measurements[0], group_ids=(group,)),)
    changed = replace(canary, sources=sources, measurements=measured)
    with pytest.raises(ValueError, match="canary groups must be fresh"):
        promote(replace(current, evidence=replace(current.evidence, canary_audit=changed)))


def test_wrong_canary_release_or_policy_and_missing_scope_do_not_pass() -> None:
    current = context()
    for evidence in (
        replace(current.evidence, canary_release_sha256="a" * 64),
        replace(current.evidence, release_sha256="a" * 64),
    ):
        with pytest.raises(ValueError, match="mismatched"):
            promote(replace(current, evidence=evidence))
    with pytest.raises(ValueError, match="policies disagree"):
        current.require_qualified(model(320), report(model(320)), policy=PromotionPolicy(1, 0))
    audit = current.evidence.annotation_audit
    cell = replace(audit.measurements[0].scope, condition="unmeasured")
    with pytest.raises(ValueError, match="coverage"):
        promote(
            replace(
                current,
                evidence=replace(
                    current.evidence,
                    annotation_audit=replace(
                        audit,
                        measurements=(replace(audit.measurements[0], scope=cell),),
                    ),
                ),
            )
        )


def test_missing_or_unsupported_critical_slices_and_nonfinite_metrics_cannot_pass() -> None:
    release = model(320)
    good = report(release)
    for altered in (
        replace(good, slices={}),
        replace(
            good,
            slices={
                "fixture_critical": replace(
                    good.overall,
                    per_class={"object": replace(good.overall.per_class["object"], support=0)},
                )
            },
        ),
        replace(
            good,
            overall=replace(
                good.overall,
                per_class={
                    "object": replace(good.overall.per_class["object"], map50_95=float("nan")),
                },
            ),
        ),
    ):
        if (
            altered.overall.per_class["object"].map50_95
            != altered.overall.per_class["object"].map50_95
        ):
            # Non-finite report snapshots are rejected at serialization, before gating.
            with pytest.raises(ValueError):
                qualification_context(release, altered, PromotionPolicy(1, 0.9))
        else:
            current = qualification_context(release, altered, PromotionPolicy(1, 0.9))
            with pytest.raises(ValueError, match="slice|support"):
                current.require_qualified(release, altered)


def test_critical_slice_regression_blocks_even_when_overall_improves() -> None:
    release = model(320)
    current = context()
    good = report(release)
    degraded = replace(
        good,
        slices={
            "fixture_critical": replace(
                good.overall,
                per_class={"object": replace(good.overall.per_class["object"], map50_95=0.95)},
            )
        },
    )
    assessed = qualification_context(release, degraded, PromotionPolicy(1, 0.9))
    with pytest.raises(ValueError, match="regression"):
        assessed.require_qualified(release, degraded, incumbent_report=good)
    # An absent incumbent critical slice is not inferred from its aggregate score.
    with pytest.raises(ValueError, match="missing incumbent"):
        current.require_qualified(release, good, incumbent_report=replace(good, slices={}))


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "0.9"])
def test_malformed_quality_targets_fail_closed(value: Any) -> None:
    with pytest.raises(ValueError):
        replace(context().evidence.targets, min_map50_95=value)


def test_fixture_truth_is_explicit_and_cannot_qualify_real_targets() -> None:
    current = context()
    assert current.evidence.targets.fixture_only is True
    assert QualificationTargets().fixture_only is False
    assert QualificationTargets().min_class_support is None
    audit = current.evidence.annotation_audit
    targets = replace(current.evidence.targets, fixture_only=False)
    with pytest.raises(ValueError, match="harness truth"):
        audit.require_quality(current.current_pipeline, current.evidence.scope, targets)
    with pytest.raises(ValueError, match="harness truth"):
        replace(audit, truth_basis="adjudicated").require_quality(
            current.current_pipeline, current.evidence.scope, current.evidence.targets
        )
    with pytest.raises(ValueError, match="human adjudication"):
        replace(audit, truth_basis="trusted_system_truth").require_quality(
            current.current_pipeline, current.evidence.scope, targets
        )
    malformed: Any = 1
    with pytest.raises(ValueError, match="explicit boolean"):
        replace(current.evidence.targets, fixture_only=malformed)


def test_declared_error_target_requires_independent_group_sample_bound() -> None:
    assert audit_sample_size(0.01, None) == 299
    assert audit_sample_size(0.005, 10) == 598
    assert audit_sample_size(0.01, 400) == 400
    assert audit_sample_size(None, 10) == 10
    for error, minimum in ((None, None), (0.0, 1), (1.0, 1), (True, 1), (0.01, 0)):
        with pytest.raises(ValueError):
            audit_sample_size(error, minimum)
    current = context()
    audit = replace(current.evidence.annotation_audit, truth_basis="adjudicated")
    targets = replace(current.evidence.targets, fixture_only=False, min_accepted_precision=0.99)
    with pytest.raises(ValueError, match="independent source-group support"):
        audit.require_quality(current.current_pipeline, current.evidence.scope, targets)


def test_critical_ap50_regression_fails_even_when_map50_95_passes() -> None:
    release = model(320)
    good = report(release)
    degraded = replace(
        good,
        slices={
            "fixture_critical": replace(
                good.overall,
                per_class={"object": replace(good.overall.per_class["object"], map50=0.99)},
            )
        },
    )
    current = qualification_context(release, degraded, PromotionPolicy(1, 0.9))
    assert current.evidence.targets.critical_slice_regression_tolerance is None
    with pytest.raises(ValueError, match="AP50 regression"):
        current.require_qualified(release, degraded, incumbent_report=good)
    current = qualification_context(release, good, PromotionPolicy(1, 0.9))
    current.require_qualified(release, good, incumbent_report=good)
