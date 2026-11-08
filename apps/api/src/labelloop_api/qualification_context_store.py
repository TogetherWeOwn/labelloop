"""Assemble current persisted qualification inputs, never HTTP/model claims.

This read port does not establish original pipeline execution, measurement truth or
canonical lineage. The authorized service must derive lineage and outcome selectors
from trusted producers. No production route may treat caller-supplied lineage as
that producer. The outer transaction and locks must survive through the decision.
"""

from dataclasses import dataclass

import sqlalchemy as sa

from labelloop_api import model_schema
from labelloop_api.annotation_evidence_store import load_annotation_evidence
from labelloop_api.final_test_store import load_final_test
from labelloop_api.model_store import load_release, stored_report
from labelloop_api.qualification_policy_store import load_qualification_policy
from labelloop_api.qualification_store import lock_project
from labelloop_core.qualification import (
    QualificationContext,
    QualificationEvidence,
    QualificationLineage,
)
from labelloop_core.qualification_data import canonical_record, record_sha256, require_name


@dataclass(frozen=True, slots=True)
class AnnotationOutcomeRef:
    release_key: str
    audit_key: str
    evidence_key: str

    def __post_init__(self) -> None:
        for value in (self.release_key, self.audit_key, self.evidence_key):
            require_name(value)


def load_qualification_context(
    connection: sa.Connection,
    *,
    project_key: str,
    recipe_key: str,
    model_key: str,
    evaluation_key: str,
    qualification_key: str,
    annotation: AnnotationOutcomeRef,
    canary: AnnotationOutcomeRef,
    lineage: QualificationLineage,
) -> QualificationContext:
    """Reload committed outcomes and fail closed on quality, lifecycle or freshness.

    The final-test identity and selection come from the stored report and current
    journal, not separate caller assertions. There is no historical-group option.
    This is a preflight, not registry authorization: promotion still rechecks the
    current configuration/final journal, durable veto and incumbent non-regression.
    Do not cache the result across transactions or use it as an acceptance grant.
    """
    for value in (project_key, recipe_key, model_key, evaluation_key, qualification_key):
        require_name(value)
    project_id = lock_project(connection, project_key)
    policy = load_qualification_policy(
        connection, project_key=project_key, recipe_key=recipe_key
    ).policy
    policy.targets.require_configured()
    model_id = connection.execute(
        sa.select(model_schema.model_release.c.id).where(
            model_schema.model_release.c.project_id == project_id,
            model_schema.model_release.c.release_key == model_key,
        )
    ).scalar_one()
    release = load_release(connection, model_id)
    if release is None or (release.project_id, release.recipe_id, release.release_id) != (
        project_key,
        recipe_key,
        model_key,
    ):
        raise ValueError("qualification requires the exact project, recipe and model release")
    report = stored_report(connection, release, evaluation_key)
    final_test = load_final_test(
        connection, project_key=project_key, dataset_key=report.dataset_release_id
    )
    digest = record_sha256(release)
    selections = tuple(
        assessment.selection
        for assessment in final_test.assessments
        if assessment.selection.project_id == project_key
        and assessment.selection.release_id == model_key
        and assessment.selection.release_sha256 == digest
        and assessment.report_json == canonical_record(report)
    )
    if len(selections) != 1:
        raise ValueError("qualification requires one exact committed final-test exposure")

    def outcome(reference, model=None):
        return load_annotation_evidence(
            connection,
            project_key=project_key,
            release_key=reference.release_key,
            audit_key=reference.audit_key,
            evidence_key=reference.evidence_key,
            canary_model_key=model,
            require_current_groups=True,
        )

    audit = outcome(annotation)
    fresh = outcome(canary, model_key)
    if fresh.canary_release_sha256 != digest:
        raise ValueError("qualification canary differs from the complete current release")
    evidence = QualificationEvidence(
        qualification_key,
        digest,
        policy.pipeline,
        policy.scope,
        policy.targets,
        audit.evidence,
        fresh.evidence,
        fresh.canary_release_sha256,
        lineage,
        final_test,
        selections[0].selection_id,
        fresh.evidence.remediation_for,
    )
    context = QualificationContext(evidence, policy.pipeline, final_test)
    context.require_qualified(release, report)
    return context
