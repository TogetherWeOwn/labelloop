"""Licence-clean synthetic lineage over the delivered fixture; no training qualification claim."""

from dataclasses import asdict, replace
from pathlib import Path

import sqlalchemy as sa

from labelloop_api import schema as db
from labelloop_api.backup_manifest import digest, object_key
from labelloop_api.model_store import (
    change_registry,
    json_record,
    record_digest,
    save_model,
    save_training,
)
from labelloop_compiler.compiler import compile_dataset
from labelloop_core.evaluation import (
    DerivedPredictions,
    Detection,
    EvaluationImage,
    EvaluationRunner,
    GroundTruth,
)
from labelloop_core.geometry import full_frame_transform
from labelloop_core.registry import ModelRelease, PromotionPolicy
from labelloop_core.training import Checkpoint, InferencePolicy, TrainingRequest, TrainingResult


def seed(connection, store, fixture, output: Path):
    release = fixture.release

    def insert(table, **values):
        return connection.execute(sa.insert(table).values(**values)).inserted_primary_key[0]

    project_id = insert(
        db.project,
        project_key=release.project_id,
        workspace_id="00000000-0000-0000-0000-000000000001",
        task="detect",
        scope="licence-clean restore fixture",
        domain="interface",
    )
    ontology_id = insert(
        db.ontology_release,
        ontology_key=release.ontology.ontology_id,
        project_id=project_id,
        version=1,
        classes=[asdict(c) for c in release.ontology.classes],
        class_map=release.ontology.mapping(),
    )
    asset_ids = {}
    for sample in release.samples:
        asset = sample.asset
        store.put(asset.object_key, fixture.images[asset.asset_id])
        asset_id = insert(
            db.source_asset,
            project_id=project_id,
            asset_key=asset.asset_id,
            sha256=asset.sha256,
            object_key=asset.object_key,
            width=asset.width,
            height=asset.height,
            exif_orientation=asset.exif_orientation,
            source_uri=asset.source_uri,
        )
        asset_ids[asset.asset_id] = asset_id
        insert(
            db.annotation_revision,
            asset_id=asset_id,
            actor_id="fixture-policy",
            **json_record(sample.revision),
        )
        insert(
            db.completeness_assessment,
            asset_id=asset_id,
            revision=1,
            ontology_hash=release.ontology_hash(),
            scope_completeness="exhaustive_for_scope",
            actor_kind="system",
            actor_id="fixture-policy",
            output_truncated=False,
            unresolved_regions=[],
        )
        insert(
            db.review_event,
            asset_id=asset_id,
            actor_kind="system",
            actor_id="fixture-policy",
            decision="fixture-accepted",
            expected_revision=0,
            correction=None,
            reason="recorded fixture acceptance, not operational gold",
        )
        insert(
            db.source_split_assignment,
            asset_id=asset_id,
            assignment_version=release.assignment.assignment_version,
            group_id=sample.group_id,
            split=sample.split,
            seed=release.assignment.seed,
            algorithm=release.assignment.algorithm,
            thresholds={},
            reason=release.assignment.reasons[asset.asset_id],
            locked=True,
        )
    first = next(iter(asset_ids.values()))
    second = list(asset_ids.values())[1]
    insert(
        db.reference_example,
        asset_id=first,
        class_id=release.ontology.classes[0].stable_id,
        crop=None,
        evidence_provenance="CC0 fixture",
        approved=True,
        validity_scope="fixture",
    )
    insert(
        db.similarity_relation,
        left_asset_id=first,
        right_asset_id=second,
        reason="fixture-negative-control",
        evidence="no split mutation",
        algorithm_version="1",
    )
    insert(
        db.borderline_review,
        left_asset_id=first,
        right_asset_id=second,
        metric="fixture",
        value="0.1",
        review_state="pending",
        algorithm_version="1",
    )
    insert(
        db.canonical_event,
        event_type="fixture-freeze",
        aggregate_id=release.release_id,
        expected_revision=0,
        payload={"fixture_only": True},
        actor_kind="system",
        actor_id="fixture-policy",
    )
    release_id = insert(
        db.canonical_dataset_release,
        release_key=release.release_id,
        project_id=project_id,
        ontology_id=ontology_id,
        assignment_version=release.assignment.assignment_version,
        sample_revisions={s.asset.asset_id: s.revision.revision for s in release.samples},
        ontology_hash=release.ontology_hash(),
        excluded_asset_ids={},
        hashes={},
        frozen=True,
    )
    manifests = {}
    for side in (320, 640):
        recipe = f"full-letterbox-{side}-v1"
        directory = output / recipe
        manifest = compile_dataset(release, recipe, directory, fixture.images)
        manifests[recipe] = manifest
        recipe_id = insert(
            db.preprocessing_recipe, recipe_key=recipe, policy=manifest["recipe"], immutable=True
        )
        variant_id = insert(
            db.yolo_dataset_variant,
            release_id=release_id,
            recipe_id=recipe_id,
            class_map=manifest["class_map"],
            file_manifest=manifest,
            validated=True,
            non_empty_val=True,
            platform_mapping={},
        )
        sample = manifest["samples"][0]
        insert(
            db.derived_sample,
            variant_id=variant_id,
            source_id=asset_ids[sample["asset_id"]],
            crop=sample["transform"]["crop"],
            transform=sample["transform"],
            image_sha256=sample["image_sha256"],
            image_key=sample["image_path"],
            label_sha256=sample["label_sha256"],
        )
        # Opaque fixture checkpoint/export bytes exercise custody and hashing, not deserialization.
        weights = f"licence-clean synthetic weights {side}".encode()
        exported = f"licence-clean synthetic static export {side}".encode()
        for data in (weights, exported):
            store.put(object_key(digest(data)), data)
        policy = InferencePolicy(side)
        images = tuple(
            EvaluationImage(
                s.asset.asset_id,
                s.asset.width,
                s.asset.height,
                tuple(GroundTruth(o.object_id, o.class_id, o.box) for o in s.revision.objects),
            )
            for s in release.samples
            if s.split == "val"
        )
        derived = []
        for image in images:
            transform = full_frame_transform(image.width, image.height, side)
            detections = tuple(
                Detection(o.class_id, transform.apply(o.box), 0.9) for o in image.objects
            )
            derived.append(DerivedPredictions(image.asset_id, transform, detections))
        report = EvaluationRunner(tuple(manifest["class_map"]), policy).evaluate(
            run_id=f"eval-{side}",
            dataset_release_id=release.release_id,
            recipe_id=recipe,
            weights_sha256=digest(weights),
            class_map_sha256=digest((directory / "class-map.json").read_bytes()),
            role="development",
            images=images,
            derived=tuple(derived),
        )
        request = TrainingRequest(
            f"fixture-{side}",
            release.project_id,
            recipe,
            release.release_id,
            digest((directory / "manifest.json").read_bytes()),
            report.class_map_sha256,
            release.assignment.assignment_version,
            digest(weights),
            policy,
        )
        checkpoint = Checkpoint(0, digest(weights), report.overall.map50_95, report.run_id)
        result = TrainingResult(
            request,
            (checkpoint,),
            checkpoint,
            request.arguments(),
            "8.4.204",
            "fixture-not-executed",
            False,
        )
        save_training(connection, variant_id=variant_id, result=result, reports=(report,))
        model = ModelRelease(
            f"model-{side}",
            release.project_id,
            recipe,
            digest(weights),
            record_digest(manifest["recipe"]),
            report.class_map_sha256,
            tuple(sorted(manifest["class_map"], key=manifest["class_map"].__getitem__)),
            policy,
            result.ultralytics_version,
            result.torch_version,
            digest(exported),
            "onnx",
            (1, 3, side, side),
            (report.run_id,),
        )
        evidence = {
            "weights_sha256": digest(weights),
            "export_sha256": digest(exported),
            "static_input_signature": list(model.static_input_signature),
            "policy": asdict(policy),
            "sample_count": 1,
            "detection_counts": [1],
            "coordinate_atol_px": 0.01,
            "confidence_atol": 1e-5,
        }
        for item in (model, replace(model, release_id=model.release_id + "-next")):
            save_model(
                connection, training_run_key=request.run_id, release=item, export_evidence=evidence
            )
        for revision, key in enumerate((model.release_id, model.release_id + "-next")):
            change_registry(
                connection,
                project_key=release.project_id,
                recipe_key=recipe,
                actor_id="fixture-admin",
                expected_revision=revision,
                candidate_key=key,
                evaluation_key=report.run_id,
                incumbent_evaluation_key=report.run_id if revision else None,
                policy=PromotionPolicy(1, 0.0),
            )
    return manifests
