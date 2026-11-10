"""Attributed approval of draft intent; no publication or qualification authority."""

import hashlib
import json

from sqlalchemy import Connection, func, select

from .management_contracts import ManagementError, Resource, SetupAnchorPack, SetupStepApprove
from .management_ontology import read_catalog_publication
from .management_ports import ManagementFailure
from .schema import canonical_event

INVALIDATING_OPERATIONS = (
    "save_scope_draft",
    "save_guide_draft",
    "save_class_catalog_draft",
    "publish_class_catalog",
    "save_source_policy_draft",
    "resolve_source_pair",
    "discover_initial_source_pairs",
    "publish_initial_grouping",
    "enroll_reference",
    "accept_reference",
    "revalidate_reference",
    "withdraw_reference",
    "publish_anchor_pack",
)


def draft_sha256(draft: dict) -> str:
    return hashlib.sha256(
        json.dumps(draft, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def approved_setup_summary_sha256(project_row, approvals, pins, versions) -> str | None:
    """Pin a summary already validated in one canonical read snapshot, not a release.

    The caller verifies draft/publication and approval receipts, current anchor
    evidence and materialized grouping before supplying these facts. No approval
    or eligibility can be established from this digest alone.
    """
    if set(approvals) != {"scope", "guide", "classes", "sources", "evidence"}:
        return None
    stored = project_row.access_policy["management_setup_approvals"]
    if (
        set(pins) != {"scope", "guide", "classes", "sources"}
        or not {"ontology", "ontology_id", "assignment"} <= versions.keys()
        or any(stored[step]["draft_sha256"] != digest for step, digest in pins.items())
    ):
        raise ManagementFailure(ManagementError(detail="missing_evidence"))
    return draft_sha256(
        {
            "schema": "approved-setup-summary-v1",
            "project_id": str(project_row.id),
            "workspace_id": str(project_row.workspace_id),
            "approvals": stored,
            "versions": versions,
        }
    )


def published_catalog_content(connection: Connection, project_row, workspace_id) -> dict | None:
    receipt = read_catalog_publication(connection, project_row, workspace_id)
    if (
        receipt is None
        or project_row.access_policy.get("management_class_catalog_draft") is not None
    ):
        return None
    pending = connection.execute(
        select(canonical_event.c.id)
        .where(
            canonical_event.c.aggregate_id == str(project_row.id),
            canonical_event.c.actor_kind == "human",
            canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
            canonical_event.c.event_type == "management.save_class_catalog_draft",
            canonical_event.c.expected_revision >= receipt.expected_revision,
        )
        .limit(1)
    ).first()
    return receipt.payload["evidence"]["class_catalog"] if pending is None else None


def evidence_pack_digest(connection, project_row, workspace_id, pin, *, lock_sources=False):
    from .management_reference_pack import pack_snapshot, read_current_anchor_pack

    try:
        pin = SetupAnchorPack.model_validate(pin)
        pack = read_current_anchor_pack(
            connection,
            project_row,
            workspace_id,
            pin.pack_id,
            pin.version,
            lock_sources=lock_sources,
        )
        if (
            pack.scope.ontology.ontology_id != pin.ontology_id
            or pack.scope.assignment.assignment_version != pin.assignment_version
        ):
            raise ValueError("pack scope mismatch")
        return pack_snapshot(pack)[1]
    except (TypeError, ValueError):
        raise ManagementFailure(ManagementError(detail="missing_evidence")) from None


def verify_setup_receipt(receipt, project_row, workspace_id, step) -> SetupStepApprove:
    try:
        payload = receipt.payload
        approval = SetupStepApprove.model_validate(
            {**payload["evidence"], "expected_revision": receipt.expected_revision}
        )
        result = Resource.model_validate(payload["result"])
        if (
            str(project_row.workspace_id) != str(workspace_id)
            or receipt.aggregate_id != str(project_row.id)
            or receipt.event_type != "management.approve_setup_step"
            or receipt.actor_kind != "human"
            or not isinstance(receipt.actor_id, str)
            or not receipt.actor_id.strip()
            or payload["workspace_id"] != str(workspace_id)
            or type(receipt.expected_revision) is not int
            or receipt.expected_revision < 1
            or type(payload["aggregate_revision"]) is not int
            or payload["aggregate_revision"] != receipt.expected_revision + 1
            or result.id != str(project_row.id)
            or result.project_id != str(project_row.id)
            or result.kind != "project"
            or result.state != "draft"
            or result.revision != payload["aggregate_revision"]
            or approval.step != step
        ):
            raise ValueError("inconsistent approval receipt")
        return approval
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ManagementFailure(ManagementError(detail="missing_evidence")) from None


def read_setup_approvals(
    connection: Connection, project_row, workspace_id, *, lock_sources=False
) -> list[str]:
    stored = project_row.access_policy.get("management_setup_approvals", {})
    if not isinstance(stored, dict) or not stored.keys() <= {
        "scope",
        "guide",
        "classes",
        "sources",
        "evidence",
    }:
        raise ManagementFailure(ManagementError(detail="missing_evidence"))
    if not stored:
        return []
    scope = (
        canonical_event.c.aggregate_id == str(project_row.id),
        canonical_event.c.actor_kind == "human",
        canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
    )
    invalidation = connection.execute(
        select(func.max(canonical_event.c.expected_revision)).where(
            *scope,
            canonical_event.c.event_type.in_(
                [f"management.{operation}" for operation in INVALIDATING_OPERATIONS]
            ),
        )
    ).scalar_one()
    approved = []
    for step, value in sorted(stored.items()):
        receipts = connection.execute(
            select(canonical_event)
            .where(
                *scope,
                canonical_event.c.event_type == "management.approve_setup_step",
                canonical_event.c.payload["evidence"]["step"].as_string() == step,
            )
            .order_by(canonical_event.c.expected_revision.desc())
            .limit(2)
        ).all()
        if not receipts or (
            len(receipts) == 2 and receipts[0].expected_revision == receipts[1].expected_revision
        ):
            raise ManagementFailure(ManagementError(detail="missing_evidence"))
        receipt = receipts[0]
        approval = verify_setup_receipt(receipt, project_row, workspace_id, step)
        draft = (
            published_catalog_content(connection, project_row, workspace_id)
            if step == "classes"
            else project_row.access_policy.get(
                "management_source_policy_draft"
                if step == "sources"
                else f"management_{step}_draft"
            )
        )
        digest = draft_sha256(draft) if isinstance(draft, dict) else None
        if step == "evidence":
            digest = evidence_pack_digest(
                connection,
                project_row,
                workspace_id,
                approval.anchor_pack,
                lock_sources=lock_sources,
            )
        if (
            digest is None
            or invalidation is None
            or receipt.expected_revision <= invalidation
            or not isinstance(value, dict)
            or value
            != {
                "draft_sha256": digest,
                "receipt_revision": receipt.expected_revision + 1,
            }
            or receipt.payload["evidence"]["draft_sha256"] != value["draft_sha256"]
        ):
            raise ManagementFailure(ManagementError(detail="missing_evidence"))
        approved.append(step)
    return approved
