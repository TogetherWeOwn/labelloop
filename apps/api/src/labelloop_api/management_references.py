"""Receipt-backed reference lifecycle, not pack publication or retrieval."""

import hashlib
import json
from uuid import uuid4

from sqlalchemy import Connection, func, insert, select, update

from .management_contracts import (
    ManagementError,
    ReferenceAccept,
    ReferenceEnroll,
    ReferenceWithdraw,
)
from .management_details import ReferenceRead
from .management_grouping import load_grouping
from .management_ports import ManagementFailure
from .management_setup import read_setup_approvals
from .schema import (
    canonical_event,
    ontology_release,
    reference_example,
    source_asset,
    source_split_assignment,
)


def reject(code):
    raise ManagementFailure(ManagementError(detail=code))


def validate_reference_source(
    connection: Connection,
    project_row,
    payload: ReferenceEnroll,
    *,
    lock_sources=True,
    validation_cache=None,
):
    # A read snapshot may contain many approved references. Validate the shared
    # ontology/grouping once per pin, never once per reference or across snapshots.
    cache = validation_cache if validation_cache is not None else {}
    key = (payload.base_ontology_id, payload.assignment_version)
    if key not in cache:
        ontology = connection.execute(
            select(ontology_release)
            .where(ontology_release.c.project_id == project_row.id)
            .order_by(ontology_release.c.version.desc())
            .limit(1)
        ).one_or_none()
        if ontology is None or ontology.ontology_key != payload.base_ontology_id:
            reject("revision_conflict")
        if "management_class_catalog_draft" in project_row.access_policy:
            reject("missing_evidence")
        if (
            connection.execute(
                select(source_asset.c.id).where(
                    source_asset.c.project_id == project_row.id,
                    source_asset.c.asset_key == payload.asset_id,
                )
            ).first()
            is None
        ):
            reject("not_found")
        assets, by_asset, _, _ = load_grouping(
            connection, project_row, payload.assignment_version, lock_sources=lock_sources
        )
        hashes = {row.id: row.sha256 for row in assets}
        holdouts = (
            connection.execute(
                select(source_split_assignment.c.asset_id).where(
                    source_split_assignment.c.asset_id.in_(hashes),
                    source_split_assignment.c.split.in_(["val", "test"]),
                )
            )
            .scalars()
            .all()
        )
        cache[key] = (
            ontology,
            {row.asset_key: row for row in assets},
            by_asset,
            {hashes[identifier] for identifier in holdouts},
            {by_asset[identifier].group_id for identifier in holdouts},
        )
    ontology, assets_by_key, by_asset, holdout_hashes, holdout_groups = cache[key]
    if not {payload.class_id, *payload.confused_with} <= ontology.class_map.keys():
        reject("unsupported_classes")
    asset = assets_by_key.get(payload.asset_id)
    if asset is None:
        reject("not_found")
    # A new assignment never launders a prior holdout (or byte-identical copy).
    if (
        by_asset[asset.id].split != "train"
        or asset.sha256 in holdout_hashes
        or by_asset[asset.id].group_id in holdout_groups
    ):
        reject("missing_evidence")
    return asset


def enroll_reference(
    connection: Connection, project_row, payload: ReferenceEnroll
) -> ReferenceRead:
    asset = validate_reference_source(connection, project_row, payload)
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


def reference_receipts(connection: Connection, project_row, workspace_id, operation: str):
    return (
        connection.execute(
            select(canonical_event.c.payload)
            .where(
                canonical_event.c.aggregate_id == str(project_row.id),
                canonical_event.c.event_type == f"management.{operation}",
                canonical_event.c.actor_kind == "human",
                canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
            )
            .limit(1001)
        )
        .scalars()
        .all()
    )


