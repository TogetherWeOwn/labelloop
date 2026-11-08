"""Persisted assembly on explicitly invented canonical groups/counts, not domain gold."""

import hashlib
from dataclasses import replace

import pytest
import sqlalchemy as sa

from labelloop_api import model_schema
from labelloop_api.acceptance_store import pause_acceptance, require_automatic_acceptance
from labelloop_api.annotation_audit_store import record_annotation_audit
from labelloop_api.annotation_evidence_store import record_annotation_evidence
from labelloop_api.audit_sample_store import (
    freeze_audit_sample,
    freeze_automatic_audit_sample,
    load_audit_sample,
)
from labelloop_api.automatic_review_store import load_automatic_acceptance
from labelloop_api.final_test_store import register_final_test
from labelloop_api.model_store import change_registry, load_registry
from labelloop_api.qualification_context_store import (
    AnnotationOutcomeRef,
    load_qualification_context,
)
from labelloop_api.qualification_policy_store import (
    append_qualification_policy,
    load_qualification_policy,
)
from labelloop_api.qualification_store import lock_project
from labelloop_api.revision_store import AutomaticReview, append_review, completeness_context_hash
from labelloop_api.schema import (
    annotation_revision,
    canonical_dataset_release,
    completeness_assessment,
    ontology_release,
    project,
    review_event,
    source_asset,
    source_split_assignment,
)
from labelloop_core.audit import AuditObservation
from labelloop_core.qualification import AnnotationEvidence, AnnotationMeasurement
from labelloop_core.qualification_data import SourceGroup
from labelloop_core.registry import PromotionPolicy


def _outcome(
    connection, release, context, actor, *, canary, group_id=None, split="train", suffix=""
):
    """Seed synthetic sources, optionally on an already-used group; never edit CPU inputs."""
    prefix = release.release_id + ("-assembly-canary" if canary else "-assembly-audit") + suffix
    project_id = lock_project(connection, release.project_id)
    original = (
        connection.execute(
            sa.select(canonical_dataset_release)
            .where(canonical_dataset_release.c.project_id == project_id)
            .limit(1)
        )
        .mappings()
        .one()
    )
    membership = {}
    group = group_id if group_id is not None else prefix + "-group"
    for positive in (True, False):
        key = prefix + ("-positive" if positive else "-negative")
        digest = hashlib.sha256(key.encode()).hexdigest()
        asset_id = connection.execute(
            sa.insert(source_asset)
            .values(
                project_id=project_id,
                asset_key=key,
                sha256=digest,
                object_key=f"sha256/{digest[:2]}/{digest}",
                width=32,
                height=32,
                exif_orientation=1,
                source_uri="fixture://context-assembly",
            )
            .returning(source_asset.c.id)
        ).scalar_one()
        connection.execute(
            sa.insert(annotation_revision).values(
                asset_id=asset_id,
                revision=1,
                actor_kind="system",
                actor_id="synthetic-teacher",
                objects=[
                    {"object_id": value, "class_id": value, "box": [0, 0, 8, 8]}
                    for value in release.class_ids
                ]
                if positive
                else [],
                approval_state="auto_accepted",
                review_tier="machine_verified_no_human_review",
                scope_completeness="exhaustive_for_scope",
                training_eligible=True,
                gold_eligible=False,
            )
        )
        connection.execute(
            sa.insert(source_split_assignment).values(
                assignment_version=original["assignment_version"],
                asset_id=asset_id,
                group_id=group,
                split=split,
                seed=7,
                algorithm="explicit-synthetic",
                thresholds={},
                reason="fixture-only context assembly",
                locked=True,
            )
        )
        membership[key] = 1
    connection.execute(
        sa.insert(canonical_dataset_release).values(
            **{
                key: value
                for key, value in original.items()
                if key
                not in {"id", "created_at", "release_key", "sample_revisions", "excluded_asset_ids"}
            },
            release_key=prefix,
            sample_revisions=membership,
            excluded_asset_ids=[],
        )
    )
    frozen = freeze_audit_sample(
        connection,
        project_key=release.project_id,
        release_key=prefix,
        sample_key=prefix,
        actor_id=actor,
        pipeline_id=context.current_pipeline.pipeline_id,
        size=2,
        seed=7,
    )
    observations = tuple(
        AuditObservation(item.asset_id, 1, actor, True, True, True)
        for item in frozen.sample.candidates
    )
    record_annotation_audit(
        connection,
        project_key=release.project_id,
        release_key=prefix,
        audit_key=prefix,
        actor_id=actor,
        sample_key=prefix,
        observations=observations,
    )
    cells = context.evidence.scope
    evidence = AnnotationEvidence(
        frozen.sample,
        observations,
        tuple(
            SourceGroup(
                key, group, cells[0].source_id, tuple(sorted({cell.condition for cell in cells}))
            )
            for key in membership
        ),
        "constructive_synthetic",
        tuple(AnnotationMeasurement(cell, (group,), 1, 1, 1, 1, 0.0) for cell in cells),
    )
    record_annotation_evidence(
        connection,
        project_key=release.project_id,
        release_key=prefix,
        audit_key=prefix,
        evidence_key=prefix,
        actor_id=actor,
        evidence=evidence,
        canary_model_key=release.release_id if canary else None,
    )
    if canary:
        ordinary = prefix + "-ordinary"
        record_annotation_audit(
            connection,
            project_key=release.project_id,
            release_key=prefix,
            audit_key=ordinary,
            actor_id=actor,
            sample_key=prefix,
            observations=observations,
        )
        record_annotation_evidence(
            connection,
            project_key=release.project_id,
            release_key=prefix,
            audit_key=ordinary,
            evidence_key=ordinary,
            actor_id=actor,
            evidence=evidence,
        )
        partial = prefix + "-partial"
        record_annotation_audit(
            connection,
            project_key=release.project_id,
            release_key=prefix,
            audit_key=partial,
            actor_id=actor,
            sample_key=prefix,
            observations=observations[:1],
        )
        record_annotation_evidence(
            connection,
            project_key=release.project_id,
            release_key=prefix,
            audit_key=partial,
            evidence_key=partial,
            actor_id=actor,
            evidence=replace(evidence, observations=observations[:1]),
            canary_model_key=release.release_id,
        )
    return AnnotationOutcomeRef(prefix, prefix, prefix)


