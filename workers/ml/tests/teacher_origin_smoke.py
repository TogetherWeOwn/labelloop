"""Synthetic teacher-origin recovery and failure controls on Actions PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa

from labelloop_api import model_schema, qualification_schema
from labelloop_api.generated_label_store import label_teacher_runs, record_generated_label_origin
from labelloop_api.qualification_store import lock_project
from labelloop_api.schema import annotation_revision, project, source_asset


def exercise_teacher_origins(engine, request):
    table = qualification_schema.generated_label_origin
    asset_key = request.project_id + "-synthetic-ordinary-positive-teacher-clean"
    args = dict(
        project_key=request.project_id, asset_key=asset_key, revision=1, actor_id="fixture-worker"
    )
    with engine.begin() as connection:
        project_id = lock_project(connection, request.project_id)
        asset_id = connection.execute(
            sa.select(source_asset.c.id).where(source_asset.c.asset_key == asset_key)
        ).scalar_one()
        row = connection.execute(
            sa.select(
                model_schema.model_release.c.release_key,
                model_schema.model_release.c.training_run_id,
            )
            .join(table, table.c.teacher_model_id == model_schema.model_release.c.id)
            .where(table.c.asset_id == asset_id, table.c.revision == 1)
            .order_by(model_schema.model_release.c.release_key)
            .limit(1)
        ).one()
        args["teacher_model_key"] = row.release_key
        assert (1, row.training_run_id) in label_teacher_runs(connection, project_id)[asset_key]
        before = connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
        with pytest.raises(sa.exc.IntegrityError):
            record_generated_label_origin(connection, **args)
        for changed in (
            {"asset_key": "missing"},
            {"revision": 999},
            {"teacher_model_key": "missing"},
        ):
            with pytest.raises(sa.exc.NoResultFound):
                record_generated_label_origin(connection, **(args | changed))

        # Cross-project source and teacher substitutions never append a link.
        with connection.begin_nested() as transaction:
            foreign_key = request.project_id + "-teacher-foreign"
            foreign_id = connection.execute(
                sa.insert(project).values(
                    project_key=foreign_key,
                    workspace_id="00000000-0000-0000-0000-000000000001",
                    task="detect",
                    scope="synthetic teacher scope control",
                    domain="fixture",
                )
            ).inserted_primary_key[0]
            source = (
                connection.execute(sa.select(source_asset).where(source_asset.c.id == asset_id))
                .mappings()
                .one()
            )
            foreign_asset = asset_key + "-foreign"
            source_id = connection.execute(
                sa.insert(source_asset).values(
                    **{
                        key: value
                        for key, value in source.items()
                        if key not in {"id", "created_at", "project_id", "asset_key"}
                    },
                    project_id=foreign_id,
                    asset_key=foreign_asset,
                )
            ).inserted_primary_key[0]
            original = (
                connection.execute(
                    sa.select(annotation_revision).where(
                        annotation_revision.c.asset_id == asset_id,
                        annotation_revision.c.revision == 1,
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                sa.insert(annotation_revision).values(
                    **{
                        key: value
                        for key, value in original.items()
                        if key not in {"id", "created_at", "asset_id"}
                    },
                    asset_id=source_id,
                )
            )
            with pytest.raises(sa.exc.NoResultFound):
                record_generated_label_origin(connection, **(args | {"asset_key": foreign_asset}))
            with pytest.raises(sa.exc.NoResultFound):
                record_generated_label_origin(
                    connection, **(args | {"project_key": foreign_key, "asset_key": foreign_asset})
                )
            transaction.rollback()

        # Future revision provenance cannot leak into revision-one training sets.
        original = (
            connection.execute(
                sa.select(annotation_revision).where(
                    annotation_revision.c.asset_id == asset_id, annotation_revision.c.revision == 1
                )
            )
            .mappings()
            .one()
        )
        connection.execute(
            sa.insert(annotation_revision).values(
                **{
                    key: value
                    for key, value in original.items()
                    if key not in {"id", "created_at", "revision", "parent_revision"}
                },
                revision=2,
                parent_revision=1,
            )
        )

        def fail_origin(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("INSERT INTO qualification_generated_label_origin"):
                raise RuntimeError("fixture teacher append failure")

        sa.event.listen(connection, "before_cursor_execute", fail_origin)
        try:
            with pytest.raises(RuntimeError, match="teacher append failure"):
                record_generated_label_origin(connection, **(args | {"revision": 2}))
        finally:
            sa.event.remove(connection, "before_cursor_execute", fail_origin)
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one() == before
        )

    def compete(actor):
        try:
            with engine.begin() as connection:
                record_generated_label_origin(
                    connection, **(args | {"revision": 2, "actor_id": actor})
                )
            return True
        except sa.exc.IntegrityError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(compete, ("fixture-worker-a", "fixture-worker-b"))) == 1
    with engine.begin() as connection:
        project_id = lock_project(connection, request.project_id)
        links = label_teacher_runs(connection, project_id)[asset_key]
        assert (1, row.training_run_id) in links and (2, row.training_run_id) in links
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
            == before + 1
        )
        for statement in (
            sa.update(table).values(actor_id="replacement"),
            sa.delete(table),
            sa.text("TRUNCATE qualification_generated_label_origin"),
        ):
            with connection.begin_nested() as transaction:
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.execute(statement)
                transaction.rollback()
    return {
        "scope": "synthetic teacher provenance, not inference execution proof",
        "reconnect_recovered": True,
        "race_winners": 1,
        "failed_append_unchanged": True,
        "foreign_source_and_teacher_rejected": True,
    }
