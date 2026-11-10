"""Project setup facts from canonical rows; absent setup evidence stays absent."""

from sqlalchemy import Connection, func, select

from labelloop_core.domain import ClassDefinition, OntologyRelease

from .management_canonical import refuse
from .management_contracts import (
    ClassCatalogDraft,
    GuideDraft,
    Resource,
    ScopeDraft,
    SourcePolicyDraft,
)
from .management_details import (
    ClassCatalogRead,
    ClassRead,
    GuideRead,
    ProjectDetail,
    SourceGroupsRead,
    SourcePairRead,
    SourcePolicyRead,
)
from .management_grouping import validate_group_membership
from .management_ontology import read_catalog_publication
from .management_ports import ManagementFailure
from .management_references import read_references
from .management_setup import (
    approved_setup_summary_sha256,
    draft_sha256,
    published_catalog_content,
    read_setup_approvals,
)
from .schema import (
    borderline_review,
    canonical_event,
    ontology_release,
    source_asset,
    source_split_assignment,
)


def source_groups(connection: Connection, project_id) -> SourceGroupsRead:
    assets = connection.execute(
        select(source_asset.c.id, source_asset.c.asset_key, source_asset.c.sha256)
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
        try:
            validate_group_membership(
                connection, project_id, assets, {row.asset_id: row for row in assignments}
            )
        except ManagementFailure as failure:
            if failure.error.detail != "missing_evidence":
                raise
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


def scope_draft(connection: Connection, project_row, workspace_id) -> ScopeDraft | None:
    receipt = connection.execute(
        select(canonical_event)
        .where(
            canonical_event.c.aggregate_id == str(project_row.id),
            canonical_event.c.event_type == "management.save_scope_draft",
            canonical_event.c.actor_kind == "human",
            canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
        )
        .order_by(canonical_event.c.expected_revision.desc())
        .limit(1)
    ).one_or_none()
    stored = project_row.access_policy.get("management_scope_draft")
    if receipt is None:
        if stored is not None:
            refuse("missing_evidence")
        return None
    evidence = receipt.payload["evidence"]["scope_draft"]
    draft = ScopeDraft.model_validate({**evidence, "expected_revision": receipt.expected_revision})
    if (
        stored != evidence
        or project_row.scope != draft.scope
        or project_row.domain != draft.domain
        or project_row.access_policy["source_permitted_use"] != draft.source_permitted_use
        or project_row.access_policy.get("management_setup_approvals") is None
    ):
        refuse("missing_evidence")
    return draft


def guide_draft(connection: Connection, project_row, workspace_id) -> GuideDraft | None:
    receipt = connection.execute(
        select(canonical_event)
        .where(
            canonical_event.c.aggregate_id == str(project_row.id),
            canonical_event.c.event_type == "management.save_guide_draft",
            canonical_event.c.actor_kind == "human",
            canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
        )
        .order_by(canonical_event.c.expected_revision.desc())
        .limit(1)
    ).one_or_none()
    stored = project_row.access_policy.get("management_guide_draft")
    if receipt is None:
        if stored is not None:
            refuse("missing_evidence")
        return None
    evidence = receipt.payload["evidence"]["guide_draft"]
    draft = GuideDraft.model_validate({**evidence, "expected_revision": receipt.expected_revision})
    if (
        stored != evidence
        or project_row.access_policy["annotation_guide"] != draft.annotation_guide
        or project_row.access_policy.get("management_setup_approvals") is None
    ):
        refuse("missing_evidence")
    return draft


def class_catalog_draft(
    connection: Connection, project_row, workspace_id, ontology_key: str
) -> ClassCatalogRead | None:
    receipt = connection.execute(
        select(canonical_event)
        .where(
            canonical_event.c.aggregate_id == str(project_row.id),
            canonical_event.c.event_type == "management.save_class_catalog_draft",
            canonical_event.c.actor_kind == "human",
            canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
        )
        .order_by(canonical_event.c.expected_revision.desc())
        .limit(1)
    ).one_or_none()
    stored = project_row.access_policy.get("management_class_catalog_draft")
    publication = read_catalog_publication(connection, project_row, workspace_id)
    if receipt is None:
        if stored is not None or publication is not None:
            refuse("missing_evidence")
        return None
    evidence = receipt.payload["evidence"]["class_catalog_draft"]
    if stored is None and publication is not None:
        if (
            publication.expected_revision <= receipt.expected_revision
            or publication.payload["evidence"]["class_catalog"] != evidence
        ):
            refuse("missing_evidence")
        return None
    draft = ClassCatalogDraft.model_validate(
        {**evidence, "expected_revision": receipt.expected_revision}
    )
    if (
        stored != evidence
        or draft.base_ontology_id != ontology_key
        or project_row.access_policy.get("management_setup_approvals") is None
    ):
        refuse("missing_evidence")
    return ClassCatalogRead.model_validate(draft.model_dump(exclude={"expected_revision"}))


def source_policy_draft(
    connection: Connection, project_row, workspace_id
) -> SourcePolicyRead | None:
    receipt = connection.execute(
        select(canonical_event)
        .where(
            canonical_event.c.aggregate_id == str(project_row.id),
            canonical_event.c.event_type == "management.save_source_policy_draft",
            canonical_event.c.actor_kind == "human",
            canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
        )
        .order_by(canonical_event.c.expected_revision.desc())
        .limit(1)
    ).one_or_none()
    stored = project_row.access_policy.get("management_source_policy_draft")
    if receipt is None:
        if stored is not None:
            refuse("missing_evidence")
        return None
    evidence = receipt.payload["evidence"]["source_policy_draft"]
    draft = SourcePolicyDraft.model_validate(
        {**evidence, "expected_revision": receipt.expected_revision}
    )
    if stored != evidence or project_row.access_policy.get("management_setup_approvals") is None:
        refuse("missing_evidence")
    return SourcePolicyRead.model_validate(draft.model_dump(exclude={"expected_revision"}))


def project_detail(
    connection: Connection, project_row, revision: int, workspace_id, *, lock_sources=False
) -> ProjectDetail:
    # Normal callers own a repeatable read snapshot. Mutation callers instead
    # hold the project lock and must retain source locking through evidence reads.
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
    draft = scope_draft(connection, project_row, workspace_id)
    guide = guide_draft(connection, project_row, workspace_id)
    catalog = class_catalog_draft(connection, project_row, workspace_id, ontology.ontology_key)
    source_policy = source_policy_draft(connection, project_row, workspace_id)
    published = published_catalog_content(connection, project_row, workspace_id)
    published_classes = (
        {item["stable_id"]: item for item in published["classes"]} if published else {}
    )
    approvals = read_setup_approvals(
        connection, project_row, workspace_id, lock_sources=lock_sources
    )
    pins = {
        step: draft_sha256(value.model_dump(mode="json", exclude={"expected_revision"}))
        for step, value in (
            ("scope", draft),
            ("guide", guide),
            ("classes", catalog),
            ("sources", source_policy),
        )
        if value is not None
    }
    if published is not None:
        pins["classes"] = draft_sha256(published)
    references = read_references(connection, project_row, workspace_id, lock_sources=lock_sources)
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
        name=draft.name if draft else None,
        scope=project_row.scope,
        domain=project_row.domain,
        representation=draft.representation if draft else None,
        intended_use=draft.intended_use if draft else None,
        specificity=draft.specificity if draft else None,
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
                exclusions=published_classes[item.stable_id]["exclusions"]
                if item.stable_id in published_classes
                else None,
                mirror_safe=published_classes[item.stable_id]["mirror_safe"]
                if item.stable_id in published_classes
                else None,
                approval="admin_approved" if "classes" in approvals else None,
            )
            for item in canonical.classes
        ],
        class_catalog_draft=catalog,
        references=references,
        annotation_guide=policy["annotation_guide"],
        guide=GuideRead.model_validate(
            guide.model_dump(exclude={"expected_revision", "annotation_guide"})
        )
        if guide
        else None,
        sources=sources,
        source_policy_draft=source_policy,
        setup_draft_sha256=pins,
        approved_steps=approvals
        if draft or guide or catalog or published or source_policy or references
        else None,
        setup_summary_sha256=approved_setup_summary_sha256(project_row, approvals, pins, versions),
        versions=versions,
        published_release_id=None,
        saved_at=receipts[0].created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