def _proposal_origin_fixture(connection, release, asset_key, revision, suffix="", references=()):
    """Synthetic completed producer rows; not a claim of inference execution."""
    import hashlib
    import json

    from labelloop_api.model_store import json_record
    from labelloop_core.json_records import canonical_json

    run_key = asset_key + f"-proposal-{revision}" + suffix
    binding = dict(
        run_id=run_key,
        source=dict(project_id=release.project_id, asset_id=asset_key),
        expected_revision=revision,
    )
    teacher = json_record(release)
    record = dict(
        run_id=run_key,
        project_id=release.project_id,
        binding=binding,
        teacher=teacher,
        checkpoint_sha256=release.weights_sha256,
        approval_state="proposed",
        review_tier="unreviewed",
        scope_completeness="unreviewed",
        training_eligible=False,
        gold_eligible=False,
        evidence_ids=sorted(reference["reference_id"] for reference in references),
    )
    inputs = dict(
        run_id=run_key, request=binding, registry={"champion": teacher}, references=list(references)
    )
    digest = hashlib.sha256(canonical_json(record)).hexdigest()
    parameters = dict(
        key=run_key,
        project=release.project_id,
        inputs=json.dumps(inputs),
        record=json.dumps(record),
        digest=digest,
    )
    for statement in (
        "INSERT INTO worker_job(job_id,project_key,operation_key,kind,input,status,max_attempts) "
        "VALUES (:key,:project,:key,'proposals',CAST(:inputs AS jsonb),'succeeded',1)",
        "INSERT INTO worker_application(job_id,generation) VALUES (:key,0)",
        "INSERT INTO worker_stage_artifact(job_id,stage,sha256,payload,generation) "
        "VALUES (:key,'proposal',:digest,CAST(:record AS jsonb),0)",
        "INSERT INTO worker_proposal_run(run_id,job_id,project_key,sha256,record) "
        "VALUES (:key,:key,:project,:digest,CAST(:record AS jsonb))",
    ):
        connection.execute(sa.text(statement), parameters)
    return run_key


