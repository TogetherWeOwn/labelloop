"""Frozen release projections use pinned revisions, never today's review state."""

from collections import Counter, defaultdict

from sqlalchemy import Connection, select, tuple_

from .management_canonical import refuse
from .management_contracts import Resource
from .management_details import (
    CheckRead,
    ExclusionRead,
    ReleaseClassRead,
    ReleaseDetail,
    ReleaseMemberRead,
)
from .schema import (
    annotation_revision,
    canonical_dataset_release,
    canonical_event,
    ontology_release,
    source_asset,
    source_split_assignment,
)


def release_members(connection: Connection, release):
    pins = release.sample_revisions
    if not pins or len(pins) > 10000 or len(release.excluded_asset_ids) > 10000:
        refuse("missing_evidence")
    rows = connection.execute(
        select(
            source_asset.c.asset_key,
            source_asset.c.sha256,
            annotation_revision.c.revision,
            annotation_revision.c.objects,
            annotation_revision.c.actor_kind,
            annotation_revision.c.approval_state,
            annotation_revision.c.review_tier,
            annotation_revision.c.scope_completeness,
            source_split_assignment.c.split,
        )
        .join(annotation_revision, annotation_revision.c.asset_id == source_asset.c.id)
        .join(source_split_assignment, source_split_assignment.c.asset_id == source_asset.c.id)
        .where(
            source_asset.c.project_id == release.project_id,
            tuple_(source_asset.c.asset_key, annotation_revision.c.revision).in_(
                list(pins.items())
            ),
            source_split_assignment.c.assignment_version == release.assignment_version,
            source_split_assignment.c.locked.is_(True),
        )
        .order_by(source_asset.c.asset_key)
    ).all()
    if len(rows) != len(pins):
        refuse("missing_evidence")
    members = []
    classes = defaultdict(Counter)
    provenance = Counter()
    for row in rows:
        members.append(
            ReleaseMemberRead(
                asset_id=row.asset_key,
                annotation_revision=row.revision,
                source_sha256=row.sha256,
                split=row.split,
                objects=len(row.objects),
                review_tier=row.review_tier,
                scope_completeness=row.scope_completeness,
            )
        )
        if row.actor_kind == "human" and row.approval_state == "human_accepted":
            provenance["human"] += len(row.objects)
        elif row.actor_kind == "system" and row.approval_state == "auto_accepted":
            provenance["auto"] += len(row.objects)
        else:
            refuse("missing_evidence")
        for item in row.objects:
            classes[item["class_id"]][row.split] += 1
    return members, classes, provenance


def release_detail(connection: Connection, release, workspace_id) -> ReleaseDetail:
    # Only a recorded management freeze supplies the attribution/policy. A
    # legacy row or a missing receipt must not become a successful detail read.
    events = (
        select(canonical_event)
        .where(
            canonical_event.c.aggregate_id == str(release.project_id),
            canonical_event.c.actor_kind == "human",
            canonical_event.c.event_type == "management.freeze_release",
            canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
        )
        .subquery()
    )
    receipts = connection.execute(
        select(events)
        .where(events.c.payload["result"]["id"].as_string() == release.release_key)
        .limit(2)
    ).all()
    if len(receipts) != 1 or not release.frozen:
        refuse("missing_evidence")
    receipt = receipts[0]
    evidence = receipt.payload["evidence"]
    if (
        evidence.get("acceptance_policy_id") != "canonical-training-eligibility-v1"
        or not release.hashes.get("canonical_snapshot_sha256")
        or evidence.get("canonical_snapshot_sha256") != release.hashes["canonical_snapshot_sha256"]
    ):
        refuse("missing_evidence")
    resource = Resource(
        id=release.release_key,
        project_id=str(release.project_id),
        kind="dataset-release",
        revision=1,
        state="frozen",
    )
    if Resource.model_validate(receipt.payload["result"]) != resource:
        refuse("missing_evidence")
    ontology = connection.execute(
        select(ontology_release).where(
            ontology_release.c.id == release.ontology_id,
            ontology_release.c.project_id == release.project_id,
        )
    ).one_or_none()
    if ontology is None:
        refuse("missing_evidence")
    members, classes, provenance = release_members(connection, release)
    if not classes.keys() <= ontology.class_map.keys():
        refuse("missing_evidence")
    prior = connection.execute(
        select(canonical_dataset_release)
        .join(
            events,
            events.c.payload["result"]["id"].as_string() == canonical_dataset_release.c.release_key,
        )
        .where(
            canonical_dataset_release.c.project_id == release.project_id,
            canonical_dataset_release.c.frozen.is_(True),
            events.c.expected_revision < receipt.expected_revision,
        )
        .order_by(events.c.expected_revision.desc())
        .limit(1)
    ).one_or_none()
    previous_counts = release_members(connection, prior)[1] if prior else {}
    previous_pins = prior.sample_revisions if prior else {}
    exclusions = defaultdict(list)
    for asset_id, reason in sorted(release.excluded_asset_ids.items()):
        exclusions[reason].append(asset_id)
    return ReleaseDetail(
        resource=resource,
        previous_release_id=prior.release_key if prior else None,
        ontology_id=ontology.ontology_key,
        assignment_version=release.assignment_version,
        members=members,
        classes=[
            ReleaseClassRead(
                class_id=class_id,
                train=classes[class_id]["train"],
                val=classes[class_id]["val"],
                test=classes[class_id]["test"],
                delta=sum(classes[class_id].values())
                - sum(previous_counts.get(class_id, {}).values()),
                minimum_support=None,
            )
            for class_id in sorted(ontology.class_map, key=ontology.class_map.get)
        ],
        exclusions=[
            ExclusionRead(reason=reason, asset_ids=asset_ids)
            for reason, asset_ids in sorted(exclusions.items())
        ],
        checks=[
            CheckRead(
                code="canonical_freeze",
                passed=True,
                reason="Recorded canonical eligibility, not model qualification",
            ),
            CheckRead(code="model_qualification", passed=None, reason="No model evidence here"),
        ],
        changed_asset_ids=sorted(
            key
            for key in release.sample_revisions.keys() | previous_pins.keys()
            if release.sample_revisions.get(key) != previous_pins.get(key)
        ),
        auto_accepted_objects=provenance["auto"],
        human_reviewed_objects=provenance["human"],
        acceptance_policy_id=evidence["acceptance_policy_id"],
        quarantined_reason=None,
        manifest_artifact_id=None,
        manifest_sha256=None,
        frozen_at=receipt.created_at.isoformat(),
        frozen_by=receipt.actor_id,
    )
