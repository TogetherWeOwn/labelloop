"""Attributed approval of draft intent; no publication or qualification authority."""

import hashlib
import json

from sqlalchemy import Connection, func, select

from .management_contracts import ManagementError, SetupAnchorPack
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
        receipt = connection.execute(
            select(canonical_event)
            .where(
                *scope,
                canonical_event.c.event_type == "management.approve_setup_step",
                canonical_event.c.payload["evidence"]["step"].as_string() == step,
            )
            .order_by(canonical_event.c.expected_revision.desc())
            .limit(1)
        ).one_or_none()
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
        if step == "evidence" and receipt is not None:
            digest = evidence_pack_digest(
                connection,
                project_row,
                workspace_id,
                receipt.payload["evidence"].get("anchor_pack"),
                lock_sources=lock_sources,
            )
        if (
            digest is None
            or receipt is None
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