def _exercise_annotation_write(connection, release, final, loaded, canary, actor):
    """Actual revision/event writes, followed by a persisted scoped-pause veto."""
    with connection.begin_nested() as transaction:
        automatic = AutomaticReview(release.recipe_id, final.run_id, loaded.evidence.scope, loaded)
        for positive in (True, False):
            asset_key = canary.release_key + ("-positive" if positive else "-negative")
            asset_id = connection.execute(
                sa.select(source_asset.c.id).where(source_asset.c.asset_key == asset_key)
            ).scalar_one()
            ontology = connection.execute(
                sa.select(ontology_release.c.class_map, ontology_release.c.classes, project.c.scope)
                .join(project)
                .where(project.c.project_key == release.project_id)
                .order_by(ontology_release.c.version.desc())
                .limit(1)
            ).one()
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
            objects = connection.execute(
                sa.select(annotation_revision.c.objects).where(
                    annotation_revision.c.asset_id == asset_id,
                    annotation_revision.c.revision == 1,
                )
            ).scalar_one()
            arguments = dict(
                asset_id=asset_key,
                actor_kind="system",
                actor_id=actor,
                approval_state="auto_accepted",
                review_tier="machine_verified_no_human_review",
                scope_completeness="exhaustive_for_scope",
                objects=objects,
                training_eligible=True,
                automatic=automatic,
            )
            arguments["proposal_run_keys"] = (
                _proposal_origin_fixture(connection, release, asset_key, 1, "-a"),
                _proposal_origin_fixture(connection, release, asset_key, 1, "-b"),
            )
            assert append_review(connection, expected_revision=1, **arguments) == 2
            original = load_automatic_acceptance(
                connection, project_key=release.project_id, asset_key=asset_key, revision=2
            )
            assert original.policy.pipeline == loaded.current_pipeline
            assert original.policy.targets == loaded.evidence.targets
            assert original.policy.targets.fixture_only is True
            assert original.scope == automatic.scope
            assert original.release_sha256 == loaded.evidence.release_sha256
            assert original.evaluation_key == final.run_id
            with pytest.raises(ValueError, match="one exact original"):
                load_automatic_acceptance(
                    connection, project_key=release.project_id, asset_key=asset_key, revision=3
                )
            source_release = (
                connection.execute(
                    sa.select(canonical_dataset_release).where(
                        canonical_dataset_release.c.release_key == canary.release_key,
                        canonical_dataset_release.c.project_id
                        == lock_project(connection, release.project_id),
                    )
                )
                .mappings()
                .one()
            )
            strict_release = asset_key + "-original-acceptance"
            strict_release_id = connection.execute(
                sa.insert(canonical_dataset_release)
                .values(
                    **{
                        key: value
                        for key, value in source_release.items()
                        if key not in {"id", "created_at", "release_key", "sample_revisions"}
                    },
                    release_key=strict_release,
                    sample_revisions={asset_key: 2},
                )
                .returning(canonical_dataset_release.c.id)
            ).scalar_one()
            sample_arguments = dict(
                project_key=release.project_id,
                release_key=strict_release,
                sample_key=strict_release,
                actor_id=actor,
                pipeline=original.policy.pipeline,
                size=1,
                seed=17,
            )
            frozen = freeze_automatic_audit_sample(connection, **sample_arguments)
            assert frozen.sample.candidates[0].revision.revision == 2
            assert frozen.sample.candidates[0].pipeline_id == original.policy.pipeline.pipeline_id
            assert (
                load_audit_sample(
                    connection,
                    project_id=source_release["project_id"],
                    release_id=strict_release_id,
                    sample_key=strict_release,
                    require_current_groups=True,
                    original_pipeline=original.policy.pipeline,
                )
                == frozen
            )
            # Legacy fixture decisions cannot acquire invented original policy.
            with pytest.raises(ValueError):
                freeze_automatic_audit_sample(
                    connection,
                    **(
                        sample_arguments
                        | {
                            "release_key": canary.release_key,
                            "sample_key": strict_release + "-historical",
                        }
                    ),
                )
            # Original provenance does not silently become today's policy on reload.
            with connection.begin_nested() as policy_transaction:
                current = load_qualification_policy(
                    connection, project_key=release.project_id, recipe_key=release.recipe_id
                )
                append_qualification_policy(
                    connection,
                    project_key=release.project_id,
                    recipe_key=release.recipe_id,
                    actor_id=actor,
                    expected_revision=current.revision,
                    policy=replace(
                        current.policy,
                        pipeline=replace(current.policy.pipeline, reviewer_model_version="changed"),
                    ),
                )
                assert (
                    load_automatic_acceptance(
                        connection, project_key=release.project_id, asset_key=asset_key, revision=2
                    )
                    == original
                )
                changed_pipeline = replace(
                    original.policy.pipeline, reviewer_model_version="changed"
                )
                with pytest.raises(ValueError, match="original acceptance pipeline"):
                    load_audit_sample(
                        connection,
                        project_id=source_release["project_id"],
                        sample_key=strict_release,
                        require_current_groups=True,
                        original_pipeline=changed_pipeline,
                    )
                with pytest.raises(ValueError, match="original acceptance pipeline"):
                    freeze_automatic_audit_sample(
                        connection,
                        **(
                            sample_arguments
                            | {
                                "pipeline": changed_pipeline,
                                "sample_key": strict_release + "-changed",
                            }
                        ),
                    )
                assert (
                    freeze_automatic_audit_sample(
                        connection,
                        **(sample_arguments | {"sample_key": strict_release + "-original"}),
                    ).sample.candidates
                    == frozen.sample.candidates
                )
                with pytest.raises(ValueError):
                    append_review(connection, expected_revision=2, **arguments)
                policy_transaction.rollback()
            from labelloop_api.generated_label_store import label_teacher_runs

            project_id = connection.execute(
                sa.select(project.c.id).where(project.c.project_key == release.project_id)
            ).scalar_one()
            assert label_teacher_runs(connection, project_id)[asset_key] == [
                (
                    2,
                    connection.execute(
                        sa.select(model_schema.model_release.c.training_run_id).where(
                            model_schema.model_release.c.release_key == release.release_id
                        )
                    ).scalar_one(),
                )
            ]
            from labelloop_api.model_store import load_release

            alternate_id = connection.execute(
                sa.select(model_schema.model_release.c.id)
                .where(
                    model_schema.model_release.c.project_id == project_id,
                    model_schema.model_release.c.release_key != release.release_id,
                )
                .order_by(model_schema.model_release.c.release_key)
                .limit(1)
            ).scalar_one()
            alternate = load_release(connection, alternate_id)
            assert alternate is not None
            teachers = sorted((release, alternate), key=lambda item: item.release_id)
            arguments["proposal_run_keys"] = tuple(
                _proposal_origin_fixture(connection, teacher, asset_key, 2, f"-{index}")
                for index, teacher in enumerate(teachers)
            )
            # A missing later run must not leave the first teacher or revision behind.
            with pytest.raises(sa.exc.NoResultFound):
                append_review(
                    connection,
                    expected_revision=2,
                    **(
                        arguments
                        | {
                            "proposal_run_keys": (
                                arguments["proposal_run_keys"][0],
                                "zz-missing-proposal",
                            )
                        }
                    ),
                )
            assert (
                connection.execute(
                    sa.select(annotation_revision.c.actor_id).where(
                        annotation_revision.c.asset_id == asset_id,
                        annotation_revision.c.revision == 2,
                    )
                ).scalar_one()
                == actor
            )
            # A failing write after the review-event insert must roll back both rows.
            connection.execute(
                sa.text(
                    "CREATE FUNCTION pg_temp.reject_automatic_revision() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN "
                    "RAISE EXCEPTION 'injected revision failure'; END $$"
                )
            )
            connection.execute(
                sa.text(
                    "CREATE TRIGGER reject_automatic_revision BEFORE INSERT ON annotation_revision "
                    "FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_automatic_revision()"
                )
            )
            with pytest.raises(sa.exc.DBAPIError, match="injected revision failure"):
                append_review(connection, expected_revision=2, **arguments)
            connection.execute(
                sa.text("DROP TRIGGER reject_automatic_revision ON annotation_revision")
            )
            connection.execute(sa.text("DROP FUNCTION pg_temp.reject_automatic_revision()"))
            event_count = connection.execute(
                sa.select(sa.func.count())
                .select_from(review_event)
                .where(review_event.c.asset_id == asset_id)
            ).scalar_one()
            assert event_count == 1
            connection.execute(
                sa.text(
                    "CREATE FUNCTION pg_temp.reject_origin() RETURNS trigger LANGUAGE plpgsql "
                    "AS $$ BEGIN IF EXISTS (SELECT 1 FROM qualification_generated_label_origin "
                    "WHERE asset_id = NEW.asset_id AND revision = NEW.revision) THEN "
                    "RAISE EXCEPTION 'injected origin failure'; END IF; RETURN NEW; END $$"
                )
            )
            connection.execute(
                sa.text(
                    "CREATE TRIGGER reject_origin BEFORE INSERT ON "
                    "qualification_generated_label_origin "
                    "FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_origin()"
                )
            )
            with pytest.raises(sa.exc.DBAPIError, match="injected origin failure"):
                append_review(connection, expected_revision=2, **arguments)
            connection.execute(
                sa.text("DROP TRIGGER reject_origin ON qualification_generated_label_origin")
            )
            connection.execute(sa.text("DROP FUNCTION pg_temp.reject_origin()"))
            assert (
                connection.execute(
                    sa.select(sa.func.count())
                    .select_from(review_event)
                    .where(review_event.c.asset_id == asset_id)
                ).scalar_one()
                == event_count
            )
            assert (
                connection.execute(
                    sa.select(sa.func.max(annotation_revision.c.revision)).where(
                        annotation_revision.c.asset_id == asset_id
                    )
                ).scalar_one()
                == 2
            )
            assert len(label_teacher_runs(connection, project_id)[asset_key]) == 1
            # Scope state is journaled; the same previously valid context must now fail.
            with connection.begin_nested() as pause_transaction:
                pause_acceptance(
                    connection,
                    project_key=release.project_id,
                    recipe_key=release.recipe_id,
                    actor_id=actor,
                    expected_revision=0,
                    pause_id=asset_key + "-pause",
                    qualification_id=loaded.evidence.qualification_id,
                    scope=(loaded.evidence.scope[0],),
                    reason="synthetic annotation containment",
                )
                with pytest.raises(ValueError, match="paused"):
                    append_review(connection, expected_revision=2, **arguments)
                assert (
                    connection.execute(
                        sa.select(sa.func.count())
                        .select_from(review_event)
                        .where(review_event.c.asset_id == asset_id)
                    ).scalar_one()
                    == event_count
                )
                assert (
                    connection.execute(
                        sa.select(sa.func.max(annotation_revision.c.revision)).where(
                            annotation_revision.c.asset_id == asset_id
                        )
                    ).scalar_one()
                    == 2
                )
                pause_transaction.rollback()
            with pytest.raises(ValueError, match="one exact original"):
                load_automatic_acceptance(
                    connection, project_key=release.project_id, asset_key=asset_key, revision=3
                )
            assert (
                load_automatic_acceptance(
                    connection, project_key=release.project_id, asset_key=asset_key, revision=2
                )
                == original
            )
            assert append_review(connection, expected_revision=2, **arguments) == 3
            assert (
                load_automatic_acceptance(
                    connection, project_key=release.project_id, asset_key=asset_key, revision=3
                )
                == original
            )
            assert (
                load_automatic_acceptance(
                    connection, project_key=release.project_id, asset_key=asset_key, revision=2
                )
                == original
            )
            expected_runs = {
                connection.execute(
                    sa.select(model_schema.model_release.c.training_run_id).where(
                        model_schema.model_release.c.release_key == teacher.release_id,
                        model_schema.model_release.c.project_id == project_id,
                    )
                ).scalar_one()
                for teacher in teachers
            }
            links = label_teacher_runs(connection, project_id)[asset_key]
            assert len(links) == 3
            assert {run for revision, run in links if revision == 3} == expected_runs
            assert (
                connection.execute(
                    sa.select(sa.func.count())
                    .select_from(review_event)
                    .where(review_event.c.asset_id == asset_id)
                ).scalar_one()
                == event_count + 1
            )
        transaction.rollback()


