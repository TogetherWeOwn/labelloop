"""PostgreSQL integration assertions over real CPU training/export evidence."""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import pytest
import sqlalchemy as sa
from audit_sample_smoke import exercise_audit_sampling
from final_test_smoke import exercise_final_test
from qualification_fixture import qualification_context, synthetic_final
from sqlalchemy.dialects.postgresql import insert

from labelloop_api import model_schema as db
from labelloop_api import qualification_schema
from labelloop_api.annotation_audit_store import (
    lock_project,
    record_annotation_audit,
    require_no_annotation_veto,
)
from labelloop_api.final_test_store import (
    expose_final_test,
    load_final_test,
    record_candidate_selection,
    register_final_test,
)
from labelloop_api.generated_label_store import record_generated_label_origin
from labelloop_api.model_store import (
    change_registry,
    decode_report,
    load_registry,
    load_release,
    record_digest,
    save_evaluation,
    save_model,
    save_training,
    stored_report,
)
from labelloop_api.schema import (
    annotation_revision,
    canonical_dataset_release,
    ontology_release,
    preprocessing_recipe,
    project,
    source_asset,
    source_split_assignment,
    yolo_dataset_variant,
)
from labelloop_core.audit import AuditObservation
from labelloop_core.registry import ModelRelease, PromotionPolicy


