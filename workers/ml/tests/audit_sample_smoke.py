"""Synthetic PostgreSQL audit-population persistence, not annotation quality."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
import sqlalchemy as sa

from labelloop_api import qualification_schema
from labelloop_api.annotation_audit_store import lock_project, record_annotation_audit
from labelloop_api.audit_sample_store import freeze_audit_sample, load_audit_sample
from labelloop_api.schema import (
    annotation_revision,
    canonical_dataset_release,
    similarity_relation,
    source_asset,
    source_split_assignment,
)
from labelloop_core.audit import AuditObservation


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
    observations = tuple(
        AuditObservation(
            item.asset_id, item.revision.revision, "fixture-independent-auditor", True, True, True
        )
        for item in frozen.sample.candidates
    )
    audit_args = dict(
        project_key=request.project_id,
        release_key=request.dataset_release_id,
        actor_id="fixture-independent-auditor",
        sample_key=frozen.sample.sample_id,
    )
    with engine.begin() as connection:
        record_annotation_audit(
            connection, **audit_args, audit_key="sample-partial", observations=observations[:1]
        )
        record_annotation_audit(
            connection, **audit_args, audit_key="sample-complete", observations=observations
        )
        for bad, message in (
            (replace(observations[0], asset_id="unselected"), "outside the frozen sample"),
            (replace(observations[0], revision=2), "stale source revision"),
            (replace(observations[0], reviewer_id="forged"), "authenticated principal"),
        ):
            with pytest.raises(ValueError, match=message):
                record_annotation_audit(
                    connection, **audit_args, audit_key="invalid-sample-result", observations=(bad,)
                )
        with pytest.raises(ValueError, match="unique assets"):
            record_annotation_audit(
                connection,
                **audit_args,
                audit_key="duplicate-observation",
                observations=(observations[0], observations[0]),
            )
        with pytest.raises(sa.exc.NoResultFound):
            record_annotation_audit(
                connection,
                **(audit_args | {"sample_key": "missing-sample"}),
                audit_key="unknown-sample",
                observations=observations,
            )
        # Even identical canonical membership is not the sample's original release.
        other_release = request.dataset_release_id + "-identical-membership"
        connection.execute(
            sa.insert(canonical_dataset_release).values(
                **(
                    {
                        key: value
                        for key, value in release.items()
                        if key not in {"id", "created_at"}
                    }
                    | {"release_key": other_release}
                )
            )
        )
        with pytest.raises(sa.exc.NoResultFound):
            record_annotation_audit(
                connection,
                **(audit_args | {"release_key": other_release}),
                audit_key="wrong-release-sample",
                observations=observations,
            )

    with engine.begin() as connection:
        results = {
            row["audit_key"]: row["record"]
            for row in connection.execute(
                sa.select(qualification_schema.annotation_audit)
            ).mappings()
        }
        assert set(results) == {"sample-partial", "sample-complete"}
        partial = results["sample-partial"]["sample"]["verdict"]
        assert not partial["passed"]
        assert partial["pending_asset_ids"] == [observations[1].asset_id]
        binding = results["sample-complete"]["sample"]
        assert binding["population_sha256"] == frozen.population_sha256
        assert binding["group_ids"] == frozen.group_ids
        assert binding["verdict"]["passed"]
        assert not binding["verdict"]["pending_asset_ids"]

    def race_audit():
        try:
            with engine.begin() as connection:
                record_annotation_audit(
                    connection,
                    **audit_args,
                    audit_key="racing-sample-result",
                    observations=observations,
                )
            return "saved"
        except sa.exc.IntegrityError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: race_audit(), range(2))) == ["duplicate", "saved"]

    def fail_audit(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO qualification_annotation_audit"):
            raise RuntimeError("injected bound audit write failure")

    with engine.begin() as connection:
        sa.event.listen(connection, "before_cursor_execute", fail_audit)
        try:
            with pytest.raises(RuntimeError, match="injected bound audit"):
                record_annotation_audit(
                    connection,
                    **audit_args,
                    audit_key="failed-bound-result",
                    observations=observations,
                )
        finally:
            sa.event.remove(connection, "before_cursor_execute", fail_audit)
        assert (
            connection.execute(
                sa.select(sa.func.count())
                .select_from(qualification_schema.annotation_audit)
                .where(qualification_schema.annotation_audit.c.audit_key == "failed-bound-result")
            ).scalar_one()
            == 0
        )
    exercise_current_groups(engine, request, arguments, project_id, frozen, observations)
    return {
        "scope": "synthetic sampling/persistence only, not independent truth or HTTP acceptance",
        "current_transitive_group_checks_and_writer_fence": True,
        "observations_bound_to_sample_and_release": True,
        "partial_observations_remain_pending": True,
        "bound_result_restart_race_and_failure_checked": True,
        "sample_id": frozen.sample.sample_id,
        "population_sha256": frozen.population_sha256,
        "group_ids": frozen.group_ids,
        "restart_exact": True,
        "correlated_sources_counted_once": True,
        "racing_identity_has_one_winner": True,
        "missing_population_and_failed_write_leave_no_sample": True,
    }


def exercise_current_groups(engine, request, arguments, project_id, frozen, observations):
    checked = dict(
        project_id=project_id, sample_key=frozen.sample.sample_id, require_current_groups=True
    )
    with engine.begin() as connection:
        lock_project(connection, request.project_id)
        assert load_audit_sample(connection, **checked) == frozen
    with engine.begin() as connection:
        assets = dict(
            connection.execute(
                sa.select(source_asset.c.asset_key, source_asset.c.id).where(
                    source_asset.c.project_id == project_id
                )
            ).all()
        )
        ordinary = assets[observations[0].asset_id]
        other = assets[observations[1].asset_id]
        # The database forbids rewriting a locked assignment; the negative
        # grouping control below must create new evidence, not bypass this guard.
        with connection.begin_nested() as transaction:
            with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                connection.execute(
                    sa.update(source_split_assignment)
                    .where(
                        source_split_assignment.c.asset_id == other,
                        source_split_assignment.c.assignment_version
                        == request.split_assignment_version,
                    )
                    .values(group_id="invented-independent-group")
                )
            transaction.rollback()
        assert load_audit_sample(connection, **checked) == frozen

        def add_source(key, digest, split=None, group="one-shared-group"):
            asset_id = connection.execute(
                sa.insert(source_asset).values(
                    asset_key=request.project_id + key,
                    project_id=project_id,
                    sha256=digest,
                    object_key=f"sha256/{digest[:2]}/{digest}",
                    width=32,
                    height=32,
                    exif_orientation=1,
                    source_uri="fixture://current-groups",
                )
            ).inserted_primary_key[0]
            if split is not None:
                connection.execute(
                    sa.insert(source_split_assignment).values(
                        assignment_version=request.split_assignment_version,
                        asset_id=asset_id,
                        group_id=group,
                        split=split,
                        seed=9,
                        algorithm="explicit-synthetic",
                        thresholds={},
                        reason="fixture current-relation check",
                        locked=True,
                    )
                )
            return asset_id

        def relate(left, right):
            connection.execute(
                sa.insert(similarity_relation).values(
                    left_asset_id=left,
                    right_asset_id=right,
                    reason="metadata",
                    evidence="synthetic relation",
                    algorithm_version="fixture-current-groups",
                )
            )

        # A newly discovered exact duplicate may carry a different pinned group.
        # Both fresh sampling and reuse must reject it without editing old rows.
        with connection.begin_nested() as transaction:
            digest = connection.execute(
                sa.select(source_asset.c.sha256).where(source_asset.c.id == ordinary)
            ).scalar_one()
            add_source("-current-duplicate", digest, "train", "invented-independent-group")
            for operation in ("reuse", "freeze"):
                with pytest.raises(ValueError, match="merge pinned groups"):
                    if operation == "reuse":
                        load_audit_sample(connection, **checked)
                    else:
                        freeze_audit_sample(
                            connection, **(arguments | {"sample_key": "invalid-current-groups"})
                        )
            transaction.rollback()
        assert load_audit_sample(connection, **checked) == frozen

        with connection.begin_nested() as transaction:
            bridge = add_source("-current-bridge", "b" * 64)
            # An unrelated unassigned source does not block a valid population.
            assert load_audit_sample(connection, **checked) == frozen
            relate(ordinary, bridge)
            with pytest.raises(ValueError, match="foreign or unassigned"):
                load_audit_sample(connection, **checked)
            transaction.rollback()
        with connection.begin_nested() as transaction:
            bridge = add_source("-current-bridge", "b" * 64, "train")
            heldout = add_source("-current-heldout", "c" * 64, "test", "separate-final-group")
            relate(ordinary, bridge)
            relate(bridge, heldout)
            with pytest.raises(ValueError, match="cross pinned splits"):
                load_audit_sample(connection, **checked)
            # Historical evidence remains readable and an adverse observation is
            # still appended, never discarded because new relationships surfaced.
            assert (
                load_audit_sample(connection, **(checked | {"require_current_groups": False}))
                == frozen
            )
            record_annotation_audit(
                connection,
                project_key=request.project_id,
                release_key=request.dataset_release_id,
                audit_key="adverse-after-new-relation",
                actor_id="fixture-independent-auditor",
                sample_key=frozen.sample.sample_id,
                observations=(replace(observations[0], labels_correct=False),),
            )
            assert connection.execute(
                sa.select(qualification_schema.annotation_audit.c.vetoed_revisions).where(
                    qualification_schema.annotation_audit.c.audit_key
                    == "adverse-after-new-relation"
                )
            ).scalar_one() == {observations[0].asset_id: observations[0].revision}
            transaction.rollback()

    # A raw edge writer that does not take the project lock cannot race a current
    # check. The database timeout bounds this negative control; no polling/sleep.
    edge = sa.insert(similarity_relation).values(
        left_asset_id=ordinary,
        right_asset_id=other,
        reason="metadata",
        evidence="synthetic writer fence",
        algorithm_version="fixture-current-groups",
    )
    with engine.begin() as reader:
        lock_project(reader, request.project_id)
        assert load_audit_sample(reader, **checked) == frozen
        with engine.begin() as writer:
            writer.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
            with writer.begin_nested() as transaction:
                with pytest.raises(sa.exc.DatabaseError, match="lock timeout"):
                    writer.execute(edge)
                transaction.rollback()
    # Once the reader commits the same writer can proceed; do not retain the edge.
    with engine.connect() as writer:
        with writer.begin() as transaction:
            writer.execute(sa.text("SET LOCAL lock_timeout = '1s'"))
            writer.execute(edge)
            transaction.rollback()