def _exercise_evaluation_groups(connection, release, annotation, canary, actor):
    """Canonical grouping proof only; role selection and independent truth are separate."""
    from labelloop_api.evaluation_group_store import load_evaluation_set
    from labelloop_api.schema import similarity_relation

    arguments = dict(project_key=release.project_id, dataset_key=annotation.release_key)
    expected_group = annotation.release_key + "-group"
    for role in ("development", "calibration", "locked_final_test"):
        assert load_evaluation_set(connection, **arguments, role=role).group_ids == (
            expected_group,
        )
    canonical = (
        connection.execute(
            sa.select(canonical_dataset_release).where(
                canonical_dataset_release.c.release_key == annotation.release_key
            )
        )
        .mappings()
        .one()
    )
    for change in (
        {"sample_revisions": {"missing-source": 1}},
        {"sample_revisions": dict.fromkeys(canonical["sample_revisions"], 999)},
        {"assignment_version": "missing-evaluation-assignment"},
        {"excluded_asset_ids": list(canonical["sample_revisions"])},
        {"frozen": False},
    ):
        with connection.begin_nested() as transaction:
            key = annotation.release_key + "-invalid-evaluation"
            connection.execute(
                sa.insert(canonical_dataset_release).values(
                    **(
                        {
                            key: value
                            for key, value in canonical.items()
                            if key not in {"id", "created_at"}
                        }
                        | {"release_key": key}
                        | change
                    )
                )
            )
            with pytest.raises((ValueError, sa.exc.NoResultFound)):
                load_evaluation_set(
                    connection, **(arguments | {"dataset_key": key}), role="development"
                )
            transaction.rollback()
    with connection.begin_nested() as transaction:
        dataset = load_evaluation_set(connection, **arguments, role="locked_final_test")
        register_final_test(connection, dataset=dataset, actor_id=actor)
        for role in ("development", "calibration"):
            with pytest.raises(ValueError, match="role forbids"):
                load_evaluation_set(connection, **arguments, role=role)
        transaction.rollback()
    with connection.begin_nested() as transaction:
        identities = {
            row.asset_key: row.id
            for row in connection.execute(
                sa.select(source_asset.c.asset_key, source_asset.c.id).where(
                    source_asset.c.asset_key.in_(
                        [annotation.release_key + "-positive", canary.release_key + "-positive"]
                    )
                )
            )
        }
        connection.execute(
            sa.insert(similarity_relation).values(
                left_asset_id=identities[annotation.release_key + "-positive"],
                right_asset_id=identities[canary.release_key + "-positive"],
                reason="declared_metadata",
                evidence="synthetic evaluation grouping conflict",
                algorithm_version="synthetic-v1",
            )
        )
        with pytest.raises(ValueError, match="merge pinned groups"):
            load_evaluation_set(connection, **arguments, role="locked_final_test")
        transaction.rollback()
    assert load_evaluation_set(connection, **arguments, role="development").group_ids == (
        expected_group,
    )


