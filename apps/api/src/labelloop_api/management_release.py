"""Load a freeze candidate from canonical rows, never request-supplied labels.

Mutation callers own the project lock and transaction and keep source locking
on. Only read-only snapshot preparation disables it. Review writers synchronize
on source rows through append_review; setup/grouping writers must lock the
project before publishing ontology, relation or assignment versions.
"""

from typing import NoReturn

from sqlalchemy import Connection, select

from labelloop_core.domain import (
    AnnotationRevision,
    CanonicalDatasetRelease,
    ClassDefinition,
    DatasetObject,
    OntologyRelease,
    ReleasedSample,
    SourceAsset,
    SourceSplitAssignment,
)
from labelloop_core.geometry import Box

from .management_contracts import ManagementError, ReleaseFreeze
from .management_grouping import load_grouping
from .management_ports import ManagementFailure
from .revision_store import completeness_context_hash
from .schema import (
    annotation_revision,
    canonical_event,
    completeness_assessment,
    ontology_release,
)


def reject(code: str) -> NoReturn:
    raise ManagementFailure(ManagementError.model_validate({"detail": code}))


def revision_refusal(project_row, context_hash: str, current, assessment) -> str | None:
    if (
        assessment is None
        or assessment.ontology_hash != context_hash
        or assessment.scope_completeness != "exhaustive_for_scope"
        or assessment.output_truncated
        or assessment.unresolved_regions
        or not current.training_eligible
        or current.review_tier == "unreviewed"
    ):
        return "incomplete_labels"
    if current.actor_kind == "system":
        qualification = project_row.access_policy.get("auto_accept_qualification", {})
        if (
            not qualification.get("id")
            or qualification["id"] != current.actor_id
            or current.approval_state != "auto_accepted"
            or current.review_tier != "machine_verified_no_human_review"
        ):
            return "missing_evidence"
    return None


def freeze_candidate(
    connection: Connection,
    project_row,
    payload: ReleaseFreeze,
    release_id: str,
    *,
    lock_sources: bool = True,
) -> CanonicalDatasetRelease:
    # Draft saves invalidate setup acceptance, including edits that leave the
    # old scope string unchanged. Publication/approval is a separate producer;
    # until it is implemented, an edited draft cannot freeze a new release.
    edited = connection.execute(
        select(canonical_event.c.id)
        .where(
            canonical_event.c.aggregate_id == str(project_row.id),
            canonical_event.c.event_type.in_(
                [
                    "management.save_scope_draft",
                    "management.save_guide_draft",
                    "management.save_class_catalog_draft",
                    "management.save_source_policy_draft",
                    "management.enroll_reference",
                ]
            ),
        )
        .limit(1)
    ).first()
    if edited is not None or any(
        key in project_row.access_policy
        for key in (
            "management_scope_draft",
            "management_guide_draft",
            "management_class_catalog_draft",
            "management_source_policy_draft",
        )
    ):
        reject("missing_evidence")
    ontology = connection.execute(
        select(ontology_release)
        .where(ontology_release.c.project_id == project_row.id)
        .order_by(ontology_release.c.version.desc())
        .limit(1)
    ).one_or_none()
    # The bootstrap ontology key is public; the SQL UUID is an implementation ID.
    if ontology is None or ontology.ontology_key != payload.ontology_id:
        reject("revision_conflict")
    assets, by_asset, assignments, group_splits = load_grouping(
        connection, project_row, payload.assignment_version, lock_sources=lock_sources
    )
    by_key = {row.asset_key: row for row in assets}
    if not set(payload.annotation_revisions) <= by_key.keys():
        reject("not_found")
    context_hash = completeness_context_hash(
        ontology.class_map, ontology.classes, project_row.scope
    )
    selected_ids = [by_key[key].id for key in payload.annotation_revisions]
    latest_revisions = (
        select(annotation_revision)
        .where(annotation_revision.c.asset_id.in_(selected_ids))
        .distinct(annotation_revision.c.asset_id)
        .order_by(annotation_revision.c.asset_id, annotation_revision.c.revision.desc())
        .subquery()
    )
    revisions = {row.asset_id: row for row in connection.execute(select(latest_revisions))}
    assessments = {
        row.asset_id: row
        for row in connection.execute(
            select(completeness_assessment).join(
                latest_revisions,
                (completeness_assessment.c.asset_id == latest_revisions.c.asset_id)
                & (completeness_assessment.c.revision == latest_revisions.c.revision),
            )
        )
    }
    samples = []
    excluded = {}
    for asset in assets:
        requested_revision = payload.annotation_revisions.get(asset.asset_key)
        if requested_revision is None:
            excluded[asset.asset_key] = "not_selected"
            continue
        current = revisions.get(asset.id)
        if current is None or current.revision != requested_revision:
            reject("revision_conflict")
        assessment = assessments.get(asset.id)
        if reason := revision_refusal(project_row, context_hash, current, assessment):
            reject(reason)
        assignment = by_asset[asset.id]
        try:
            revision = AnnotationRevision(
                revision=current.revision,
                parent_revision=current.parent_revision,
                actor_kind=current.actor_kind,
                approval_state=current.approval_state,
                review_tier=current.review_tier,
                scope_completeness=current.scope_completeness,
                training_eligible=current.training_eligible,
                gold_eligible=current.gold_eligible,
                objects=tuple(
                    DatasetObject(
                        object_id=item["object_id"],
                        class_id=item["class_id"],
                        box=Box(*item["box"]),
                        label_state=item.get("label_state", "identified"),
                        attributes=item.get("attributes", {}),
                        occluded=item.get("occluded", False),
                        truncated_at_image_edge=item.get("truncated_at_image_edge", False),
                    )
                    for item in current.objects
                ),
            )
            samples.append(
                ReleasedSample(
                    asset=SourceAsset(
                        asset_id=asset.asset_key,
                        project_id=str(project_row.id),
                        sha256=asset.sha256,
                        object_key=asset.object_key,
                        width=asset.width,
                        height=asset.height,
                        exif_orientation=asset.exif_orientation,
                        source_uri=asset.source_uri,
                        source_group=assignment.group_id,
                    ),
                    revision=revision,
                    group_id=assignment.group_id,
                    split=assignment.split,
                )
            )
        except (KeyError, TypeError, ValueError):
            reject("incomplete_labels")
    try:
        canonical_ontology = OntologyRelease(
            ontology_id=ontology.ontology_key,
            project_id=str(project_row.id),
            version=ontology.version,
            classes=tuple(ClassDefinition(**item) for item in ontology.classes),
            class_map=ontology.class_map,
        )
        assignment = SourceSplitAssignment(
            assignment_version=payload.assignment_version,
            asset_splits={asset.asset_key: by_asset[asset.id].split for asset in assets},
            group_splits=group_splits,
            group_ids={asset.asset_key: by_asset[asset.id].group_id for asset in assets},
            seed=assignments[0].seed,
            algorithm=assignments[0].algorithm,
            reasons={asset.asset_key: by_asset[asset.id].reason for asset in assets},
            locked=True,
        )
        return CanonicalDatasetRelease(
            release_id=release_id,
            project_id=str(project_row.id),
            ontology=canonical_ontology,
            assignment=assignment,
            samples=tuple(samples),
            excluded_asset_ids=excluded,
        )
    except (KeyError, TypeError, ValueError):
        reject("incomplete_labels")
