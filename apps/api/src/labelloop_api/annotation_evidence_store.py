"""Immutable measured audit/canary outcomes, not an authorization or truth boundary.

Trusted callers derive measurements, truth provenance and source/condition metadata
from independent inspection, never HTTP/model claims. This port binds them to the
committed audit and canonical groups; it does not establish that provenance itself.
"""

import json
from dataclasses import dataclass

import sqlalchemy as sa

from labelloop_api import model_schema, qualification_schema
from labelloop_api.annotation_audit_store import load_annotation_audit
from labelloop_api.model_store import load_release
from labelloop_api.qualification_store import lock_project
from labelloop_api.schema import canonical_dataset_release
from labelloop_core.qualification import AnnotationEvidence
from labelloop_core.qualification_data import record_sha256, require_name
from labelloop_core.qualification_wire import dumps, loads


@dataclass(frozen=True, slots=True)
class StoredAnnotationEvidence:
    evidence: AnnotationEvidence
    canary_release_sha256: str | None


def _audit(connection, project_id, release_key, audit_key):
    return (
        connection.execute(
            sa.select(qualification_schema.annotation_audit)
            .join(canonical_dataset_release)
            .where(
                qualification_schema.annotation_audit.c.project_id == project_id,
                canonical_dataset_release.c.project_id == project_id,
                canonical_dataset_release.c.release_key == release_key,
                qualification_schema.annotation_audit.c.audit_key == audit_key,
            )
        )
        .mappings()
        .one()
    )


def _bound_evidence(connection, project_key, release_key, audit_key, evidence, current):
    audit = load_annotation_audit(
        connection,
        project_key=project_key,
        release_key=release_key,
        audit_key=audit_key,
        require_current_groups=current,
    )
    groups = {source.asset_id: source.group_id for source in evidence.sources}
    if (
        evidence.sample != audit.frozen.sample
        or evidence.observations != audit.observations
        or groups != audit.frozen.group_ids
    ):
        raise ValueError("measured evidence differs from its committed audit or canonical groups")


def _canary(connection, project_id, model_key):
    model_id = connection.execute(
        sa.select(model_schema.model_release.c.id).where(
            model_schema.model_release.c.project_id == project_id,
            model_schema.model_release.c.release_key == model_key,
        )
    ).scalar_one()
    release = load_release(connection, model_id)
    if release is None:
        raise ValueError("missing canary model release")
    return model_id, record_sha256(release)


def record_annotation_evidence(
    connection: sa.Connection,
    *,
    project_key: str,
    release_key: str,
    audit_key: str,
    evidence_key: str,
    actor_id: str,
    evidence: AnnotationEvidence,
    canary_model_key: str | None = None,
) -> StoredAnnotationEvidence:
    """Append complete or failed outcomes without selecting only passing evidence.

    Canary evidence binds the complete current model release, not just weights.
    Freshness against training/evaluation/prior-audit groups remains a qualification
    gate. Failed/partial outcomes persist and cannot clear an earlier adverse audit.
    The caller commits this outcome before attempting a qualification transaction.
    """
    for value in (project_key, release_key, audit_key, evidence_key, actor_id):
        require_name(value)
    if canary_model_key is not None:
        require_name(canary_model_key)
    record = json.loads(dumps(evidence))
    with connection.begin_nested():
        project_id = lock_project(connection, project_key)
        audit = _audit(connection, project_id, release_key, audit_key)
        _bound_evidence(connection, project_key, release_key, audit_key, evidence, False)
        model_id, digest = (
            _canary(connection, project_id, canary_model_key)
            if canary_model_key is not None
            else (None, None)
        )
        connection.execute(
            sa.insert(qualification_schema.annotation_evidence).values(
                project_id=project_id,
                evidence_key=evidence_key,
                audit_id=audit["id"],
                actor_id=actor_id,
                record=record,
                canary_model_id=model_id,
                canary_release_sha256=digest,
            )
        )
    return StoredAnnotationEvidence(evidence, digest)


def select_annotation_evidence(
    connection: sa.Connection,
    *,
    project_key: str,
    release_key: str,
    canary_model_key: str | None = None,
) -> StoredAnnotationEvidence:
    """Select the sole outcome for a trusted population and role, never by quality.

    Every outcome counts, including partial, adverse and stale-model-digest rows.
    Ambiguity requires an explicit trusted decision; neither scores nor timestamps
    break ties. Population selection and measurement provenance remain caller duties.
    The exact reader still validates the audit, current groups and model digest.
    """
    for value in (project_key, release_key):
        require_name(value)
    if canary_model_key is not None:
        require_name(canary_model_key)
    project_id = lock_project(connection, project_key)
    model_id = (
        _canary(connection, project_id, canary_model_key)[0]
        if canary_model_key is not None
        else None
    )
    outcome = qualification_schema.annotation_evidence
    audit = qualification_schema.annotation_audit
    rows = (
        connection.execute(
            sa.select(audit.c.audit_key, outcome.c.evidence_key)
            .select_from(outcome.join(audit).join(canonical_dataset_release))
            .where(
                outcome.c.project_id == project_id,
                audit.c.project_id == project_id,
                canonical_dataset_release.c.project_id == project_id,
                canonical_dataset_release.c.release_key == release_key,
                outcome.c.canary_model_id == model_id,
            )
            .limit(2)
        )
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise ValueError("qualification requires one unambiguous measured annotation outcome")
    return load_annotation_evidence(
        connection,
        project_key=project_key,
        release_key=release_key,
        audit_key=rows[0]["audit_key"],
        evidence_key=rows[0]["evidence_key"],
        canary_model_key=canary_model_key,
        require_current_groups=True,
    )


def load_annotation_evidence(
    connection: sa.Connection,
    *,
    project_key: str,
    release_key: str,
    audit_key: str,
    evidence_key: str,
    canary_model_key: str | None = None,
    require_current_groups: bool = True,
) -> StoredAnnotationEvidence:
    """Reload exact immutable outcomes; historical reads never confer qualification.

    Callers must select a canary model explicitly. Ordinary audit evidence cannot
    substitute for a canary, nor can one candidate reuse another candidate's result.
    Retain the outer transaction/project and current-group locks through the decision.
    """
    for value in (project_key, release_key, audit_key, evidence_key):
        require_name(value)
    if canary_model_key is not None:
        require_name(canary_model_key)
    project_id = lock_project(connection, project_key)
    audit = _audit(connection, project_id, release_key, audit_key)
    row = (
        connection.execute(
            sa.select(qualification_schema.annotation_evidence).where(
                qualification_schema.annotation_evidence.c.project_id == project_id,
                qualification_schema.annotation_evidence.c.evidence_key == evidence_key,
                qualification_schema.annotation_evidence.c.audit_id == audit["id"],
            )
        )
        .mappings()
        .one()
    )
    require_name(row["actor_id"])
    evidence = loads(json.dumps(row["record"]), AnnotationEvidence)
    _bound_evidence(
        connection, project_key, release_key, audit_key, evidence, require_current_groups
    )
    expected = (
        _canary(connection, project_id, canary_model_key)
        if canary_model_key is not None
        else (None, None)
    )
    if (row["canary_model_id"], row["canary_release_sha256"]) != expected:
        raise ValueError("canary evidence requires its exact current model release")
    return StoredAnnotationEvidence(evidence, row["canary_release_sha256"])