def synthetic_final_variant(connection, source_variant_id, dataset_key):
    """Persist independent synthetic membership for journal mechanics, never domain gold."""
    source = (
        connection.execute(
            sa.select(yolo_dataset_variant).where(yolo_dataset_variant.c.id == source_variant_id)
        )
        .mappings()
        .one()
    )
    existing = connection.execute(
        sa.select(yolo_dataset_variant.c.id)
        .join(canonical_dataset_release)
        .where(
            canonical_dataset_release.c.release_key == dataset_key,
            yolo_dataset_variant.c.recipe_id == source["recipe_id"],
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    from evaluation_fixture import independent_evaluation_variant

    return independent_evaluation_variant(connection, source_variant_id, dataset_key)


def fixture_change_registry(connection, **kwargs):
    """Exercise persistence with explicitly SYNTHETIC qualification, not a quality claim.

    The CPU development metrics are copied only to test record/transaction wiring.
    Invented source groups/audits must never be used as real final-test evidence.
    The unchanged production entry point rejects the original missing context.
    """
    candidate_key = kwargs.get("candidate_key")
    if candidate_key is None:
        return change_registry(connection, **kwargs)

    project_id = lock_project(connection, kwargs["project_key"])

    def final_for(model_key, evaluation_key):
        model_id = connection.execute(
            sa.select(db.model_release.c.id).where(db.model_release.c.release_key == model_key)
        ).scalar_one()
        release = load_release(connection, model_id)
        source = stored_report(connection, release, evaluation_key)
        final = synthetic_final(
            source,
            run_id=evaluation_key + "-synthetic-final-" + model_key + "-for-" + candidate_key,
            dataset_id=source.dataset_release_id + "-synthetic-final-" + candidate_key,
        )
        if final.run_id not in release.evaluation_run_ids:
            variant_id = connection.execute(
                sa.select(db.evaluation_run.c.variant_id).where(
                    db.evaluation_run.c.run_key == evaluation_key
                )
            ).scalar_one()
            variant_id = synthetic_final_variant(connection, variant_id, final.dataset_release_id)
            save_evaluation(connection, model_key=model_key, variant_id=variant_id, report=final)
        development = replace(
            source,
            run_id=evaluation_key + "-synthetic-development-" + model_key + "-for-" + candidate_key,
            dataset_release_id=source.dataset_release_id
            + "-synthetic-development-"
            + candidate_key,
        )
        release = load_release(connection, model_id)
        if development.run_id not in release.evaluation_run_ids:
            source_variant_id = connection.execute(
                sa.select(db.evaluation_run.c.variant_id).where(
                    db.evaluation_run.c.run_key == evaluation_key
                )
            ).scalar_one()
            development_variant_id = synthetic_final_variant(
                connection, source_variant_id, development.dataset_release_id
            )
            save_evaluation(
                connection,
                model_key=model_key,
                variant_id=development_variant_id,
                report=development,
            )
        return load_release(connection, model_id), final, development

    from labelloop_api.evaluation_group_store import load_evaluation_set

    candidate, final, development = final_for(candidate_key, kwargs["evaluation_key"])
    kwargs["evaluation_key"] = final.run_id
    state = load_registry(connection, kwargs["project_key"], kwargs["recipe_key"])
    if state.champion and kwargs.get("incumbent_evaluation_key"):
        _, incumbent, _ = final_for(state.champion.release_id, kwargs["incumbent_evaluation_key"])
        kwargs["incumbent_evaluation_key"] = incumbent.run_id
    development_set = load_evaluation_set(
        connection,
        project_key=candidate.project_id,
        dataset_key=development.dataset_release_id,
        role="development",
    )
    selection_key = "fixture-selection-" + final.run_id
    actor = kwargs["actor_id"]
    exists = connection.execute(
        sa.select(qualification_schema.final_test_event.c.revision)
        .where(
            qualification_schema.final_test_event.c.project_id == project_id,
            qualification_schema.final_test_event.c.dataset_key == final.dataset_release_id,
        )
        .limit(1)
    ).scalar_one_or_none()
    if exists is None:
        register_final_test(
            connection,
            actor_id=actor,
            dataset=load_evaluation_set(
                connection,
                project_key=candidate.project_id,
                dataset_key=final.dataset_release_id,
                role="locked_final_test",
            ),
        )
        record_candidate_selection(
            connection,
            project_key=candidate.project_id,
            model_key=candidate_key,
            selection_key=selection_key,
            development_key=development.run_id,
            development_set=development_set,
            actor_id=actor,
        )
        expose_final_test(
            connection,
            project_key=candidate.project_id,
            dataset_key=final.dataset_release_id,
            model_key=candidate_key,
            selection_key=selection_key,
            evaluation_key=final.run_id,
            expected_revision=0,
            actor_id=actor,
        )
    exposed = load_final_test(
        connection, project_key=candidate.project_id, dataset_key=final.dataset_release_id
    )
    from evaluation_fixture import compiled_training_lineage

    context = qualification_context(candidate, final, kwargs["policy"])
    lineage = compiled_training_lineage(connection, candidate, context.evidence.lineage)
    kwargs["qualification"] = replace(
        context,
        current_final_test=exposed,
        evidence=replace(
            context.evidence,
            final_test=exposed,
            selection_id=selection_key,
            lineage=replace(lineage, development=development_set),
        ),
    )
    from qualification_policy_smoke import fixture_policy

    fixture_policy(connection, kwargs["qualification"], actor)
    return change_registry(connection, **kwargs)


def compiled_fixture_members(manifest, release):
    """Keep actual CPU source lineage separate from synthetic audit controls."""
    members = {sample.asset.asset_id: sample for sample in release.samples}
    assert manifest["split_assignment_version"] == release.assignment.assignment_version
    assert manifest["class_map"] == release.ontology.class_map
    assert manifest["samples"]
    for compiled in manifest["samples"]:
        source = members[compiled["asset_id"]]
        assert (
            compiled["revision"],
            compiled["source_sha256"],
            compiled["group_id"],
            compiled["split"],
        ) == (source.revision.revision, source.asset.sha256, source.group_id, source.split)
    return members


def seed_compiled_sources(connection, project_id, manifest):
    from load_canonical import FixtureDomain

    release = FixtureDomain("cc0-interface").release
    members = compiled_fixture_members(manifest, release)
    for key, sample in members.items():
        asset = sample.asset
        connection.execute(
            insert(source_asset)
            .values(
                project_id=project_id,
                asset_key=key,
                sha256=asset.sha256,
                object_key=asset.object_key,
                width=asset.width,
                height=asset.height,
                exif_orientation=asset.exif_orientation,
                source_uri=asset.source_uri,
            )
            .on_conflict_do_nothing()
        )
        asset_id = connection.execute(
            sa.select(source_asset.c.id).where(
                source_asset.c.project_id == project_id, source_asset.c.asset_key == key
            )
        ).scalar_one()
        revision = asdict(sample.revision)
        revision["objects"] = [
            asdict(obj) | {"box": list(asdict(obj.box).values())} for obj in sample.revision.objects
        ]
        connection.execute(
            insert(annotation_revision)
            .values(asset_id=asset_id, actor_id="fixture-canonical-reviewer", **revision)
            .on_conflict_do_nothing()
        )
        connection.execute(
            insert(source_split_assignment)
            .values(
                assignment_version=release.assignment.assignment_version,
                asset_id=asset_id,
                group_id=sample.group_id,
                split=sample.split,
                seed=release.assignment.seed,
                algorithm=release.assignment.algorithm,
                thresholds={},
                reason=release.assignment.reasons[sample.group_id],
                locked=True,
            )
            .on_conflict_do_nothing()
        )
    return {key: sample.revision.revision for key, sample in members.items()}


def seed_variant(connection, request, manifest, *, asset_suffix=""):
    connection.execute(
        insert(project)
        .values(
            project_key=request.project_id,
            workspace_id="00000000-0000-0000-0000-000000000001",
            task="detect",
            scope="licence-clean fixture",
            domain="interface",
        )
        .on_conflict_do_nothing()
    )
    project_id = connection.execute(
        sa.select(project.c.id).where(project.c.project_key == request.project_id)
    ).scalar_one()
    connection.execute(
        insert(ontology_release)
        .values(
            project_id=project_id,
            ontology_key="smoke-ontology",
            version=1,
            classes=[],
            class_map=manifest["class_map"],
        )
        .on_conflict_do_nothing()
    )
    ontology_id = connection.execute(
        sa.select(ontology_release.c.id).where(ontology_release.c.project_id == project_id)
    ).scalar_one()
    membership = seed_compiled_sources(connection, project_id, manifest)
    # Extra synthetic members exercise the veto's conservative whole-release
    # lineage. They are not compiled CPU inputs, actual image labels or domain gold.
    ordinary_membership = {}
    # The paired ordinary sources are correlated; unrelated lineage-control
    # sources must not claim the same pixel digest as the audited population.
    source_digest = hashlib.sha256((request.project_id + asset_suffix).encode()).hexdigest()
    for kind in ("positive", "negative"):
        asset_key = f"{request.project_id}-synthetic-ordinary-{kind}{asset_suffix}"
        connection.execute(
            insert(source_asset)
            .values(
                project_id=project_id,
                asset_key=asset_key,
                sha256=source_digest,
                object_key=f"sha256/{source_digest[:2]}/{source_digest}",
                width=32,
                height=32,
                exif_orientation=1,
                source_uri="fixture://synthetic-audit",
            )
            .on_conflict_do_nothing()
        )
        asset_id = connection.execute(
            sa.select(source_asset.c.id).where(source_asset.c.asset_key == asset_key)
        ).scalar_one()
        connection.execute(
            insert(annotation_revision)
            .values(
                asset_id=asset_id,
                revision=1,
                actor_kind="system",
                actor_id="fixture-original-reviewer",
                objects=[
                    {
                        "object_id": "one",
                        "class_id": next(iter(manifest["class_map"])),
                        "box": [0, 0, 8, 8],
                    }
                ]
                if kind == "positive"
                else [],
                approval_state="auto_accepted",
                review_tier="machine_verified_no_human_review",
                scope_completeness="exhaustive_for_scope",
                training_eligible=True,
                gold_eligible=False,
            )
            .on_conflict_do_nothing()
        )
        ordinary_membership[asset_key] = 1
    # The tiny audit population remains explicitly synthetic and separate from
    # the CPU sources; its exact revisions also appear in training-release lineage.
    for key, revisions in (
        (request.dataset_release_id, membership | ordinary_membership),
        (request.dataset_release_id + "-ordinary-audit", ordinary_membership),
    ):
        connection.execute(
            insert(canonical_dataset_release)
            .values(
                release_key=key,
                project_id=project_id,
                ontology_id=ontology_id,
                assignment_version=request.split_assignment_version,
                sample_revisions=revisions,
                ontology_hash="a" * 64,
                excluded_asset_ids=[],
                hashes={},
                frozen=True,
            )
            .on_conflict_do_nothing()
        )
    release_id = connection.execute(
        sa.select(canonical_dataset_release.c.id).where(
            canonical_dataset_release.c.release_key == request.dataset_release_id
        )
    ).scalar_one()
    connection.execute(
        insert(preprocessing_recipe)
        .values(
            recipe_key=request.recipe_id,
            policy=manifest["recipe"],
            immutable=True,
        )
        .on_conflict_do_nothing()
    )
    recipe_id = connection.execute(
        sa.select(preprocessing_recipe.c.id).where(
            preprocessing_recipe.c.recipe_key == request.recipe_id
        )
    ).scalar_one()
    return connection.execute(
        sa.insert(yolo_dataset_variant).values(
            release_id=release_id,
            recipe_id=recipe_id,
            class_map=manifest["class_map"],
            file_manifest=manifest,
            validated=True,
            non_empty_val=True,
        )
    ).inserted_primary_key[0]


def exercise_registry(result, exported, manifest: dict, output: Path) -> None:
    engine = sa.create_engine(os.environ["DATABASE_URL"])
    request = result.request
    reports = tuple(
        decode_report(json.loads((output / f"evaluation-epoch-{c.epoch}.json").read_text()))
        for c in result.checkpoints
    )
    model = ModelRelease(
        release_id=f"model-{request.run_id}",
        project_id=request.project_id,
        recipe_id=request.recipe_id,
        weights_sha256=result.selected.sha256,
        preprocessing_sha256=record_digest(manifest["recipe"]),
        class_map_sha256=request.class_map_sha256,
        class_ids=tuple(sorted(manifest["class_map"], key=manifest["class_map"].__getitem__)),
        policy=request.policy,
        ultralytics_version=result.ultralytics_version,
        torch_version=result.torch_version,
        export_sha256=exported.export_sha256,
        export_format="onnx",
        static_input_signature=exported.static_input_signature,
        evaluation_run_ids=(result.selected.evaluation_run_id,),
    )
    other_recipe = f"full-letterbox-{640 if request.policy.imgsz == 320 else 320}-v1"
    gate = PromotionPolicy(1, 0.0)  # Fixture wiring only, never a production qualification target.
    common = dict(
        project_key=request.project_id, recipe_key=request.recipe_id, actor_id="fixture-admin"
    )
    try:
        with engine.begin() as connection:
            variant_id = seed_variant(connection, request, manifest)
            untouched = load_registry(connection, request.project_id, other_recipe)
            save_training(connection, variant_id=variant_id, result=result, reports=reports)
            for statement in (
                sa.update(preprocessing_recipe)
                .where(preprocessing_recipe.c.recipe_key == request.recipe_id)
                .values(policy=manifest["recipe"] | {"padding_color": [0, 0, 0]}),
                sa.update(preprocessing_recipe)
                .where(preprocessing_recipe.c.recipe_key == request.recipe_id)
                .values(immutable=False),
                sa.update(yolo_dataset_variant)
                .where(yolo_dataset_variant.c.id == variant_id)
                .values(file_manifest={}),
            ):
                with connection.begin_nested() as transaction:
                    with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                        connection.execute(statement)
                    transaction.rollback()
            with pytest.raises(ValueError, match="training lineage"):
                save_model(
                    connection,
                    training_run_key=request.run_id,
                    release=replace(model, preprocessing_sha256="f" * 64),
                    export_evidence=asdict(exported),
                )
            with pytest.raises(sa.exc.IntegrityError):
                save_training(
                    connection,
                    variant_id=variant_id,
                    result=replace(
                        result, request=replace(request, run_id=request.run_id + "-duplicate")
                    ),
                    reports=reports,
                )
            assert (
                connection.execute(
                    sa.select(sa.func.count())
                    .select_from(db.training_run)
                    .where(db.training_run.c.run_key == request.run_id + "-duplicate")
                ).scalar_one()
                == 0
            )
            with pytest.raises(ValueError, match="lineage mismatch"):
                save_training(
                    connection,
                    variant_id=variant_id,
                    result=result,
                    reports=(replace(reports[0], role="locked_final_test"),),
                )
            with pytest.raises(ValueError, match="export verification"):
                save_model(
                    connection, training_run_key=request.run_id, release=model, export_evidence={}
                )
            for counts in (
                (0,) * exported.sample_count,
                (-1,) + (1,) * (exported.sample_count - 1),
                (1.5,) * exported.sample_count,
            ):
                with pytest.raises(ValueError, match="export verification"):
                    save_model(
                        connection,
                        training_run_key=request.run_id,
                        release=model,
                        export_evidence=asdict(exported) | {"detection_counts": counts},
                    )
                assert (
                    connection.execute(
                        sa.select(sa.func.count())
                        .select_from(db.model_release)
                        .where(db.model_release.c.release_key == model.release_id)
                    ).scalar_one()
                    == 0
                )
            for item in (
                model,
                replace(model, release_id=model.release_id + "-next"),
                replace(model, release_id=model.release_id + "-racer"),
            ):
                save_model(
                    connection,
                    training_run_key=request.run_id,
                    release=item,
                    export_evidence=asdict(exported),
                )
            with pytest.raises(ValueError, match="trusted qualification"):
                change_registry(
                    connection,
                    **common,
                    expected_revision=0,
                    candidate_key=model.release_id,
                    evaluation_key=model.evaluation_run_ids[0],
                    policy=gate,
                )
            assert load_registry(connection, request.project_id, request.recipe_id).champion is None
            promoted = fixture_change_registry(
                connection,
                **common,
                expected_revision=0,
                candidate_key=model.release_id,
                evaluation_key=model.evaluation_run_ids[0],
                policy=gate,
            )
            assert replace(promoted.champion, evaluation_run_ids=model.evaluation_run_ids) == model
            initial_model = model  # Immutable initial payload, before append-only evaluation links.
            model = promoted.champion
        # A new connection must recover the whole release and actual compiled groups.
        from labelloop_api.training_group_store import model_training_groups

        with engine.begin() as connection:
            assert load_registry(connection, request.project_id, request.recipe_id) == promoted
            project_id = lock_project(connection, request.project_id)
            model_id = connection.execute(
                sa.select(db.model_release.c.id).where(
                    db.model_release.c.release_key == model.release_id
                )
            ).scalar_one()
            assert model_training_groups(
                connection, project_id=project_id, model_id=model_id
            ) == tuple(
                sorted({row["group_id"] for row in manifest["samples"] if row["split"] == "train"})
            )

        def compete(suffix):
            try:
                with engine.begin() as connection:
                    return fixture_change_registry(
                        connection,
                        **common,
                        expected_revision=1,
                        candidate_key=model.release_id + suffix,
                        evaluation_key=model.evaluation_run_ids[0],
                        incumbent_evaluation_key=model.evaluation_run_ids[0],
                        policy=gate,
                    )
            except ValueError as error:
                assert "stale" in str(error)
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(compete, ("-next", "-racer")))
        assert sum(item is not None for item in results) == 1
        winner = next(item for item in results if item is not None)
        assert winner.revision == 2
        assert replace(winner.rollback, evaluation_run_ids=model.evaluation_run_ids) == model
        model = winner.rollback  # Includes the new common-final incumbent comparison link.
        with engine.begin() as connection:
            # Inject a failure after the pointer write: the savepoint must undo both effects.
            def fail_event(_conn, _cursor, statement, _parameters, _context, _many):
                if statement.startswith("INSERT INTO promotion_event"):
                    raise RuntimeError("fixture event failure")

            sa.event.listen(connection, "before_cursor_execute", fail_event)
            try:
                with pytest.raises(RuntimeError, match="event failure"):
                    fixture_change_registry(connection, **common, expected_revision=2)
            finally:
                sa.event.remove(connection, "before_cursor_execute", fail_event)
            assert load_registry(connection, request.project_id, request.recipe_id) == winner
            assert (
                connection.execute(
                    sa.select(sa.func.count())
                    .select_from(db.promotion_event)
                    .where(
                        db.promotion_event.c.recipe_id
                        == connection.execute(
                            sa.select(preprocessing_recipe.c.id).where(
                                preprocessing_recipe.c.recipe_key == request.recipe_id
                            )
                        ).scalar_one()
                    )
                ).scalar_one()
                == 2
            )
            restored = fixture_change_registry(connection, **common, expected_revision=2)
            assert restored.champion == model and restored.rollback == winner.champion
            assert restored.revision == 3
            assert load_registry(connection, request.project_id, other_recipe) == untouched
            with pytest.raises(ValueError, match="stale"):
                fixture_change_registry(connection, **common, expected_revision=2)
            for table in (db.training_run, db.evaluation_run, db.model_release, db.promotion_event):
                with connection.begin_nested() as transaction:
                    with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                        connection.execute(sa.delete(table))
                    transaction.rollback()
        with engine.connect() as connection:
            assert load_registry(connection, request.project_id, request.recipe_id) == restored
        with engine.begin() as connection:
            common_evaluation = exercise_common_evaluation(
                connection, result, exported, manifest, initial_model, reports[0], common
            )
            assert load_registry(connection, request.project_id, other_recipe) == untouched
            seed_checkpoint_descendants(
                connection, result, exported, manifest, initial_model, reports[0]
            )
        final_test_journal = exercise_final_test(
            engine,
            result,
            manifest,
            initial_model,
            next(item for item in reports if item.run_id == result.selected.evaluation_run_id),
            seed_variant,
        )
        from registry_http_smoke import exercise_http_audit_veto, exercise_http_rollback

        http_rollback = exercise_http_rollback(engine, request)
        audit_veto = None
        http_audit_veto = None
        # The recipes share a canonical training release. Finish both smoke cases
        # before inserting its durable adverse evidence; 640 runs second in Actions.
        if request.policy.imgsz == 640:
            audit_veto = exercise_annotation_veto(engine, request, common, gate)
            http_audit_veto = exercise_http_audit_veto(engine, request)
        (output / "registry-smoke.json").write_text(
            json.dumps(
                {
                    "promoted": asdict(promoted),
                    "raced": asdict(winner),
                    "restored": asdict(restored),
                    "other_recipe_unchanged": True,
                    "common_evaluation": common_evaluation,
                    "durable_annotation_veto": audit_veto,
                    "final_test_journal": final_test_journal,
                    "http_rollback": http_rollback,
                    "http_durable_annotation_veto": http_audit_veto,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
    finally:
        engine.dispose()


def exercise_common_evaluation(connection, result, exported, manifest, model, report, common):
    """Synthetic persistence regression, not a second actual training/inference claim."""
    request = result.request
    next_manifest = manifest | {"release_id": request.dataset_release_id + "-synthetic-r2"}
    next_request = replace(
        request,
        run_id=request.run_id + "-synthetic-r2",
        dataset_release_id=next_manifest["release_id"],
        dataset_manifest_sha256=hashlib.sha256(
            (json.dumps(next_manifest, sort_keys=True, indent=2) + "\n").encode()
        ).hexdigest(),
    )
    checkpoint = replace(result.selected, evaluation_run_id=next_request.run_id + "-eval")
    next_result = replace(
        result, request=next_request, checkpoints=(checkpoint,), selected=checkpoint
    )
    next_report = replace(
        report,
        run_id=checkpoint.evaluation_run_id,
        dataset_release_id=next_request.dataset_release_id,
    )
    next_variant = seed_variant(connection, next_request, next_manifest)
    save_training(connection, variant_id=next_variant, result=next_result, reports=(next_report,))
    challenger = replace(
        model,
        release_id=model.release_id + "-synthetic-r2",
        evaluation_run_ids=(next_report.run_id,),
    )
    save_model(
        connection,
        training_run_key=next_request.run_id,
        release=challenger,
        export_evidence=asdict(exported),
    )
    gate = PromotionPolicy(1, 0.0)
    with pytest.raises(ValueError, match="frozen evaluation protocol"):
        fixture_change_registry(
            connection,
            **common,
            expected_revision=3,
            candidate_key=challenger.release_id,
            evaluation_key=next_report.run_id,
            incumbent_evaluation_key=report.run_id,
            policy=gate,
        )
    shared_manifest = manifest | {"release_id": request.dataset_release_id + "-common-development"}
    shared_variant = seed_variant(
        connection,
        replace(request, dataset_release_id=shared_manifest["release_id"]),
        shared_manifest,
    )
    incumbent_shared = replace(
        report,
        run_id=request.run_id + "-incumbent-common",
        dataset_release_id=shared_manifest["release_id"],
    )
    challenger_shared = replace(
        report,
        run_id=request.run_id + "-challenger-common",
        dataset_release_id=shared_manifest["release_id"],
    )
    with pytest.raises(ValueError, match="independent evaluation"):
        save_evaluation(
            connection,
            model_key=model.release_id,
            variant_id=shared_variant,
            report=replace(incumbent_shared, weights_sha256="f" * 64),
        )
    save_evaluation(
        connection, model_key=model.release_id, variant_id=shared_variant, report=incumbent_shared
    )
    save_evaluation(
        connection,
        model_key=challenger.release_id,
        variant_id=shared_variant,
        report=challenger_shared,
    )
    initial_payload = connection.execute(
        sa.select(db.model_release.c.record).where(
            db.model_release.c.release_key == model.release_id
        )
    ).scalar_one()
    assert initial_payload["evaluation_run_ids"] == list(model.evaluation_run_ids)
    selected = fixture_change_registry(
        connection,
        **common,
        expected_revision=3,
        candidate_key=challenger.release_id,
        evaluation_key=challenger_shared.run_id,
        incumbent_evaluation_key=incumbent_shared.run_id,
        policy=gate,
    )
    assert selected.champion.release_id == challenger.release_id and selected.revision == 4
    restored = fixture_change_registry(connection, **common, expected_revision=4)
    assert restored.champion.release_id == model.release_id and restored.revision == 5
    assert incumbent_shared.run_id in restored.champion.evaluation_run_ids
    return {
        "scope": (
            "synthetic retraining lineage and shared-development persistence only; "
            "no second actual training claim"
        ),
        "selected": asdict(selected),
        "restored": asdict(restored),
    }


def seed_checkpoint_descendants(connection, result, exported, manifest, model, report):
    """Persist synthetic clean-data descendants; no extra training/export claim."""
    clean_manifest = manifest | {"release_id": result.request.dataset_release_id + "-lineage-clean"}
    clean_request = replace(
        result.request,
        dataset_release_id=clean_manifest["release_id"],
        dataset_manifest_sha256=hashlib.sha256(
            (json.dumps(clean_manifest, sort_keys=True, indent=2) + "\n").encode()
        ).hexdigest(),
    )
    variant_id = seed_variant(
        connection, clean_request, clean_manifest, asset_suffix="-lineage-clean"
    )
    teacher_manifest = manifest | {
        "release_id": result.request.dataset_release_id + "-teacher-clean"
    }
    teacher_request = replace(
        result.request,
        dataset_release_id=teacher_manifest["release_id"],
        dataset_manifest_sha256=hashlib.sha256(
            (json.dumps(teacher_manifest, sort_keys=True, indent=2) + "\n").encode()
        ).hexdigest(),
    )
    teacher_variant = seed_variant(
        connection, teacher_request, teacher_manifest, asset_suffix="-teacher-clean"
    )
    parent = result.selected.sha256
    for generation in ("middle", "child", "unrelated-control", "teacher-child"):
        run_key = result.request.run_id + "-lineage-" + generation
        weights = hashlib.sha256(run_key.encode()).hexdigest()
        request = replace(
            teacher_request if generation == "teacher-child" else clean_request,
            run_id=run_key,
            initialization_sha256=result.request.initialization_sha256
            if generation in {"unrelated-control", "teacher-child"}
            else parent,
        )
        run_variant = teacher_variant if generation == "teacher-child" else variant_id
        checkpoint = replace(result.selected, sha256=weights, evaluation_run_id=run_key + "-eval")
        synthetic_result = replace(
            result, request=request, checkpoints=(checkpoint,), selected=checkpoint
        )
        synthetic_report = replace(
            report,
            run_id=checkpoint.evaluation_run_id,
            dataset_release_id=request.dataset_release_id,
            weights_sha256=weights,
        )
        save_training(
            connection, variant_id=run_variant, result=synthetic_result, reports=(synthetic_report,)
        )
        parent = weights
        if generation == "middle":
            continue  # No model release: ancestry must traverse raw training checkpoints.
        release = replace(
            model,
            release_id=run_key,
            weights_sha256=weights,
            evaluation_run_ids=(synthetic_report.run_id,),
        )
        save_model(
            connection,
            training_run_key=run_key,
            release=release,
            export_evidence=asdict(exported) | {"weights_sha256": weights},
        )
        if generation == "teacher-child":
            for kind in ("positive", "negative"):
                record_generated_label_origin(
                    connection,
                    project_key=request.project_id,
                    asset_key=f"{request.project_id}-synthetic-ordinary-{kind}-teacher-clean",
                    revision=1,
                    teacher_model_key=model.release_id,
                    actor_id="fixture-inference-worker",
                )
        project_id = lock_project(connection, request.project_id)
        model_id = connection.execute(
            sa.select(db.model_release.c.id).where(db.model_release.c.release_key == run_key)
        ).scalar_one()
        require_no_annotation_veto(connection, project_id=project_id, model_id=model_id)


def exercise_annotation_veto(engine, request, common, gate):
    """Adverse ordinary labels veto actual persisted heads, not just a pure verdict."""
    from teacher_origin_smoke import exercise_teacher_origins

    teacher_origins = exercise_teacher_origins(engine, request)
    audit_request = replace(
        request, dataset_release_id=request.dataset_release_id + "-ordinary-audit"
    )
    sampling = exercise_audit_sampling(engine, audit_request)
    actor = "fixture-independent-auditor"
    positive = f"{request.project_id}-synthetic-ordinary-positive"
    negative = f"{request.project_id}-synthetic-ordinary-negative"
    ordinary_wrong = AuditObservation(positive, 1, actor, True, False, True)
    missed_object = AuditObservation(negative, 1, actor, True, True, False)
    audit_args = dict(
        project_key=request.project_id,
        release_key=audit_request.dataset_release_id,
        actor_id=actor,
        sample_key=sampling["sample_id"],
    )
    with engine.begin() as connection:
        for bad, message in (
            (replace(ordinary_wrong, revision=2), "stale source revision"),
            (replace(ordinary_wrong, asset_id="foreign-asset"), "outside the frozen sample"),
            (replace(ordinary_wrong, reviewer_id="forged-principal"), "authenticated principal"),
        ):
            with pytest.raises(ValueError, match=message):
                record_annotation_audit(
                    connection, **audit_args, audit_key="rejected", observations=(bad,)
                )
        with pytest.raises(ValueError, match="independent"):
            record_annotation_audit(
                connection,
                **(audit_args | {"actor_id": "fixture-original-reviewer"}),
                audit_key="dependent",
                observations=(replace(ordinary_wrong, reviewer_id="fixture-original-reviewer"),),
            )
    from registry_http_smoke import exercise_http_audit_write

    actor = exercise_http_audit_write(
        engine, audit_request, sampling["sample_id"], positive, negative
    )
    ordinary_wrong = replace(ordinary_wrong, reviewer_id=actor)
    missed_object = replace(missed_object, reviewer_id=actor)
    audit_args["actor_id"] = actor
    # A new connection and a new clean sample cannot erase either historical veto.
    with engine.begin() as connection:
        record_annotation_audit(
            connection,
            **audit_args,
            audit_key="replacement-clean-sample",
            observations=(
                replace(ordinary_wrong, labels_correct=True),
                replace(missed_object, scope_complete=True),
            ),
        )
    with engine.begin() as connection:
        before_count = connection.execute(
            sa.select(sa.func.count()).select_from(db.promotion_event)
        ).scalar_one()
        audited = (
            connection.execute(
                sa.select(qualification_schema.annotation_audit).where(
                    qualification_schema.annotation_audit.c.audit_key == "wrong-identity"
                )
            )
            .mappings()
            .one()
        )
        assert audited["actor_id"] == actor
        assert audited["vetoed_revisions"] == {positive: 1}
        binding = audited["record"]["sample"]
        assert binding["sample_key"] == sampling["sample_id"]
        assert binding["population_sha256"] == sampling["population_sha256"]
        assert binding["verdict"]["blocking_asset_ids"] == [positive]
        assert binding["verdict"]["pending_asset_ids"] == [negative]
        assert not binding["verdict"]["passed"]
        for statement in (
            sa.delete(qualification_schema.annotation_audit),
            sa.update(qualification_schema.annotation_audit).values(vetoed_revisions={}),
        ):
            with connection.begin_nested() as transaction:
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.execute(statement)
                transaction.rollback()
        # Both recipes and models trained from a separately frozen descendant release
        # are checked against their actual training membership, not evaluation inputs.
        for side in (320, 640):
            recipe = f"full-letterbox-{side}-v1"
            if load_registry(connection, request.project_id, recipe).champion is None:
                continue  # Also permits focused reproduction of the 640 case alone.
            scope = common | {"recipe_key": recipe}
            candidates = connection.execute(
                sa.select(db.model_release.c.id, db.model_release.c.release_key).where(
                    db.model_release.c.project_id
                    == connection.execute(
                        sa.select(project.c.id).where(project.c.project_key == request.project_id)
                    ).scalar_one(),
                    db.model_release.c.recipe_id
                    == connection.execute(
                        sa.select(preprocessing_recipe.c.id).where(
                            preprocessing_recipe.c.recipe_key == recipe
                        )
                    ).scalar_one(),
                )
            ).all()
            qualified = []
            development_only = 0
            clean_descendants = 0
            teacher_descendants = 0
            unaffected_controls = 0
            project_id = lock_project(connection, request.project_id)
            for candidate in candidates:
                if candidate.release_key.endswith("-lineage-unrelated-control"):
                    require_no_annotation_veto(
                        connection, project_id=project_id, model_id=candidate.id
                    )
                    unaffected_controls += 1
                    continue
                if candidate.release_key.endswith(("-lineage-child", "-lineage-teacher-child")):
                    membership = connection.execute(
                        sa.select(canonical_dataset_release.c.sample_revisions)
                        .select_from(
                            db.model_release.join(db.training_run)
                            .join(yolo_dataset_variant)
                            .join(canonical_dataset_release)
                        )
                        .where(db.model_release.c.id == candidate.id)
                    ).scalar_one()
                    assert not {positive, negative} & set(membership)
                    if candidate.release_key.endswith("-lineage-teacher-child"):
                        teacher_descendants += 1
                    else:
                        clean_descendants += 1
                release = load_release(connection, candidate.id)
                development_only += len(release.evaluation_run_ids) == 1
                report = stored_report(connection, release, release.evaluation_run_ids[0])
                # The losing racer has no final link. Never rename its development ID:
                # append separate, explicitly synthetic evidence for every candidate.
                final = synthetic_final(
                    report,
                    run_id=release.release_id + "-veto-synthetic-final",
                    dataset_id=report.dataset_release_id,
                )
                variant_id = connection.execute(
                    sa.select(db.evaluation_run.c.variant_id).where(
                        db.evaluation_run.c.run_key == report.run_id
                    )
                ).scalar_one()
                save_evaluation(
                    connection,
                    model_key=release.release_id,
                    variant_id=variant_id,
                    report=final,
                )
                release = load_release(connection, candidate.id)
                context = qualification_context(release, final, gate)
                context.require_qualified(release, final)
                qualified.append((release, final, context))
            assert development_only >= 1  # Regression: the unpromoted racer is still covered.
            assert clean_descendants == teacher_descendants == unaffected_controls == 1
            # Evaluation links are append-only model metadata. Snapshot only after
            # fixture preparation, then prove the rejected operations change nothing.
            before = load_registry(connection, request.project_id, recipe)
            for release, final, context in qualified:
                with pytest.raises(ValueError, match="annotation audit veto"):
                    change_registry(
                        connection,
                        **scope,
                        expected_revision=before.revision,
                        candidate_key=release.release_id,
                        evaluation_key=final.run_id,
                        policy=gate,
                        qualification=context,
                    )
            with pytest.raises(ValueError, match="annotation audit veto"):
                change_registry(connection, **scope, expected_revision=before.revision)
            assert load_registry(connection, request.project_id, recipe) == before
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(db.promotion_event)
            ).scalar_one()
            == before_count
        )
    return {
        "scope": "synthetic ordinary-label audit mechanics, not domain quality",
        "canonical_sampling": sampling,
        "generated_label_provenance": teacher_origins,
        "clean_data_teacher_descendants_vetoed_both_recipes": True,
        "wrong_identity_and_missed_object_persisted": True,
        "clean_replacement_sample_did_not_clear_veto": True,
        "clean_data_checkpoint_descendants_vetoed_both_recipes": True,
        "unrelated_clean_data_controls_not_vetoed": True,
        "heads_and_event_count_unchanged": True,
    }
