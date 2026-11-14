"""Read-only freeze preparation from one canonical snapshot, never an approval.

The reader owns a REPEATABLE READ, READ ONLY transaction. Reuse the write-side
eligibility and grouping validator without its row locks. POST always repeats
validation under the canonical mutation locks at the pinned project revision.
"""

from sqlalchemy import Connection, select

from .management_contracts import ReleaseFreeze
from .management_details import CheckRead, ExclusionRead, FreezePlanRead
from .management_grouping import current_assignment_version
from .management_ports import ManagementFailure
from .management_release import freeze_candidate, revision_refusal
from .revision_store import completeness_context_hash
from .schema import (
    annotation_revision,
    completeness_assessment,
    ontology_release,
    source_asset,
    source_split_assignment,
)


def unavailable_plan(project_id: str, reason: str, exclusions: list[ExclusionRead]):
    return FreezePlanRead(
        project_id=project_id,
        payload=None,
        checks=[CheckRead(code="canonical_freeze", passed=False, reason=reason)],
        exclusions=exclusions,
    )


def freeze_plan(
    connection: Connection, project_row, project_revision: int, *, configuration_release=None
) -> FreezePlanRead:
    project_id = str(project_row.id)
    # The published request can carry at most 10,000 selected revisions. Bound
    # preparation too; a larger project requires an explicit selection surface.
    assets = connection.execute(
        select(source_asset)
        .where(source_asset.c.project_id == project_row.id)
        .order_by(source_asset.c.id)
        .limit(10001)
    ).all()
    if not assets or len(assets) > 10000:
        return unavailable_plan(project_id, "missing_evidence", [])
    ontology = connection.execute(
        select(ontology_release)
        .where(ontology_release.c.project_id == project_row.id)
        .order_by(ontology_release.c.version.desc())
        .limit(1)
    ).one_or_none()
    if ontology is None:
        return unavailable_plan(project_id, "missing_evidence", [])
    identities = [asset.id for asset in assets]
    version = current_assignment_version(connection, project_row.id, identities)
    if version is None:
        return unavailable_plan(project_id, "missing_evidence", [])
    assignments = connection.execute(
        select(source_split_assignment).where(
            source_split_assignment.c.asset_id.in_(identities),
            source_split_assignment.c.assignment_version == version,
        )
    ).all()
    if len(assignments) != len(assets):
        return unavailable_plan(project_id, "missing_evidence", [])
    latest = (
        select(annotation_revision)
        .where(annotation_revision.c.asset_id.in_(identities))
        .distinct(annotation_revision.c.asset_id)
        .order_by(annotation_revision.c.asset_id, annotation_revision.c.revision.desc())
        .subquery()
    )
    revisions = {row.asset_id: row for row in connection.execute(select(latest))}
    assessments = {
        row.asset_id: row
        for row in connection.execute(
            select(completeness_assessment).join(
                latest,
                (completeness_assessment.c.asset_id == latest.c.asset_id)
                & (completeness_assessment.c.revision == latest.c.revision),
            )
        )
    }
    context_hash = completeness_context_hash(
        ontology.class_map, ontology.classes, project_row.scope
    )
    selected = {}
    excluded: dict[str, list[str]] = {}
    for asset in assets:
        current = revisions.get(asset.id)
        reason = (
            "missing_evidence"
            if current is None
            else revision_refusal(project_row, context_hash, current, assessments.get(asset.id))
        )
        if reason:
            excluded.setdefault(reason, []).append(asset.asset_key)
        else:
            selected[asset.asset_key] = current.revision
    exclusions = [
        ExclusionRead(reason=reason, asset_ids=sorted(keys))
        for reason, keys in sorted(excluded.items())
    ]
    if not selected:
        return unavailable_plan(project_id, "incomplete_labels", exclusions)
    payload = ReleaseFreeze(
        expected_revision=project_revision,
        ontology_id=ontology.ontology_key,
        assignment_version=version,
        annotation_revisions=selected,
    )
    try:
        # No storage or DB writes; the domain constructor validates the same
        # complete membership and geometry that POST will later freeze.
        freeze_candidate(
            connection,
            project_row,
            payload,
            "freeze-plan",
            lock_sources=False,
            configuration_release=configuration_release,
        )
    except ManagementFailure as failure:
        return unavailable_plan(project_id, failure.error.detail, exclusions)
    return FreezePlanRead(
        project_id=project_id,
        configuration=configuration_release.pin if configuration_release is not None else None,
        payload=payload,
        checks=[
            CheckRead(
                code="canonical_freeze",
                passed=True,
                reason="Eligible at this snapshot; POST must recheck. Not model qualification.",
            )
        ],
        exclusions=exclusions,
    )
