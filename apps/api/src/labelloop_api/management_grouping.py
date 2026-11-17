"""Shared authoritative grouping checks for freeze and proposed reference enrollment.

Callers hold the project lock; all grouping writers must share that lock.
"""

from sqlalchemy import Connection, func, select

from .management_contracts import ManagementError
from .management_ports import ManagementFailure
from .schema import (
    borderline_review,
    canonical_event,
    similarity_relation,
    source_asset,
    source_split_assignment,
)


def reject(code):
    raise ManagementFailure(ManagementError(detail=code))


def current_assignment_version(connection: Connection, project_id, identities):
    """Current version by publication order, matching the lifecycle leaf.

    The latest ``source_grouping_published`` event (its per-project
    ``expected_revision`` sequence is monotonic) wins when its version covers
    every asset. Without such an event, fall back to per-asset latest
    ``created_at`` and require exactly one complete version; ties across
    versions are ambiguous, not current. Returns ``None`` when no complete
    current version exists.
    """
    if not identities:
        return None
    event = connection.execute(
        select(canonical_event.c.payload)
        .where(
            canonical_event.c.aggregate_id == str(project_id),
            canonical_event.c.event_type == "source_grouping_published",
        )
        .order_by(canonical_event.c.expected_revision.desc())
        .limit(1)
    ).one_or_none()
    if event is not None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        version = payload.get("assignment_version")
        if not isinstance(version, str) or not version:
            return None
        covered = connection.execute(
            select(func.count())
            .select_from(source_split_assignment)
            .where(
                source_split_assignment.c.asset_id.in_(identities),
                source_split_assignment.c.assignment_version == version,
            )
        ).scalar_one()
        return version if covered == len(identities) else None
    latest_times = (
        select(
            source_split_assignment.c.asset_id,
            func.max(source_split_assignment.c.created_at).label("latest_at"),
        )
        .where(source_split_assignment.c.asset_id.in_(identities))
        .group_by(source_split_assignment.c.asset_id)
        .subquery()
    )
    latest = (
        connection.execute(
            select(source_split_assignment.c.assignment_version).join(
                latest_times,
                (source_split_assignment.c.asset_id == latest_times.c.asset_id)
                & (source_split_assignment.c.created_at == latest_times.c.latest_at),
            )
        )
        .scalars()
        .all()
    )
    if len(latest) != len(identities) or len(set(latest)) != 1:
        return None
    return latest[0]


def load_grouping(
    connection: Connection, project_row, assignment_version: str, *, lock_sources=True
):
    asset_query = (
        select(source_asset)
        .where(source_asset.c.project_id == project_row.id)
        .order_by(source_asset.c.id)
    )
    if lock_sources:
        asset_query = asset_query.with_for_update()
    assets = connection.execute(asset_query).all()
    identities = [row.id for row in assets]
    assignments = connection.execute(
        select(source_split_assignment).where(
            source_split_assignment.c.asset_id.in_(identities),
            source_split_assignment.c.assignment_version == assignment_version,
        )
    ).all()
    by_asset = {row.asset_id: row for row in assignments}
    if set(by_asset) != set(identities) or any(not row.locked for row in assignments):
        reject("missing_evidence")
    if len({(row.seed, row.algorithm) for row in assignments}) != 1:
        reject("missing_evidence")
    # One version must mean one policy, not independently chosen per-image rules.
    if any(row.thresholds != assignments[0].thresholds for row in assignments):
        reject("missing_evidence")
    group_splits = validate_group_membership(connection, project_row.id, assets, by_asset)
    pending = connection.execute(
        select(borderline_review.c.id)
        .where(
            (
                borderline_review.c.left_asset_id.in_(identities)
                | borderline_review.c.right_asset_id.in_(identities)
            ),
            borderline_review.c.review_state == "pending",
        )
        .limit(1)
    ).first()
    if pending is not None:
        reject("missing_evidence")
    # A caller cannot turn an obsolete assignment into current membership.
    # Version labels are opaque; publication order decides, never lexical order.
    if current_assignment_version(connection, project_row.id, identities) != assignment_version:
        reject("revision_conflict")
    return assets, by_asset, assignments, group_splits


def validate_group_membership(connection: Connection, project_id, assets, by_asset):
    """Check materialized constraints without asserting readiness or locking rows.

    Callers first require one complete assignment version and one policy. Pending
    suggestions remain separate: they block freeze, but not observation of intact
    groups while a reviewer decides them.
    """
    by_key = {row.asset_key: row for row in assets}
    identities = [row.id for row in assets]
    group_splits = {}
    hash_groups = {}
    for asset in assets:
        assignment = by_asset[asset.id]
        if group_splits.setdefault(assignment.group_id, assignment.split) != assignment.split:
            reject("missing_evidence")
        if hash_groups.setdefault(asset.sha256, assignment.group_id) != assignment.group_id:
            reject("missing_evidence")
    source_groups = {}
    ingest_events = connection.execute(
        select(canonical_event.c.payload).where(
            canonical_event.c.aggregate_id == str(project_id),
            canonical_event.c.event_type == "management.ingest_asset",
        )
    ).scalars()
    for event in ingest_events:
        asset_key = event["result"]["id"]
        if asset_key not in by_key:
            reject("missing_evidence")
        group = by_asset[by_key[asset_key].id].group_id
        declared = event["evidence"]["source_group"]
        if source_groups.setdefault(declared, group) != group:
            reject("missing_evidence")
    # Check edges even through excluded assets. Connected endpoints must already
    # be in one materialized group; this also enforces transitive constraints.
    relations = connection.execute(
        select(similarity_relation).where(
            similarity_relation.c.left_asset_id.in_(identities)
            | similarity_relation.c.right_asset_id.in_(identities)
        )
    ).all()
    for relation in relations:
        left = by_asset.get(relation.left_asset_id)
        right = by_asset.get(relation.right_asset_id)
        if left is None or right is None or left.group_id != right.group_id:
            reject("missing_evidence")
    return group_splits