def _exercise_recorded_selection(
    connection, release, final, loaded, arguments, actor, before, count
):
    from evaluation_fixture import independent_evaluation_variant

    from labelloop_api.annotation_evidence_store import load_annotation_evidence
    from labelloop_api.evaluation_group_store import load_evaluation_set
    from labelloop_api.final_test_store import record_candidate_selection
    from labelloop_api.model_store import json_record, save_evaluation
    from labelloop_api.training_group_store import model_input_groups

    scope = dict(project_key=release.project_id, recipe_key=release.recipe_id)
    project_id = lock_project(connection, release.project_id)
    model = (
        connection.execute(
            sa.select(model_schema.model_release).where(
                model_schema.model_release.c.project_id == project_id,
                model_schema.model_release.c.release_key == release.release_id,
            )
        )
        .mappings()
        .one()
    )
    source_variant = connection.execute(
        sa.select(model_schema.evaluation_run.c.variant_id).where(
            model_schema.evaluation_run.c.run_key == final.run_id
        )
    ).scalar_one()
    for role in ("development", "calibration"):
        with connection.begin_nested() as transaction:
            # An unselected sibling of this stored run used a noncompiled holdout.
            # Keep the candidate's existing final/canary digest unchanged.
            prefix = release.release_id + "-prior-" + role
            dataset = prefix + "-dataset"
            variant = independent_evaluation_variant(connection, source_variant, dataset)
            development_dataset = dataset if role == "development" else prefix + "-development"
            development_variant = (
                variant
                if role == "development"
                else independent_evaluation_variant(connection, source_variant, development_dataset)
            )
            development = replace(
                final,
                run_id=prefix + "-development-run",
                dataset_release_id=development_dataset,
                role="development",
            )
            calibration = (
                replace(
                    final,
                    run_id=prefix + "-calibration-run",
                    dataset_release_id=dataset,
                    role="calibration",
                )
                if role == "calibration"
                else None
            )
            sibling = replace(
                release,
                release_id=prefix,
                evaluation_run_ids=(development.run_id,)
                + ((calibration.run_id,) if calibration else ()),
            )
            values = {key: value for key, value in model.items() if key not in {"id", "created_at"}}
            values.update(release_key=sibling.release_id, record=json_record(sibling))
            connection.execute(sa.insert(model_schema.model_release).values(**values))
            save_evaluation(
                connection,
                model_key=sibling.release_id,
                variant_id=development_variant,
                report=development,
            )
            if calibration:
                save_evaluation(
                    connection, model_key=sibling.release_id, variant_id=variant, report=calibration
                )
            record_candidate_selection(
                connection,
                project_key=release.project_id,
                model_key=sibling.release_id,
                selection_key=prefix + "-selection",
                development_key=development.run_id,
                development_set=load_evaluation_set(
                    connection,
                    project_key=release.project_id,
                    dataset_key=development_dataset,
                    role="development",
                ),
                actor_id=actor,
                calibration_key=calibration.run_id if calibration else None,
                calibration_set=load_evaluation_set(
                    connection,
                    project_key=release.project_id,
                    dataset_key=dataset,
                    role="calibration",
                )
                if calibration
                else None,
            )
            assert (
                dataset + "-group"
                in model_input_groups(
                    connection, project_id=project_id, model_id=model["id"]
                ).recorded_selection
            )
            reused = _outcome(
                connection,
                release,
                loaded,
                actor,
                canary=True,
                group_id=dataset + "-group",
                split="test",
                suffix="-recorded-" + role,
            )
            stored = load_annotation_evidence(
                connection,
                project_key=release.project_id,
                release_key=reused.release_key,
                audit_key=reused.audit_key,
                evidence_key=reused.evidence_key,
                canary_model_key=release.release_id,
            )
            replay = replace(
                loaded, evidence=replace(loaded.evidence, canary_audit=stored.evidence)
            )
            message = "canary groups overlap recorded selection or calibration"
            with pytest.raises(ValueError, match=message):
                load_qualification_context(connection, **(arguments | {"canary": reused}))
            with pytest.raises(ValueError, match=message):
                require_automatic_acceptance(
                    connection,
                    **scope,
                    evaluation_key=final.run_id,
                    qualification=replay,
                    scope=loaded.evidence.scope[0],
                )
            with pytest.raises(ValueError, match=message):
                change_registry(
                    connection,
                    **scope,
                    actor_id=actor,
                    expected_revision=before.revision,
                    candidate_key=release.release_id,
                    evaluation_key=final.run_id,
                    policy=PromotionPolicy(1, 0.0),
                    qualification=replay,
                )
            assert load_registry(connection, **scope) == before
            assert (
                connection.execute(
                    sa.select(sa.func.count()).select_from(model_schema.promotion_event)
                ).scalar_one()
                == count
            )
            transaction.rollback()


