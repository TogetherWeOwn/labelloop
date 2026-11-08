"""Proposed canonical references; enrollment is not approval or retrieval authority."""

import json
from uuid import uuid4

from sqlalchemy import Connection, func, insert, select

from .management_contracts import ManagementError, ReferenceEnroll
from .management_details import ReferenceRead
from .management_grouping import load_grouping
from .management_ports import ManagementFailure
from .schema import (
    canonical_event,
    ontology_release,
    reference_example,
    source_asset,
    source_split_assignment,
)


def reject(code):
    raise ManagementFailure(ManagementError(detail=code))


def enroll_reference(
    connection: Connection, project_row, payload: ReferenceEnroll
) -> ReferenceRead:
    ontology = connection.execute(
        select(ontology_release)
        .where(ontology_release.c.project_id == project_row.id)
        .order_by(ontology_release.c.version.desc())
        .limit(1)
    ).one_or_none()
    if ontology is None or ontology.ontology_key != payload.base_ontology_id:
        reject("revision_conflict")
    if not {payload.class_id, *payload.confused_with} <= ontology.class_map.keys():
        reject("unsupported_classes")
    # Draft classes cannot be used as if their identities were already published.
    if "management_class_catalog_draft" in project_row.access_policy:
        reject("missing_evidence")
    asset = connection.execute(
        select(source_asset).where(
            source_asset.c.project_id == project_row.id,
            source_asset.c.asset_key == payload.asset_id,
        )
    ).one_or_none()
    if asset is None:
        reject("not_found")
    assets, by_asset, _, _ = load_grouping(connection, project_row, payload.assignment_version)
    if by_asset[asset.id].split != "train":
        reject("missing_evidence")
    # Conservative lifetime exclusion: a new assignment never launders a prior
    # holdout (or byte-identical copy) into visual memory. No cross-project import.
    hashes = {row.id: row.sha256 for row in assets}
    historical_holdouts = (
        connection.execute(
            select(source_split_assignment.c.asset_id).where(
                source_split_assignment.c.asset_id.in_(hashes),
                source_split_assignment.c.split.in_(["val", "test"]),
            )
        )
        .scalars()
        .all()
    )
    if any(
        hashes[identifier] == asset.sha256
        or by_asset[identifier].group_id == by_asset[asset.id].group_id
        for identifier in historical_holdouts
    ):
        reject("missing_evidence")
    count = connection.execute(
        select(func.count())
        .select_from(reference_example)
        .join(source_asset)
        .where(source_asset.c.project_id == project_row.id)
    ).scalar_one()
    if count >= 1000:
        reject("management_unavailable")
    reference = ReferenceRead(
        id=str(uuid4()),
        asset_id=asset.asset_key,
        class_id=payload.class_id,
        source=payload.source,
        permitted_use=payload.permitted_use,
        view=payload.view,
        lighting=payload.lighting,
        scale=payload.scale,
        confused_with=payload.confused_with,
        validity_scope=payload.validity_scope,
        approval="proposed",
    )
    connection.execute(
        insert(reference_example).values(
            id=reference.id,
            asset_id=asset.id,
            class_id=reference.class_id,
            crop=None,
            evidence_provenance=json.dumps(reference.model_dump(mode="json"), sort_keys=True),
            approved=False,
            validity_scope=reference.validity_scope,
            embedding_model=None,
        )
    )
    return reference


def proposed_references(
    connection: Connection, project_row, workspace_id
) -> list[ReferenceRead] | None:
    rows = connection.execute(
        select(reference_example, source_asset.c.asset_key)
        .join(source_asset)
        .where(source_asset.c.project_id == project_row.id)
        .order_by(reference_example.c.id)
        .limit(1001)
    ).all()
    receipts = (
        connection.execute(
            select(canonical_event.c.payload)
            .where(
                canonical_event.c.aggregate_id == str(project_row.id),
                canonical_event.c.event_type == "management.enroll_reference",
                canonical_event.c.actor_kind == "human",
                canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
            )
            .limit(1001)
        )
        .scalars()
        .all()
    )
    if not rows and not receipts:
        return None
    if (
        len(rows) > 1000
        or len(rows) != len(receipts)
        or project_row.access_policy.get("management_setup_approvals") != {}
    ):
        reject("missing_evidence")
    by_id = {
        receipt["evidence"]["reference"]["id"]: receipt["evidence"]["reference"]
        for receipt in receipts
    }
    if len(by_id) != len(rows):
        reject("missing_evidence")
    result = []
    for row in rows:
        value = by_id.get(str(row.id))
        if value is None:
            reject("missing_evidence")
        ref = ReferenceRead.model_validate(value)
        if (
            ref.asset_id != row.asset_key
            or ref.class_id != row.class_id
            or ref.validity_scope != row.validity_scope
            or ref.approval != "proposed"
            or row.approved
            or row.crop is not None
            or row.embedding_model is not None
            or json.loads(row.evidence_provenance) != value
        ):
            reject("missing_evidence")
        result.append(ref)
    return result
