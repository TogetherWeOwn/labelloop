"""Committed fixture recovery and admin metadata; no annotation HTTP write grant."""

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
    assert_http_annotation_recovery(engine, release, recovery)


def assert_http_annotation_recovery(engine, release, recovery):
    from uuid import uuid4

    from registry_http_smoke import (
        authenticated_client,
        login,
        qualification_journals,
        snapshot,
    )

    from labelloop_api import annotation_reads, automatic_review_store
    from labelloop_core.qualification_data import record_sha256

    registry = snapshot(engine, release.project_id)
    journals = qualification_journals(engine)
    with authenticated_client(engine) as (client, identities, workers):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(annotation_reads, "annotation_engine", lambda: engine)
            for key, item in recovery["assets"].items():
                base = f"/projects/{recovery['project_id']}/assets/{item['asset_id']}"
                path = base + "/annotation-history/2/automatic-acceptance"
                client.cookies.clear()
                assert client.get(path).status_code == 401
                for token in workers.values():
                    response = client.get(path, headers={"authorization": "Bearer " + token})
                    assert response.status_code == 403
                login(client, identities["reviewer"][1])
                assert client.get(path + "?role=admin").status_code == 403
                login(client, identities["admin"][1])
                original = item["original"]
                expected = dict(
                    revision=2,
                    historical_only=True,
                    qualification_id=original.qualification_id,
                    release_sha256=original.release_sha256,
                    evaluation_key=original.evaluation_key,
                    pipeline_id=original.policy.pipeline.pipeline_id,
                    policy_sha256=record_sha256(original.policy),
                    fixture_only=True,
                )
                response = client.get(path)
                assert response.status_code == 200
                assert response.json() == expected
                assert response.headers["cache-control"] == "no-store"
                for absent in (
                    f"/projects/{uuid4()}/assets/{item['asset_id']}",
                    f"/projects/{recovery['project_id']}/assets/{uuid4()}",
                ):
                    response = client.get(absent + "/annotation-history/2/automatic-acceptance")
                    assert response.status_code == 404
                    assert response.json() == {"detail": "annotation_not_found"}
                response = client.get(base + "/annotation-history/3/automatic-acceptance")
                assert response.status_code == 409
                assert response.json() == {"detail": "automatic_acceptance_provenance_unavailable"}
                reader = automatic_review_store.load_automatic_acceptance
                loaded = []

                def fail_after_read(
                    connection, _key=key, _loaded=loaded, _reader=reader, **arguments
                ):
                    assert arguments["asset_key"] == _key
                    _loaded.append(_reader(connection, **arguments))
                    raise sa.exc.SQLAlchemyError("private historical provenance read failure")

                with pytest.MonkeyPatch.context() as failure:
                    failure.setattr(
                        automatic_review_store, "load_automatic_acceptance", fail_after_read
                    )
                    response = client.get(path)
                assert loaded == [original]
                assert response.status_code == 503
                assert response.json() == {"detail": "annotations_unavailable"}
                assert response.headers["cache-control"] == "no-store"
                assert client.get(path).json() == expected
    assert annotation_rows(engine) == recovery["rows"]
    assert snapshot(engine, release.project_id) == registry
    assert qualification_journals(engine) == journals


def exercise_annotation_policy_recovery(engine, release, final, context, actor, recovery):
    from dataclasses import replace

    from registry_http_smoke import qualification_journals, snapshot

    from labelloop_api.qualification_policy_store import (
        append_qualification_policy,
        current_policy_context,
        load_qualification_policy,
    )
    from labelloop_api.qualification_schema import policy_event
    from labelloop_core.qualification import QualificationTargets
    from labelloop_core.qualification_data import record_sha256

    scope = dict(project_key=release.project_id, recipe_key=release.recipe_id)
    heads = snapshot(engine, release.project_id)
    with engine.begin() as connection:
        original = load_qualification_policy(connection, **scope)
        assert current_policy_context(connection, context) == context
    prompt = "1" * 64 if original.policy.pipeline.prompt_sha256 != "1" * 64 else "2" * 64
    null_targets = QualificationTargets()
    changes = (
        replace(original.policy, pipeline=replace(original.policy.pipeline, prompt_sha256=prompt)),
        replace(
            original.policy,
            scope=tuple(
                replace(cell, condition=cell.condition + "-changed")
                for cell in original.policy.scope
            ),
        ),
        replace(
            original.policy,
            targets=null_targets,
            pipeline=replace(
                original.policy.pipeline, acceptance_policy_sha256=record_sha256(null_targets)
            ),
        ),
    )
    # Use current annotation and registry revisions, without replaying old proposal
    # jobs. Prove the identical writes can pass before/after policy restoration;
    # stale annotations or proposal identities must not explain policy rejection.
    arguments = tuple(
        item["arguments"] | {"expected_revision": 2, "proposal_run_keys": ()}
        for item in recovery["assets"].values()
    )

    def valid_writes():
        for values in arguments:
            with engine.begin() as connection:
                savepoint = connection.begin_nested()
                assert append_current_review(connection, **values) == 3
                savepoint.rollback()
        assert annotation_rows(engine) == recovery["rows"]

    def configure(policy, expected_revision):
        before = qualification_journals(engine)
        with engine.begin() as connection:
            changed = append_qualification_policy(
                connection,
                **scope,
                actor_id=actor,
                expected_revision=expected_revision,
                policy=policy,
            )
        after = qualification_journals(engine)
        for name, rows in before.items():
            if name == policy_event.name:
                assert len(after[name]) == len(rows) + 1
                assert all(row in after[name] for row in rows)
            else:
                assert after[name] == rows
        with engine.begin() as connection:
            assert load_qualification_policy(connection, **scope) == changed
        return changed, after

    valid_writes()
    revision = original.revision
    for policy in changes:
        assert policy != original.policy
        changed, journals = configure(policy, revision)
        for values in arguments:
            with engine.begin() as connection:
                with pytest.raises(ValueError, match="stale or substituted qualification policy"):
                    append_current_review(connection, **values)
            assert annotation_rows(engine) == recovery["rows"]
            assert snapshot(engine, release.project_id) == heads
            assert qualification_journals(engine) == journals
        # Real admin sessions must still read the exact original policy, not the
        # changed current configuration, and reads must preserve every journal row.
        assert_annotation_recovery(engine, release, final, context, recovery)
        assert qualification_journals(engine) == journals
        restored, journals = configure(original.policy, changed.revision)
        revision = restored.revision
        with engine.begin() as connection:
            assert current_policy_context(connection, context) == context
        valid_writes()
        assert_annotation_recovery(engine, release, final, context, recovery)
        assert snapshot(engine, release.project_id) == heads
        assert qualification_journals(engine) == journals
    return len(changes)


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
