"""Check frozen assignments against current canonical transitive relationships.

This validates recorded evidence, not similarity detection or independent truth.
Callers hold the project lock; table read locks fence even non-service relation
writers until the surrounding transaction commits. Never rewrite pinned groups.
"""

import sqlalchemy as sa

from labelloop_api.schema import similarity_relation, source_asset, source_split_assignment
from labelloop_core.qualification_data import require_name


def checked_group_membership(
    assets: dict[str, str],
    assignments: dict[str, tuple[str, str]],
    relations: tuple[tuple[str, str], ...],
    required: frozenset[str],
) -> dict[str, str]:
    """Include unselected intermediates, exact duplicates and pinned group edges."""
    if not required or not required <= assets.keys() or not required <= assignments.keys():
        raise ValueError("current groups require complete pinned source membership")
    adjacent: dict[str, set[str]] = {key: set() for key in assets}

    def link(left: str, right: str) -> None:
        adjacent.setdefault(left, set()).add(right)
        adjacent.setdefault(right, set()).add(left)

    hashes: dict[str, str] = {}
    groups: dict[str, str] = {}
    for key, digest in assets.items():
        link(key, hashes.setdefault(digest, key))
    for key, (group, split) in assignments.items():
        require_name(group)
        if key not in assets or split not in {"train", "val", "test"}:
            raise ValueError("invalid canonical source assignment")
        link(key, groups.setdefault(group, key))
    for left, right in relations:
        link(left, right)
    visited: set[str] = set()
    for key in sorted(required):
        if key in visited:
            continue
        pending = [key]
        component: set[str] = set()
        while pending:
            member = pending.pop()
            if member in component:
                continue
            component.add(member)
            pending.extend(adjacent[member] - component)
        if not component <= assets.keys() or not component <= assignments.keys():
            raise ValueError("current source relationship reaches foreign or unassigned assets")
        if len({assignments[member][1] for member in component}) != 1:
            raise ValueError(
                "current source relationships cross pinned splits; quarantine required"
            )
        if len({assignments[member][0] for member in component}) != 1:
            raise ValueError(
                "current source relationships merge pinned groups; reassessment required"
            )
        visited.update(component)
    return {key: assignments[key][0] for key in sorted(required)}


def current_release_groups(
    connection: sa.Connection,
    *,
    project_id: object,
    assignment_version: str,
    asset_keys: frozenset[str],
) -> dict[str, str]:
    # SHARE conflicts with INSERT/UPDATE/DELETE, including newly inserted edges
    # and assets, but permits concurrent validators. Retained to outer commit.
    connection.execute(
        sa.text(
            "LOCK TABLE source_asset, source_split_assignment, similarity_relation IN SHARE MODE"
        )
    )
    assets = dict(
        connection.execute(
            sa.select(source_asset.c.asset_key, source_asset.c.sha256).where(
                source_asset.c.project_id == project_id
            )
        ).all()
    )
    assignments = {
        row.asset_key: (row.group_id, row.split)
        for row in connection.execute(
            sa.select(
                source_asset.c.asset_key,
                source_split_assignment.c.group_id,
                source_split_assignment.c.split,
            )
            .join(source_split_assignment)
            .where(
                source_asset.c.project_id == project_id,
                source_split_assignment.c.assignment_version == assignment_version,
            )
        )
    }
    left = source_asset.alias("left_source")
    right = source_asset.alias("right_source")
    relations = tuple(
        connection.execute(
            sa.select(left.c.asset_key, right.c.asset_key)
            .select_from(
                similarity_relation.join(
                    left, left.c.id == similarity_relation.c.left_asset_id
                ).join(right, right.c.id == similarity_relation.c.right_asset_id)
            )
            .where(sa.or_(left.c.project_id == project_id, right.c.project_id == project_id))
        ).tuples()
    )
    return checked_group_membership(assets, assignments, relations, asset_keys)
