"""Attributed approval of draft intent; no publication or qualification authority."""

import hashlib
import json

from sqlalchemy import Connection, func, select

from .management_contracts import ManagementError
from .management_ontology import read_catalog_publication
from .management_ports import ManagementFailure
from .schema import canonical_event

INVALIDATING_OPERATIONS = (
    "save_scope_draft",
    "save_guide_draft",
    "save_class_catalog_draft",
    "publish_class_catalog",
    "save_source_policy_draft",
    "enroll_reference",
    "accept_reference",
    "withdraw_reference",
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


def read_setup_approvals(connection: Connection, project_row, workspace_id) -> list[str]:
    stored = project_row.access_policy.get("management_setup_approvals", {})
    if not isinstance(stored, dict) or not stored.keys() <= {"scope", "guide", "classes"}:
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
            else project_row.access_policy.get(f"management_{step}_draft")
        )
        if (
            not isinstance(draft, dict)
            or receipt is None
            or invalidation is None
            or receipt.expected_revision <= invalidation
            or not isinstance(value, dict)
            or value
            != {
                "draft_sha256": draft_sha256(draft),
                "receipt_revision": receipt.expected_revision + 1,
            }
            or receipt.payload["evidence"]["draft_sha256"] != value["draft_sha256"]
        ):
            raise ManagementFailure(ManagementError(detail="missing_evidence"))
        approved.append(step)
    return approved
