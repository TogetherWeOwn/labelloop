"""Synthetic policy/evidence for contract tests, NEVER a production qualification.

Group identities, independent observations and measured annotation counts here are
invented fixture data. A copied CPU report used by persistence tests remains a
synthetic lifecycle record, not proof of independent final-test model quality.
"""

from dataclasses import replace

from labelloop_core.audit import AuditCandidate, AuditObservation, select_audit_sample
from labelloop_core.domain import AnnotationRevision, DatasetObject
from labelloop_core.evaluation import EvaluationReport
from labelloop_core.evaluation_lifecycle import EvaluationSet, FinalTestState, select_candidate
from labelloop_core.geometry import Box
from labelloop_core.qualification import (
    AnnotationEvidence,
    AnnotationMeasurement,
    QualificationContext,
    QualificationEvidence,
    QualificationLineage,
    QualificationTargets,
    SliceTarget,
)
from labelloop_core.qualification_data import (
    DriftIncident,
    PipelineIdentity,
    ScopeCell,
    SourceGroup,
    record_sha256,
)
from labelloop_core.registry import ModelRelease, PromotionPolicy


def synthetic_final(report: EvaluationReport, *, run_id: str, dataset_id: str) -> EvaluationReport:
    return replace(
        report,
        run_id=run_id,
        dataset_release_id=dataset_id,
        role="locked_final_test",
        slices=report.slices | {"fixture_critical": report.overall},
    )


def annotation_evidence(
    pipeline: PipelineIdentity,
    scope: tuple[ScopeCell, ...],
    prefix: str,
) -> AnnotationEvidence:
    candidates: list[AuditCandidate] = []
    sources: list[SourceGroup] = []
    measurements: list[AnnotationMeasurement] = []
    for index, cell in enumerate(scope):
        asset = f"{prefix}-{index}"
        group = f"{prefix}-group-{index}"
        candidates.append(
            AuditCandidate(
                asset,
                AnnotationRevision(
                    1,
                    (DatasetObject("object", cell.class_id, Box(0, 0, 8, 8)),),
                    "system",
                    approval_state="auto_accepted",
                    scope_completeness="exhaustive_for_scope",
                    review_tier="machine_verified_no_human_review",
                ),
                pipeline.pipeline_id,
                frozenset({"fixture-teacher", "fixture-reviewer"}),
            )
        )
        sources.append(SourceGroup(asset, group, cell.source_id, (cell.condition,)))
        measurements.append(AnnotationMeasurement(cell, (group,), 1, 1, 1, 1, 0.0))
    candidates.append(
        AuditCandidate(
            prefix + "-negative",
            AnnotationRevision(
                1,
                (),
                "system",
                approval_state="auto_accepted",
                scope_completeness="exhaustive_for_scope",
                review_tier="machine_verified_no_human_review",
            ),
            pipeline.pipeline_id,
            frozenset({"fixture-teacher", "fixture-reviewer"}),
        )
    )
    sources.append(
        SourceGroup(
            prefix + "-negative",
            prefix + "-negative-group",
            scope[0].source_id,
            (scope[0].condition,),
        )
    )
    sample = select_audit_sample(
        tuple(candidates),
        sample_id=prefix,
        size=len(candidates),
        seed=7,
    )
    return AnnotationEvidence(
        sample,
        tuple(
            AuditObservation(
                item.asset_id,
                1,
                "fixture-independent-domain-auditor",
                True,
                True,
                True,
            )
            for item in sample.candidates
        ),
        tuple(sources),
        "constructive_synthetic",
        tuple(measurements),
    )


def qualification_context(
    release: ModelRelease,
    final_report: EvaluationReport,
    policy: PromotionPolicy,
    *,
    scope: tuple[ScopeCell, ...] | None = None,
    remediation_for: tuple[DriftIncident, ...] = (),
) -> QualificationContext:
    scope = scope or tuple(
        ScopeCell(value, "fixture-source", "ordinary") for value in release.class_ids
    )
    targets = QualificationTargets(
        policy.min_class_support,
        policy.min_map50_95,
        policy.max_regression,
        1,
        1.0,
        1.0,
        0.0,
        1,
        tuple(
            SliceTarget(
                "fixture_critical",
                value,
                policy.min_class_support,
                policy.min_map50_95,
                policy.max_regression,
            )
            for value in release.class_ids
        ),
        fixture_only=True,
    )
    pipeline = PipelineIdentity(
        release.project_id,
        "e" * 64,
        release.recipe_id,
        release.preprocessing_sha256,
        "fixture-reviewer-v1",
        "fixture-provider-v1",
        "f" * 64,
        "1" * 64,
        "2" * 64,
        "3" * 64,
        "4" * 64,
        record_sha256(targets),
    )
    development = replace(
        final_report,
        run_id=next(item for item in release.evaluation_run_ids if item != final_report.run_id),
        dataset_release_id="fixture-development",
        role="development",
    )
    dev_set = EvaluationSet(
        "fixture-development", release.project_id, ("dev-group",), "development"
    )
    selection = select_candidate(
        release,
        development,
        dev_set,
        selection_id="selection-" + release.release_id,
    )
    final_set = EvaluationSet(
        final_report.dataset_release_id,
        release.project_id,
        ("final-group",),
        "locked_final_test",
    )
    final_state = FinalTestState(final_set).expose(
        release,
        selection,
        final_report,
        expected_revision=0,
    )
    evidence = QualificationEvidence(
        "qualification-" + release.release_id,
        record_sha256(release),
        pipeline,
        scope,
        targets,
        annotation_evidence(pipeline, scope, "audit"),
        replace(annotation_evidence(pipeline, scope, "canary"), remediation_for=remediation_for),
        record_sha256(release),
        QualificationLineage(("train-group",), (), dev_set, None),
        final_state,
        selection.selection_id,
        remediation_for,
    )
    return QualificationContext(evidence, pipeline, final_state)
