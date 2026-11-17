"""Persist a bounded human pair decision without rewriting assignment history.

The caller holds the project lock. Related pairs crossing historical groups need
regrouping/quarantine support; this adapter refuses rather than inventing it.
"""

import json
import math
from uuid import UUID

from sqlalchemy import insert, select, update

from .management_contracts import ManagementError
from .management_ports import ManagementFailure
from .schema import borderline_review, similarity_relation, source_asset, source_split_assignment


def reject(code):
    raise ManagementFailure(ManagementError(detail=code))


def resolve_pair(connection, project_row, payload):
    try:
        identifier = UUID(payload.pair_id)
    except ValueError:
        reject("not_found")
    owned = select(source_asset.c.id).where(source_asset.c.project_id == project_row.id)
    pair = connection.execute(
        select(borderline_review)
        .where(
            borderline_review.c.id == identifier,
            borderline_review.c.left_asset_id.in_(owned),
            borderline_review.c.right_asset_id.in_(owned),
        )
        .with_for_update()
    ).one_or_none()
    if pair is None:
        reject("not_found")
    if pair.review_state != "pending":
        reject("revision_conflict")
    if (
        pair.left_asset_id == pair.right_asset_id
        or pair.metric not in {"phash", "embedding_cosine"}
        or not pair.algorithm_version
    ):
        reject("missing_evidence")
    try:
        value = float(pair.value)
    except (TypeError, ValueError, OverflowError):
        reject("missing_evidence")
    if not math.isfinite(value) or (
        not (0 <= value <= 64 and value.is_integer())
        if pair.metric == "phash"
        else not -1 <= value <= 1
    ):
        reject("missing_evidence")
    evidence = {
        "pair_id": str(pair.id),
        "left_asset_id": str(pair.left_asset_id),
        "right_asset_id": str(pair.right_asset_id),
        "metric": pair.metric,
        "value": pair.value,
        "algorithm_version": pair.algorithm_version,
        "decision": payload.decision,
        "reason": payload.reason,
    }
    if payload.decision == "related":
        # Check every version, not just current splits: old holdouts remain
        # protected. Partial versions and mismatched policies are not evidence.
        assignments = connection.execute(
            select(source_split_assignment)
            .where(
                source_split_assignment.c.asset_id.in_([pair.left_asset_id, pair.right_asset_id])
            )
            .limit(10001)
        ).all()
        if len(assignments) > 10000:
            reject("management_unavailable")
        versions = {}
        for row in assignments:
            versions.setdefault(row.assignment_version, []).append(row)
        for rows in versions.values():
            if (
                len(rows) != 2
                or len({row.asset_id for row in rows}) != 2
                or rows[0].group_id != rows[1].group_id
                or rows[0].split != rows[1].split
                or any(
                    (row.seed, row.algorithm, row.thresholds)
                    != (rows[0].seed, rows[0].algorithm, rows[0].thresholds)
                    for row in rows
                )
            ):
                reject("missing_evidence")
        connection.execute(
            insert(similarity_relation).values(
                left_asset_id=pair.left_asset_id,
                right_asset_id=pair.right_asset_id,
                reason=pair.metric,
                evidence=json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                algorithm_version=pair.algorithm_version,
            )
        )
    # Unrelated dismisses this suggestion only. It never deletes an automatic
    # edge or separates a conservative/transitive group.
    connection.execute(
        update(borderline_review)
        .where(borderline_review.c.id == pair.id)
        .values(review_state=payload.decision)
    )
    return evidence