def reference_context_sha256(project_row) -> str:
    policy = project_row.access_policy
    context = [
        project_row.scope,
        project_row.domain,
        {
            key: policy.get(key)
            for key in (
                "source_permitted_use",
                "annotation_guide",
                "management_scope_draft",
                "management_guide_draft",
                "management_source_policy_draft",
            )
        },
    ]
    return hashlib.sha256(
        json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def enrolled_payload(evidence: dict) -> ReferenceEnroll:
    return ReferenceEnroll.model_validate(
        {
            **{
                key: value
                for key, value in evidence["reference"].items()
                if key not in {"id", "approval"}
            },
            "expected_revision": 0,
            "base_ontology_id": evidence["ontology_id"],
            "assignment_version": evidence["assignment_version"],
        }
    )


def accept_reference(
    connection: Connection, project_row, workspace_id, payload: ReferenceAccept
) -> ReferenceRead:
    # Require matching enrollment evidence, not a browser-supplied replacement.
    references = read_references(connection, project_row, workspace_id) or []
    reference = next((item for item in references if item.id == payload.reference_id), None)
    if reference is None:
        reject("not_found")
    if reference.approval != "proposed":
        reject("revision_conflict")
    receipts = reference_receipts(connection, project_row, workspace_id, "enroll_reference")
    evidence = next(
        item["evidence"] for item in receipts if item["evidence"]["reference"]["id"] == reference.id
    )
    if (
        evidence["ontology_id"] != payload.base_ontology_id
        or evidence["assignment_version"] != payload.assignment_version
    ):
        reject("revision_conflict")
    validate_reference_source(connection, project_row, enrolled_payload(evidence))
    connection.execute(
        update(reference_example)
        .where(reference_example.c.id == reference.id)
        .values(approved=True)
    )
    return reference.model_copy(update={"approval": "admin_approved"})


def withdraw_reference(
    connection: Connection, project_row, workspace_id, payload: ReferenceWithdraw
) -> ReferenceRead:
    # Retiring stale evidence needs intact history, not current approval eligibility.
    # This private history loader is never used to serve an approved public read.
    history = _reference_history(connection, project_row, workspace_id)
    reference = next((ref for ref, _, _ in history if ref.id == payload.reference_id), None)
    if reference is None:
        reject("not_found")
    if reference.approval == "withdrawn":
        reject("revision_conflict")
    connection.execute(
        update(reference_example)
        .where(reference_example.c.id == reference.id)
        .values(approved=False)
    )
    return reference.model_copy(update={"approval": "withdrawn"})


def _reference_history(connection: Connection, project_row, workspace_id):
    rows = connection.execute(
        select(reference_example, source_asset.c.asset_key)
        .join(source_asset)
        .where(source_asset.c.project_id == project_row.id)
        .order_by(reference_example.c.id)
        .limit(1001)
    ).all()
    receipts = reference_receipts(connection, project_row, workspace_id, "enroll_reference")
    approvals = reference_receipts(connection, project_row, workspace_id, "accept_reference")
    withdrawals = reference_receipts(connection, project_row, workspace_id, "withdraw_reference")
    if not rows and not receipts and not approvals and not withdrawals:
        return []
    if (
        len(rows) > 1000
        or len(rows) != len(receipts)
        or project_row.access_policy.get("management_setup_approvals") is None
    ):
        reject("missing_evidence")
    by_id = {receipt["evidence"]["reference"]["id"]: receipt["evidence"] for receipt in receipts}
    approved = {
        receipt["evidence"]["reference"]["id"]: receipt["evidence"] for receipt in approvals
    }
    withdrawn = {
        receipt["evidence"]["reference"]["id"]: receipt["evidence"] for receipt in withdrawals
    }
    if (
        len(by_id) != len(rows)
        or len(approved) != len(approvals)
        or len(withdrawn) != len(withdrawals)
        or not approved.keys() <= by_id.keys()
        or not withdrawn.keys() <= by_id.keys()
    ):
        reject("missing_evidence")
    result = []
    for row in rows:
        evidence = by_id.get(str(row.id))
        if evidence is None:
            reject("missing_evidence")
        value = evidence["reference"]
        ref = ReferenceRead.model_validate(value)
        if (
            ref.asset_id != row.asset_key
            or ref.class_id != row.class_id
            or ref.validity_scope != row.validity_scope
            or ref.approval != "proposed"
            or row.approved != (ref.id in approved and ref.id not in withdrawn)
            or row.crop is not None
            or row.embedding_model is not None
            or json.loads(row.evidence_provenance) != value
        ):
            reject("missing_evidence")
        if ref.id in approved:
            approval = approved[ref.id]
            accepted = ref.model_copy(update={"approval": "admin_approved"})
            if (
                approval["reference"] != accepted.model_dump(mode="json")
                or approval["ontology_id"] != evidence["ontology_id"]
                or approval["assignment_version"] != evidence["assignment_version"]
            ):
                reject("missing_evidence")
            ref = accepted
        if ref.id in withdrawn:
            ref = ref.model_copy(update={"approval": "withdrawn"})
            if withdrawn[ref.id]["reference"] != ref.model_dump(mode="json"):
                reject("missing_evidence")
        result.append((ref, evidence, approved.get(ref.id)))
    return result


def read_references(
    connection: Connection, project_row, workspace_id
) -> list[ReferenceRead] | None:
    read_setup_approvals(connection, project_row, workspace_id)
    history = _reference_history(connection, project_row, workspace_id)
    validation_cache = {}
    result = []
    for ref, evidence, approval in history:
        if ref.approval == "admin_approved":
            if approval["context_sha256"] != reference_context_sha256(project_row):
                reject("missing_evidence")
            # Only withdrawn evidence can bypass current eligibility, and it is
            # explicitly historical, never usable as an approved reference.
            validate_reference_source(
                connection,
                project_row,
                enrolled_payload(evidence),
                lock_sources=False,
                validation_cache=validation_cache,
            )
        result.append(ref)
    return result or None