def exercise_context_assembly(engine, release, final, context, actor):
    scope = dict(project_key=release.project_id, recipe_key=release.recipe_id)
    with engine.begin() as connection:
        annotation = _outcome(connection, release, context, actor, canary=False)
        canary = _outcome(connection, release, context, actor, canary=True)
        before = load_registry(connection, **scope)
        count = connection.execute(
            sa.select(sa.func.count()).select_from(model_schema.promotion_event)
        ).scalar_one()
    from labelloop_api.training_group_store import model_training_groups

    with engine.begin() as connection:
        project_id = lock_project(connection, release.project_id)
        model_id = connection.execute(
            sa.select(model_schema.model_release.c.id).where(
                model_schema.model_release.c.project_id == project_id,
                model_schema.model_release.c.release_key == release.release_id,
            )
        ).scalar_one()
        training_groups = model_training_groups(
            connection, project_id=project_id, model_id=model_id
        )
    arguments = dict(
        **scope,
        model_key=release.release_id,
        evaluation_key=final.run_id,
        qualification_key=release.release_id + "-assembled",
        annotation=annotation,
        canary=canary,
        lineage=replace(
            context.evidence.lineage,
            training_group_ids=tuple(
                sorted(set(context.evidence.lineage.training_group_ids) | set(training_groups))
            ),
        ),
    )
    # Reconnect: model/report, configuration, journal and both measured outcomes
    # must come from PostgreSQL, then pass the existing pure gates without a bypass.
    with engine.begin() as connection:
        with pytest.raises(ValueError, match="omits compiled training groups"):
            load_qualification_context(
                connection,
                **(
                    arguments
                    | {"lineage": replace(arguments["lineage"], training_group_ids=("invented",))}
                ),
            )
        assert load_registry(connection, **scope) == before
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(model_schema.promotion_event)
            ).scalar_one()
            == count
        )
        _exercise_evaluation_groups(connection, release, annotation, canary, actor)
        loaded = load_qualification_context(connection, **arguments)
        derived_arguments = {key: value for key, value in arguments.items() if key != "lineage"}
        derived = load_qualification_context(connection, **derived_arguments)
        from labelloop_api.training_group_store import model_input_groups

        recorded = model_input_groups(connection, project_id=project_id, model_id=model_id)
        assert derived.evidence.lineage.training_group_ids == recorded.training
        assert derived.evidence.lineage.reference_group_ids == recorded.references
        assert derived.evidence.lineage.development == loaded.evidence.lineage.development
        assert derived.evidence.lineage.calibration == loaded.evidence.lineage.calibration
        assert replace(derived.evidence, lineage=loaded.evidence.lineage) == loaded.evidence
        require_automatic_acceptance(
            connection,
            **scope,
            evaluation_key=final.run_id,
            qualification=derived,
            scope=context.evidence.scope[0],
        )
        assert load_registry(connection, **scope) == before
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(model_schema.promotion_event)
            ).scalar_one()
            == count
        )
        # Real targets must not reinterpret synthetic/historical populations as
        # original automatic provenance. No truth or legacy event is backfilled.
        with connection.begin_nested() as real_policy_transaction:
            current = load_qualification_policy(connection, **scope)
            targets = replace(current.policy.targets, fixture_only=False)
            append_qualification_policy(
                connection,
                **scope,
                actor_id=actor,
                expected_revision=current.revision,
                policy=replace(current.policy, targets=targets),
            )
            replay = replace(loaded, evidence=replace(loaded.evidence, targets=targets))
            message = "audit sample lacks the exact original acceptance pipeline"
            with pytest.raises(ValueError, match=message):
                load_qualification_context(connection, **arguments)
            with pytest.raises(ValueError, match=message):
                require_automatic_acceptance(
                    connection,
                    **scope,
                    evaluation_key=final.run_id,
                    qualification=replay,
                    scope=replay.evidence.scope[0],
                )
            with pytest.raises(ValueError, match=message):
                change_registry(
                    connection,
                    **scope,
                    actor_id=actor,
                    expected_revision=before.revision,
                    candidate_key=release.release_id,
                    evaluation_key=final.run_id,
                    policy=PromotionPolicy(1, 0.0),
                    qualification=replay,
                )
            assert load_registry(connection, **scope) == before
            assert (
                connection.execute(
                    sa.select(sa.func.count()).select_from(model_schema.promotion_event)
                ).scalar_one()
                == count
            )
            real_policy_transaction.rollback()
        assert set(training_groups) <= set(loaded.evidence.lineage.training_group_ids)
        assert loaded.evidence.annotation_audit != context.evidence.annotation_audit
        assert loaded.evidence.canary_audit != context.evidence.canary_audit
        _exercise_recorded_selection(
            connection, release, final, loaded, arguments, actor, before, count
        )
        from proposal_reference_smoke import exercise_recorded_references

        exercise_recorded_references(
            connection, release, final, loaded, arguments, actor, before, count
        )
        with pytest.raises(ValueError, match="differ from current canonical membership"):
            load_qualification_context(
                connection,
                **(
                    arguments
                    | {
                        "lineage": replace(
                            arguments["lineage"],
                            development=replace(
                                arguments["lineage"].development, group_ids=("invented-dev-group",)
                            ),
                        )
                    }
                ),
            )
        from labelloop_api.schema import similarity_relation

        manifest = connection.execute(
            sa.select(model_schema.training_run.c.input_manifest)
            .join(model_schema.model_release)
            .where(model_schema.model_release.c.id == model_id)
        ).scalar_one()
        training_asset = next(
            sample["asset_id"] for sample in manifest["samples"] if sample["split"] == "train"
        )
        validation_sample = next(
            sample for sample in manifest["samples"] if sample["split"] == "val"
        )
        inputs = model_input_groups(connection, project_id=project_id, model_id=model_id)
        assert inputs.training == training_groups
        assert {
            sample["group_id"] for sample in manifest["samples"] if sample["split"] == "val"
        } <= set(inputs.selection)
        with connection.begin_nested() as transaction:
            # This new persisted candidate-bound canary has valid sampling and clean
            # synthetic measurements, but its group already guided CPU checkpoint selection.
            reused = _outcome(
                connection,
                release,
                context,
                actor,
                canary=True,
                group_id=validation_sample["group_id"],
                split="val",
                suffix="-reused-selection",
            )
            from labelloop_api.annotation_evidence_store import load_annotation_evidence

            stored = load_annotation_evidence(
                connection,
                project_key=release.project_id,
                release_key=reused.release_key,
                audit_key=reused.audit_key,
                evidence_key=reused.evidence_key,
                canary_model_key=release.release_id,
            )
            replay = replace(
                loaded, evidence=replace(loaded.evidence, canary_audit=stored.evidence)
            )
            with pytest.raises(
                ValueError, match="canary groups overlap compiled checkpoint selection"
            ):
                load_qualification_context(connection, **(arguments | {"canary": reused}))
            with pytest.raises(
                ValueError, match="canary groups overlap compiled checkpoint selection"
            ):
                load_qualification_context(connection, **(derived_arguments | {"canary": reused}))
            with pytest.raises(
                ValueError, match="canary groups overlap compiled checkpoint selection"
            ):
                require_automatic_acceptance(
                    connection,
                    **scope,
                    evaluation_key=final.run_id,
                    qualification=replay,
                    scope=context.evidence.scope[0],
                )
            with pytest.raises(
                ValueError, match="canary groups overlap compiled checkpoint selection"
            ):
                change_registry(
                    connection,
                    **scope,
                    actor_id=actor,
                    expected_revision=before.revision,
                    candidate_key=release.release_id,
                    evaluation_key=final.run_id,
                    policy=PromotionPolicy(1, 0.0),
                    qualification=replay,
                )
            assert load_registry(connection, **scope) == before
            assert (
                connection.execute(
                    sa.select(sa.func.count()).select_from(model_schema.promotion_event)
                ).scalar_one()
                == count
            )
            transaction.rollback()
        for scenario in ("omitted", "relation", "validation-relation"):
            with connection.begin_nested() as transaction:
                replay = loaded
                message = "omits compiled training groups"
                if scenario == "omitted":
                    replay = replace(
                        loaded,
                        evidence=replace(
                            loaded.evidence,
                            lineage=replace(
                                loaded.evidence.lineage, training_group_ids=("invented",)
                            ),
                        ),
                    )
                else:
                    keys = (
                        validation_sample["asset_id"]
                        if scenario == "validation-relation"
                        else training_asset,
                        annotation.release_key + "-positive",
                    )
                    identities = {
                        row.asset_key: row.id
                        for row in connection.execute(
                            sa.select(source_asset.c.asset_key, source_asset.c.id).where(
                                source_asset.c.asset_key.in_(keys)
                            )
                        )
                    }
                    connection.execute(
                        sa.insert(similarity_relation).values(
                            left_asset_id=identities[keys[0]],
                            right_asset_id=identities[keys[1]],
                            reason="declared_metadata",
                            evidence="synthetic current training-group conflict",
                            algorithm_version="synthetic-v1",
                        )
                    )
                    message = "merge pinned groups|cross pinned splits"
                with pytest.raises(ValueError, match=message):
                    require_automatic_acceptance(
                        connection,
                        **scope,
                        evaluation_key=final.run_id,
                        qualification=replay,
                        scope=context.evidence.scope[0],
                    )
                with pytest.raises(ValueError, match=message):
                    change_registry(
                        connection,
                        **scope,
                        actor_id=actor,
                        expected_revision=before.revision,
                        candidate_key=release.release_id,
                        evaluation_key=final.run_id,
                        policy=PromotionPolicy(1, 0.0),
                        qualification=replay,
                    )
                assert load_registry(connection, **scope) == before
                assert (
                    connection.execute(
                        sa.select(sa.func.count()).select_from(model_schema.promotion_event)
                    ).scalar_one()
                    == count
                )
                transaction.rollback()

        for dataset_key in (
            loaded.evidence.lineage.development.release_id,
            loaded.current_final_test.dataset.release_id,
        ):
            with connection.begin_nested() as transaction:
                keys = (dataset_key + "-positive", annotation.release_key + "-positive")
                identities = {
                    row.asset_key: row.id
                    for row in connection.execute(
                        sa.select(source_asset.c.asset_key, source_asset.c.id).where(
                            source_asset.c.asset_key.in_(keys)
                        )
                    )
                }
                connection.execute(
                    sa.insert(similarity_relation).values(
                        left_asset_id=identities[keys[0]],
                        right_asset_id=identities[keys[1]],
                        reason="declared_metadata",
                        evidence="synthetic context evaluation-group conflict",
                        algorithm_version="synthetic-v1",
                    )
                )
                with pytest.raises(ValueError, match="cross pinned splits"):
                    load_qualification_context(connection, **arguments)
                # An earlier assembled success cannot authorize either consuming
                # transaction after canonical grouping has changed.
                with pytest.raises(ValueError, match="cross pinned splits"):
                    require_automatic_acceptance(
                        connection,
                        **scope,
                        evaluation_key=final.run_id,
                        qualification=loaded,
                        scope=context.evidence.scope[0],
                    )
                with pytest.raises(ValueError, match="cross pinned splits"):
                    change_registry(
                        connection,
                        **scope,
                        actor_id=actor,
                        expected_revision=before.revision,
                        candidate_key=release.release_id,
                        evaluation_key=final.run_id,
                        policy=PromotionPolicy(1, 0.0),
                        qualification=loaded,
                    )
                assert load_registry(connection, **scope) == before
                assert (
                    connection.execute(
                        sa.select(sa.func.count()).select_from(model_schema.promotion_event)
                    ).scalar_one()
                    == count
                )
                transaction.rollback()
        require_automatic_acceptance(
            connection,
            **scope,
            evaluation_key=final.run_id,
            qualification=loaded,
            scope=context.evidence.scope[0],
        )
        _exercise_annotation_write(connection, release, final, loaded, canary, actor)
        with connection.begin_nested() as transaction:
            # The context was assembled before this new reservation. Neither a new
            # assembly nor replay of that older success may hide the locked identity.
            register_final_test(
                connection,
                dataset=replace(loaded.evidence.lineage.development, role="locked_final_test"),
                actor_id=actor,
            )
            with pytest.raises(ValueError, match="role forbids"):
                load_qualification_context(connection, **arguments)
            with pytest.raises(ValueError, match="role forbids"):
                require_automatic_acceptance(
                    connection,
                    **scope,
                    evaluation_key=final.run_id,
                    qualification=loaded,
                    scope=context.evidence.scope[0],
                )
            with pytest.raises(ValueError, match="role forbids"):
                change_registry(
                    connection,
                    **scope,
                    actor_id=actor,
                    expected_revision=before.revision,
                    candidate_key=release.release_id,
                    evaluation_key=final.run_id,
                    policy=PromotionPolicy(1, 0.0),
                    qualification=loaded,
                )
            assert load_registry(connection, **scope) == before
            assert (
                connection.execute(
                    sa.select(sa.func.count()).select_from(model_schema.promotion_event)
                ).scalar_one()
                == count
            )
            transaction.rollback()
        partial = AnnotationOutcomeRef(
            canary.release_key, canary.audit_key + "-partial", canary.evidence_key + "-partial"
        )
        with pytest.raises(ValueError, match="adverse or missing observations"):
            load_qualification_context(connection, **(arguments | {"canary": partial}))
        # Even a valid, committed model-bound outcome cannot reuse prior audit groups.
        with pytest.raises(ValueError, match="canary groups must be fresh"):
            load_qualification_context(
                connection,
                **(
                    arguments
                    | {
                        "annotation": AnnotationOutcomeRef(
                            canary.release_key,
                            canary.audit_key + "-ordinary",
                            canary.evidence_key + "-ordinary",
                        )
                    }
                ),
            )
        assert load_registry(connection, **scope) == before
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(model_schema.promotion_event)
            ).scalar_one()
            == count
        )
    return {
        "scope": "synthetic canonical assembly/acceptance mechanics, never domain quality or HTTP",
        "reconnected_model_report_policy_final_and_both_measured_outcomes": True,
        "lineage_derived_from_recorded_inputs_and_selection_without_assertion": True,
        "derived_context_passes_acceptance_and_rejects_selection_group_canary": True,
        "canonical_training_reload_rejects_omitted_groups_and_new_relations_in_consumers": True,
        "canonical_evaluation_groups_recover_and_reject_missing_or_conflicting_sources": True,
        "assembly_rejects_declared_group_substitution_and_new_evaluation_relations": True,
        "complete_fresh_canary_passes_and_partial_outcome_fails": True,
        "prior_audit_group_reuse_rejected_with_registry_unchanged": True,
        "locked_development_identity_rejected_by_assembly_acceptance_and_registry": True,
        "positive_and_negative_annotation_writes_consume_current_qualification": True,
        "persisted_pause_prevents_annotation_revision_and_event_append": True,
        "injected_revision_failure_rolls_back_preceding_review_event": True,
    }
