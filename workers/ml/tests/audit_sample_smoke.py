"""Synthetic PostgreSQL audit-population persistence, not annotation quality."""

from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa

from labelloop_api import qualification_schema
from labelloop_api.annotation_audit_store import lock_project
from labelloop_api.audit_sample_store import freeze_audit_sample, load_audit_sample
from labelloop_api.schema import (
    annotation_revision,
    canonical_dataset_release,
    source_asset,
    source_split_assignment,
)


def exercise_audit_sampling(engine, request):
    arguments = dict(
        project_key=request.project_id,
        release_key=request.dataset_release_id,
        sample_key="canonical-ordinary-audit",
        actor_id="fixture-sampling-admin",
        pipeline_id="fixture-pipeline-provenance-not-yet-qualified",
        size=2,
        seed=9,
    )
    with engine.begin() as connection:
        project_id = lock_project(connection, request.project_id)
        # Canonical fixture sources initially have no group rows: never invent them
        # in the loader or silently reduce the population to rows that happen to join.
        with pytest.raises(ValueError, match="missing canonical revisions or pinned groups"):
            freeze_audit_sample(connection, **arguments)
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(qualification_schema.audit_sample)
            ).scalar_one()
            == 0
        )
        assets = connection.execute(
            sa.select(source_asset.c.id, source_asset.c.asset_key).where(
                source_asset.c.project_id == project_id,
                source_asset.c.asset_key.in_(
                    [
                        request.project_id + "-synthetic-ordinary-positive",
                        request.project_id + "-synthetic-ordinary-negative",
                    ]
                ),
            )
        ).all()
        assert len(assets) == 2
        for asset in assets:
            for version, group in (
                (request.split_assignment_version, "one-shared-group"),
                ("other-assignment", "foreign-version-" + asset.asset_key),
            ):
                connection.execute(
                    sa.insert(source_split_assignment).values(
                        assignment_version=version,
                        asset_id=asset.id,
                        group_id=group,
                        split="train",
                        seed=9,
                        algorithm="explicit-synthetic",
                        thresholds={},
                        reason="fixture",
                        locked=True,
                    )
                )
        with pytest.raises(sa.exc.NoResultFound):
            freeze_audit_sample(connection, **(arguments | {"project_key": "foreign-project"}))
        with pytest.raises(ValueError, match="both accepted positives and negatives"):
            freeze_audit_sample(connection, **(arguments | {"size": 1}))
        frozen = freeze_audit_sample(connection, **arguments)
        assert frozen.independent_groups == 1
        assert {bool(item.revision.objects) for item in frozen.sample.candidates} == {True, False}
        assert {item.revision.revision for item in frozen.sample.candidates} == {1}
        # Appending a later revision cannot change a frozen release/sample.
        for asset in assets:
            current = (
                connection.execute(
                    sa.select(annotation_revision).where(
                        annotation_revision.c.asset_id == asset.id,
                        annotation_revision.c.revision == 1,
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                sa.insert(annotation_revision).values(
                    **(
                        {
                            key: value
                            for key, value in current.items()
                            if key not in {"id", "created_at"}
                        }
                        | {"revision": 2, "parent_revision": 1, "actor_id": "later-reviewer"}
                    )
                )
            )
    with engine.begin() as connection:
        recovered = load_audit_sample(
            connection, project_id=project_id, sample_key=arguments["sample_key"]
        )
        assert recovered == frozen
        with pytest.raises(sa.exc.NoResultFound):
            load_audit_sample(
                connection,
                project_id="00000000-0000-0000-0000-000000000000",
                sample_key=arguments["sample_key"],
            )
        for statement in (
            sa.update(qualification_schema.audit_sample).values(actor_id="replacement"),
            sa.delete(qualification_schema.audit_sample),
            sa.text("TRUNCATE qualification_audit_sample"),
        ):
            with connection.begin_nested() as transaction:
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.execute(statement)
                transaction.rollback()
        release = (
            connection.execute(
                sa.select(canonical_dataset_release).where(
                    canonical_dataset_release.c.release_key == request.dataset_release_id
                )
            )
            .mappings()
            .one()
        )
        connection.execute(
            sa.insert(canonical_dataset_release).values(
                **(
                    {
                        key: value
                        for key, value in release.items()
                        if key not in {"id", "created_at"}
                    }
                    | {
                        "release_key": request.dataset_release_id + "-missing-canonical-member",
                        "sample_revisions": release["sample_revisions"] | {"foreign-asset": 1},
                    }
                )
            )
        )
        with pytest.raises(ValueError, match="missing canonical revisions or pinned groups"):
            freeze_audit_sample(
                connection,
                **(
                    arguments
                    | {
                        "release_key": request.dataset_release_id + "-missing-canonical-member",
                        "sample_key": "must-not-shrink-population",
                    }
                ),
            )

    # Same identity under racing requests has exactly one durable winner. Both
    # transactions retain the canonical project lock through their outer commit.
    def race():
        try:
            with engine.begin() as connection:
                freeze_audit_sample(connection, **(arguments | {"sample_key": "racing-sample"}))
            return "saved"
        except sa.exc.IntegrityError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: race(), range(2))) == ["duplicate", "saved"]

    # Failed inserts cannot leave any partial sample snapshot.
    def fail_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO qualification_audit_sample"):
            raise RuntimeError("injected audit sample write failure")

    with engine.begin() as connection:
        sa.event.listen(connection, "before_cursor_execute", fail_insert)
        try:
            with pytest.raises(RuntimeError, match="injected audit sample"):
                freeze_audit_sample(connection, **(arguments | {"sample_key": "failed-sample"}))
        finally:
            sa.event.remove(connection, "before_cursor_execute", fail_insert)
        assert (
            connection.execute(
                sa.select(sa.func.count())
                .select_from(qualification_schema.audit_sample)
                .where(qualification_schema.audit_sample.c.sample_key == "failed-sample")
            ).scalar_one()
            == 0
        )
    return {
        "scope": "synthetic sampling/persistence only, not independent truth or HTTP acceptance",
        "sample_id": frozen.sample.sample_id,
        "population_sha256": frozen.population_sha256,
        "group_ids": frozen.group_ids,
        "restart_exact": True,
        "correlated_sources_counted_once": True,
        "racing_identity_has_one_winner": True,
        "missing_population_and_failed_write_leave_no_sample": True,
    }
