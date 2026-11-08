"""Project setup facts from canonical rows; absent setup evidence stays absent."""

from sqlalchemy import Connection, func, select

from labelloop_core.domain import ClassDefinition, OntologyRelease

from .management_canonical import refuse
from .management_contracts import Resource
from .management_details import ClassRead, ProjectDetail, SourceGroupsRead, SourcePairRead
from .schema import (
    borderline_review,
    canonical_event,
    ontology_release,
    source_asset,
    source_split_assignment,
)


def source_groups(connection: Connection, project_id) -> SourceGroupsRead:
    assets = connection.execute(
        select(source_asset.c.id, source_asset.c.asset_key)
        .where(source_asset.c.project_id == project_id)
        .order_by(source_asset.c.id)
        .limit(10001)
    ).all()
    if len(assets) > 10000:
        refuse("management_unavailable")
    by_id = {asset.id: asset.asset_key for asset in assets}
    pairs = connection.execute(
        select(borderline_review)
        .where(
            (
                borderline_review.c.left_asset_id.in_(by_id)
                | borderline_review.c.right_asset_id.in_(by_id)
            ),
            borderline_review.c.review_state == "pending",
        )
        .order_by(borderline_review.c.id)
        .limit(10001)
    ).all()
    if len(pairs) > 10000:
        refuse("management_unavailable")
    if any(pair.left_asset_id not in by_id or pair.right_asset_id not in by_id for pair in pairs):
        refuse("missing_evidence")
    pending = [
        SourcePairRead(
            id=str(pair.id),
            left_asset_id=by_id[pair.left_asset_id],
            right_asset_id=by_id[pair.right_asset_id],
        )
        for pair in pairs
    ]
    # Return all latest ties, not an arbitrarily selected assignment. One
    # complete, locked version is required to report materialized group counts.
    latest = (
        select(
            source_split_assignment.c.asset_id,
            func.max(source_split_assignment.c.created_at).label("created_at"),
        )
        .where(source_split_assignment.c.asset_id.in_(by_id))
        .group_by(source_split_assignment.c.asset_id)
        .subquery()
    )
    assignments = connection.execute(
        select(source_split_assignment)
        .join(
            latest,
            (source_split_assignment.c.asset_id == latest.c.asset_id)
            & (source_split_assignment.c.created_at == latest.c.created_at),
        )
        .limit(10001)
    ).all()
    usable = (
        bool(assets)
        and len(assignments) == len(assets)
        and len({row.asset_id for row in assignments}) == len(assets)
        and len({row.assignment_version for row in assignments}) == 1
        and all(row.locked for row in assignments)
        and all(
            (row.seed, row.algorithm, row.thresholds)
            == (assignments[0].seed, assignments[0].algorithm, assignments[0].thresholds)
            for row in assignments
        )
    )
    if usable:
        splits = {}
        for row in assignments:
            if splits.setdefault(row.group_id, row.split) != row.split:
                usable = False
    return SourceGroupsRead(
        images=len(assets),
        groups=len({row.group_id for row in assignments}) if usable else None,
        metadata_keys=None,
        pending_pairs=pending,
        split_counts={
            split: sum(row.split == split for row in assignments)
            for split in ("train", "val", "test")
        }
        if usable
        else None,
        assignment_version=assignments[0].assignment_version if usable else None,
    )


def project_detail(
    connection: Connection, project_row, revision: int, workspace_id
) -> ProjectDetail:
    ontology = connection.execute(
        select(ontology_release)
        .where(ontology_release.c.project_id == project_row.id)
        .order_by(ontology_release.c.version.desc())
        .limit(1)
    ).one_or_none()
    if ontology is None or len(ontology.classes) > 1000:
        refuse("missing_evidence")
    # Reuse canonical class-map validation; an admin-created ontology alone is
    # not evidence that individual classes or setup steps were approved.
    canonical = OntologyRelease(
        ontology_id=ontology.ontology_key,
        project_id=str(project_row.id),
        version=ontology.version,
        classes=tuple(ClassDefinition(**item) for item in ontology.classes),
        class_map=ontology.class_map,
    )
    receipts = connection.execute(
        select(canonical_event.c.created_at)
        .where(
            canonical_event.c.aggregate_id == str(project_row.id),
            canonical_event.c.event_type.like("management.%"),
            canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
            canonical_event.c.expected_revision == revision - 1,
            canonical_event.c.actor_kind == "human",
        )
        .limit(2)
    ).all()
    if len(receipts) != 1:
        refuse("missing_evidence")
    policy = project_row.access_policy
    sources = source_groups(connection, project_row.id)
    versions = {"ontology": str(ontology.version), "ontology_id": ontology.ontology_key}
    if sources.assignment_version is not None:
        versions["assignment"] = sources.assignment_version
    return ProjectDetail(
        resource=Resource(
            id=str(project_row.id),
            project_id=str(project_row.id),
            kind="project",
            revision=revision,
            state="draft",
        ),
        name=None,
        scope=project_row.scope,
        domain=project_row.domain,
        representation=None,
        intended_use=None,
        specificity=None,
        source_permitted_use=policy["source_permitted_use"],
        external_provider_ids=policy["external_llm_providers_allowed"],
        classes=[
            ClassRead(
                stable_id=item.stable_id,
                name=item.name,
                definition=item.definition,
                aliases=list(item.aliases),
                parent_id=item.parent_id,
                positive_examples=None,
                exclusions=None,
                mirror_safe=None,
                approval=None,
            )
            for item in canonical.classes
        ],
        references=None,
        annotation_guide=policy["annotation_guide"],
        guide=None,
        sources=sources,
        approved_steps=None,
        versions=versions,
        published_release_id=None,
        saved_at=receipts[0].created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
