"""Derive evaluation groups from complete frozen canonical source membership.

This proves recorded grouping, not label truth, measured scores or permission to
assign an evaluation role. Only a trusted service selects the role and release;
model/HTTP claims cannot supply those selectors. Retain the outer transaction and
canonical group locks through the consuming decision.
"""

import sqlalchemy as sa

from labelloop_api.final_test_store import require_development_uses
from labelloop_api.qualification_store import lock_project, release_membership
from labelloop_api.schema import annotation_revision, canonical_dataset_release, source_asset
from labelloop_api.source_group_store import current_release_groups
from labelloop_core.evaluation_lifecycle import EvaluationSet, SetRole
from labelloop_core.qualification_data import require_name


def load_evaluation_set(
    connection: sa.Connection,
    *,
    project_key: str,
    dataset_key: str,
    role: SetRole,
) -> EvaluationSet:
    """Load every pinned revision; never shrink a holdout to successful joins.

    The source assignment version is the frozen release's, not a caller-selected
    version. Current relations include exact duplicates and unselected intermediate
    sources. Split labels alone do not establish development/calibration/final roles.
    A known final-test reservation still overrides a supplied development role.
    """
    require_name(project_key)
    require_name(dataset_key)
    if role not in {"development", "calibration", "locked_final_test"}:
        raise ValueError("evaluation group loading requires an evaluation role")
    project_id = lock_project(connection, project_key)
    release = (
        connection.execute(
            sa.select(canonical_dataset_release).where(
                canonical_dataset_release.c.project_id == project_id,
                canonical_dataset_release.c.release_key == dataset_key,
                canonical_dataset_release.c.frozen,
            )
        )
        .mappings()
        .one()
    )
    membership = release_membership(release["sample_revisions"])
    if not membership or set(membership) & set(release["excluded_asset_ids"]):
        raise ValueError("evaluation groups require complete non-excluded frozen membership")
    rows = connection.execute(
        sa.select(source_asset.c.asset_key, annotation_revision.c.revision)
        .join(annotation_revision)
        .where(
            source_asset.c.project_id == project_id,
            sa.tuple_(source_asset.c.asset_key, annotation_revision.c.revision).in_(
                list(membership.items())
            ),
        )
    ).all()
    if len(rows) != len(membership) or dict(rows) != membership:
        raise ValueError("evaluation groups have missing canonical source revisions")
    current = current_release_groups(
        connection,
        project_id=project_id,
        assignment_version=release["assignment_version"],
        asset_keys=frozenset(membership),
    )
    if set(current) != set(membership):
        raise ValueError("evaluation groups have incomplete current source membership")
    if role == "development":
        require_development_uses(
            connection, project_key=project_key, development_set_key=dataset_key
        )
    elif role == "calibration":
        require_development_uses(
            connection,
            project_key=project_key,
            development_set_key=dataset_key,
            calibration_set_key=dataset_key,
        )
    return EvaluationSet(dataset_key, project_key, tuple(sorted(set(current.values()))), role)
