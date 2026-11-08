"""Trusted teacher provenance port, not model-asserted lineage or authorization.

The annotation service must append every contributing teacher with the revision
in its outer transaction. Later human acceptance does not erase that influence.
This does not infer missing historical provenance or implement cross-project imports.
"""

import sqlalchemy as sa

from labelloop_api import model_schema, qualification_schema
from labelloop_api.qualification_store import lock_project
from labelloop_api.schema import annotation_revision, source_asset
from labelloop_core.qualification_data import require_count, require_name


def record_generated_label_origin(
    connection: sa.Connection,
    *,
    project_key: str,
    asset_key: str,
    revision: int,
    teacher_model_key: str,
    actor_id: str,
) -> None:
    """Record one teacher of the exact persisted revision, including negatives.

    The service derives teacher identity from its fenced inference job, never a
    model response or request body. Callers retain the project lock through commit.
    Multiple teachers are retained; duplicate/substituted links cannot replace one.
    """
    for value in (actor_id, asset_key, teacher_model_key):
        require_name(value)
    require_count(revision, minimum=1)
    with connection.begin_nested():
        project_id = lock_project(connection, project_key)
        asset_id = connection.execute(
            sa.select(source_asset.c.id)
            .join(annotation_revision)
            .where(
                source_asset.c.project_id == project_id,
                source_asset.c.asset_key == asset_key,
                annotation_revision.c.revision == revision,
            )
        ).scalar_one()
        teacher_id = connection.execute(
            sa.select(model_schema.model_release.c.id).where(
                model_schema.model_release.c.project_id == project_id,
                model_schema.model_release.c.release_key == teacher_model_key,
            )
        ).scalar_one()
        connection.execute(
            sa.insert(qualification_schema.generated_label_origin).values(
                project_id=project_id,
                asset_id=asset_id,
                revision=revision,
                teacher_model_id=teacher_id,
                actor_id=actor_id,
            )
        )


def label_teacher_runs(connection: sa.Connection, project_id: object):
    """Load all recorded influence, preserving exact revision and project checks."""
    origin = qualification_schema.generated_label_origin
    teacher = model_schema.model_release
    rows = connection.execute(
        sa.select(
            origin.c.project_id,
            source_asset.c.project_id.label("source_project"),
            teacher.c.project_id.label("teacher_project"),
            source_asset.c.asset_key,
            origin.c.revision,
            teacher.c.training_run_id,
        )
        .select_from(
            origin.join(source_asset, origin.c.asset_id == source_asset.c.id).join(
                teacher, origin.c.teacher_model_id == teacher.c.id
            )
        )
        .where(sa.or_(origin.c.project_id == project_id, source_asset.c.project_id == project_id))
    ).mappings()
    links: dict[str, list[tuple[int, object]]] = {}
    for row in rows:
        if (row["project_id"], row["source_project"], row["teacher_project"]) != (
            project_id,
            project_id,
            project_id,
        ):
            raise ValueError("generated-label provenance cannot cross project boundaries")
        links.setdefault(row["asset_key"], []).append((row["revision"], row["training_run_id"]))
    return links
