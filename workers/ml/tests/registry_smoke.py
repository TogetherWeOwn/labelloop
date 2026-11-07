"""PostgreSQL integration assertions over real CPU training/export evidence."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from labelloop_api import model_schema as db
from labelloop_api.model_store import (
    change_registry,
    decode_report,
    load_registry,
    record_digest,
    save_model,
    save_training,
)
from labelloop_api.schema import (
    canonical_dataset_release,
    ontology_release,
    preprocessing_recipe,
    project,
    yolo_dataset_variant,
)
from labelloop_core.registry import ModelRelease, PromotionPolicy


def seed_variant(connection, request, manifest):
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
    connection.execute(
        insert(canonical_dataset_release)
        .values(
            release_key=request.dataset_release_id,
            project_id=project_id,
            ontology_id=ontology_id,
            assignment_version=request.split_assignment_version,
            sample_revisions={},
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
            promoted = change_registry(
                connection,
                **common,
                expected_revision=0,
                candidate_key=model.release_id,
                evaluation_key=model.evaluation_run_ids[0],
                policy=gate,
            )
            assert promoted.champion == model
        # A new connection must recover the whole immutable release, not in-memory state.
        with engine.connect() as connection:
            assert load_registry(connection, request.project_id, request.recipe_id) == promoted

        def compete(suffix):
            try:
                with engine.begin() as connection:
                    return change_registry(
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
        assert winner.rollback == model and winner.revision == 2
        with engine.begin() as connection:
            # Inject a failure after the pointer write: the savepoint must undo both effects.
            def fail_event(_conn, _cursor, statement, _parameters, _context, _many):
                if statement.startswith("INSERT INTO promotion_event"):
                    raise RuntimeError("fixture event failure")

            sa.event.listen(connection, "before_cursor_execute", fail_event)
            try:
                with pytest.raises(RuntimeError, match="event failure"):
                    change_registry(connection, **common, expected_revision=2)
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
            restored = change_registry(connection, **common, expected_revision=2)
            assert restored.champion == model and restored.rollback == winner.champion
            assert restored.revision == 3
            assert load_registry(connection, request.project_id, other_recipe) == untouched
            with pytest.raises(ValueError, match="stale"):
                change_registry(connection, **common, expected_revision=2)
            for table in (db.training_run, db.evaluation_run, db.model_release, db.promotion_event):
                with connection.begin_nested() as transaction:
                    with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                        connection.execute(sa.delete(table))
                    transaction.rollback()
        with engine.connect() as connection:
            assert load_registry(connection, request.project_id, request.recipe_id) == restored
        (output / "registry-smoke.json").write_text(
            json.dumps(
                {
                    "promoted": asdict(promoted),
                    "raced": asdict(winner),
                    "restored": asdict(restored),
                    "other_recipe_unchanged": True,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
    finally:
        engine.dispose()
