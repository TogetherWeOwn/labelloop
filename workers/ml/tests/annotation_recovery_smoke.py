"""Committed fixture annotation recovery; not HTTP authorization or execution truth."""

import pytest
import sqlalchemy as sa

from labelloop_api.automatic_review_service import append_current_review
from labelloop_api.automatic_review_store import load_automatic_acceptance
from labelloop_api.generated_label_store import label_teacher_runs
from labelloop_api.model_schema import model_release
from labelloop_api.qualification_schema import generated_label_origin
from labelloop_api.revision_store import completeness_context_hash
from labelloop_api.schema import (
    annotation_revision,
    completeness_assessment,
    ontology_release,
    project,
    review_event,
    source_asset,
)


def annotation_rows(engine):
    with engine.connect() as connection:
        return {
            table.name: tuple(
                dict(row)
                for row in connection.execute(
                    sa.select(table).order_by(*table.primary_key.columns)
                ).mappings()
            )
            for table in (
                annotation_revision,
                review_event,
                completeness_assessment,
                generated_label_origin,
            )
        }


def assert_annotation_recovery(engine, release, final, context, recovery):
    assert annotation_rows(engine) == recovery["rows"]
    with engine.begin() as connection:
        links = label_teacher_runs(connection, recovery["project_id"])
        for asset_key, item in recovery["assets"].items():
            original = load_automatic_acceptance(
                connection, project_key=release.project_id, asset_key=asset_key, revision=2
            )
            assert original == item["original"]
            assert original.policy.pipeline == context.current_pipeline
            assert original.policy.targets == context.evidence.targets
            assert original.policy.targets.fixture_only is True
            assert original.scope == context.evidence.scope
            assert original.qualification_id == context.evidence.qualification_id
            assert original.release_sha256 == context.evidence.release_sha256
            assert original.evaluation_key == final.run_id
            assert links[asset_key] == [(2, recovery["teacher_run_id"])]
            with pytest.raises(ValueError, match="one exact original"):
                load_automatic_acceptance(
                    connection, project_key=release.project_id, asset_key=asset_key, revision=3
                )


def exercise_annotation_recovery(engine, release, final, context, canary, actor, revision):
    from qualification_context_smoke import _proposal_origin_fixture

    scope = dict(project_key=release.project_id, recipe_key=release.recipe_id)
    recovery = {"assets": {}}
    # Commit synthetic completeness and completed proposal inputs separately from
    # acceptance. These are not actual inference/reviewer execution attestations.
    with engine.begin() as connection:
        project_id = connection.execute(
            sa.select(project.c.id).where(project.c.project_key == release.project_id)
        ).scalar_one()
        recovery["project_id"] = project_id
        recovery["teacher_run_id"] = connection.execute(
            sa.select(model_release.c.training_run_id).where(
                model_release.c.project_id == project_id,
                model_release.c.release_key == release.release_id,
            )
        ).scalar_one()
        ontology = connection.execute(
            sa.select(ontology_release.c.class_map, ontology_release.c.classes, project.c.scope)
            .join(project)
            .where(project.c.id == project_id)
            .order_by(ontology_release.c.version.desc())
            .limit(1)
        ).one()
        for positive in (True, False):
            key = canary.release_key + ("-positive" if positive else "-negative")
            asset_id = connection.execute(
                sa.select(source_asset.c.id).where(
                    source_asset.c.project_id == project_id, source_asset.c.asset_key == key
                )
            ).scalar_one()
            objects = connection.execute(
                sa.select(annotation_revision.c.objects).where(
                    annotation_revision.c.asset_id == asset_id,
                    annotation_revision.c.revision == 1,
                )
            ).scalar_one()
            assert bool(objects) is positive
            connection.execute(
                sa.insert(completeness_assessment).values(
                    asset_id=asset_id,
                    revision=1,
                    ontology_hash=completeness_context_hash(*ontology),
                    scope_completeness="exhaustive_for_scope",
                    actor_kind="system",
                    actor_id=actor,
                    output_truncated=False,
                    unresolved_regions=[],
                )
            )
            proposals = tuple(
                _proposal_origin_fixture(connection, release, key, 1, suffix)
                for suffix in ("-durable-a", "-durable-b")
            )
            recovery["assets"][key] = dict(
                asset_id=asset_id,
                arguments=dict(
                    **scope,
                    asset_key=key,
                    actor_id=actor,
                    expected_revision=1,
                    expected_registry_revision=revision,
                    scope=context.evidence.scope,
                    objects=objects,
                    scope_completeness="exhaustive_for_scope",
                    training_eligible=True,
                    proposal_run_keys=proposals,
                ),
            )
    for key, item in recovery["assets"].items():
        before = annotation_rows(engine)
        arguments = item["arguments"]
        for changed, error, message in (
            ({"expected_registry_revision": revision - 1}, ValueError, None),
            (
                {"proposal_run_keys": (arguments["proposal_run_keys"][0], "zz-missing-durable")},
                sa.exc.NoResultFound,
                None,
            ),
        ):
            with engine.begin() as connection:
                with pytest.raises(error, match=message):
                    append_current_review(connection, **(arguments | changed))
            assert annotation_rows(engine) == before

        injected = []

        def fail_origin(
            _conn, _cursor, statement, _parameters, _context, _many, _injected=injected
        ):
            if statement.startswith("INSERT INTO qualification_generated_label_origin"):
                _injected.append(True)
                raise sa.exc.SQLAlchemyError("private committed annotation fixture failure")

        # The review event and revision have already been inserted when this fails.
        sa.event.listen(engine, "before_cursor_execute", fail_origin)
        try:
            with engine.begin() as connection:
                with pytest.raises(sa.exc.SQLAlchemyError, match="committed annotation fixture"):
                    append_current_review(connection, **arguments)
        finally:
            sa.event.remove(engine, "before_cursor_execute", fail_origin)
        assert injected == [True]
        assert annotation_rows(engine) == before

        with engine.begin() as connection:
            assert append_current_review(connection, **arguments) == 2
        after = annotation_rows(engine)
        for table in before:
            assert len(after[table]) == len(before[table]) + 1
            assert all(row in after[table] for row in before[table])
            added = next(row for row in after[table] if row not in before[table])
            assert added["asset_id"] == item["asset_id"]
            assert added["actor_id"] == actor
            if table == review_event.name:
                assert added["actor_kind"] == "system"
                assert added["expected_revision"] == 1
                assert added["correction"]["proposal_run_keys"] == sorted(
                    arguments["proposal_run_keys"]
                )
            else:
                assert added["revision"] == 2
            if table == annotation_revision.name:
                assert added["objects"] == arguments["objects"]
                assert added["parent_revision"] == 1
                assert added["actor_kind"] == "system"
                assert added["approval_state"] == "auto_accepted"
                assert added["review_tier"] == "machine_verified_no_human_review"
                assert added["scope_completeness"] == "exhaustive_for_scope"
                assert added["training_eligible"] is True
                assert added["gold_eligible"] is False
        with engine.begin() as connection:
            item["original"] = load_automatic_acceptance(
                connection, project_key=release.project_id, asset_key=key, revision=2
            )
        # A committed retry of the old expected revision cannot duplicate any row.
        with engine.begin() as connection:
            with pytest.raises(ValueError, match="stale expected annotation revision"):
                append_current_review(connection, **arguments)
        assert annotation_rows(engine) == after
    recovery["rows"] = annotation_rows(engine)
    assert_annotation_recovery(engine, release, final, context, recovery)
    return recovery
