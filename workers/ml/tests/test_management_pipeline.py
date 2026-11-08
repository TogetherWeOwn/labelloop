"""Disposable Actions DB -> durable queue -> actual CPU adapters, never qualification.

Reuse the API's restricted-role fixtures, not another store or runner. Annotation
and grouping evidence is explicitly seeded from licence-clean fixture metadata;
this does not prove setup publication, real admission or a mounted HTTP pipeline.
"""

import base64
import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, text

from labelloop_api.management_contracts import AssetIngest, ProjectCreate, ReleaseFreeze
from labelloop_api.management_evaluation import (
    ApprovedDevelopmentProtocol,
    CanonicalManagementEvaluation,
)
from labelloop_api.management_training import ApprovedTrainingProfile, CanonicalManagementTraining
from labelloop_api.management_workers import (
    compilation_runner,
    local_evaluation_runner,
    local_training_runner,
)
from labelloop_api.model_store import load_release
from labelloop_api.revision_store import append_review, completeness_context_hash
from labelloop_api.schema import completeness_assessment, ontology_release, source_asset
from labelloop_api.schema import source_split_assignment as assignment_table
from labelloop_core.training import InferencePolicy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/api/tests"))
sys.path.insert(0, str(ROOT / "fixtures"))
pytest_plugins = ("test_management_canonical_db",)
pytestmark = pytest.mark.skipif(
    os.environ.get("LABELLOOP_ML_SMOKE") != "1" or not os.environ.get("DATABASE_URL"),
    reason="requires the Actions CPU ML environment and migrated disposable Postgres",
)


def seed_reviewed_fixture(service, owner):
    from load_canonical import FixtureDomain
    from test_management_canonical_db import Actor, create, freeze, ingest

    fixture = FixtureDomain("cc0-interface")
    scope = "Coarse visible interface rectangles for fixture wiring only"
    created = create(
        service,
        ProjectCreate(
            project_key="cpu-fixture-" + uuid4().hex,
            scope=scope,
            domain="software-interface",
            annotation_guide="Fixture-only visible extents; not independent gold",
            source_permitted_use="Licence-clean checked-in fixture; see fixture manifest",
            classes=[
                {"stable_id": item.stable_id, "name": item.name, "definition": item.definition}
                for item in fixture.release.ontology.classes
            ],
        ),
    )
    with owner.connect() as connection:
        ontology = connection.execute(
            select(ontology_release).where(ontology_release.c.project_id == UUID(created.id))
        ).one()
    assignment = fixture.release.assignment
    revisions, source_ids = {}, {}
    for index, sample in enumerate(fixture.release.samples):
        old_id = sample.asset.asset_id
        # Feed original fixture bytes through the real orientation-normalizing ingest.
        original = ROOT / "fixtures/cc0-interface/images" / sample.asset.source_uri
        asset = ingest(
            service,
            created.id,
            AssetIngest(
                expected_revision=1 + index,
                original_base64=base64.b64encode(original.read_bytes()).decode(),
                source_uri="fixture:" + original.name,
                source_group=sample.group_id,
            ),
            key=f"source-{index}",
        )
        source_ids[old_id] = asset.id
        with owner.begin() as connection:
            source = connection.execute(
                select(source_asset).where(source_asset.c.asset_key == asset.id)
            ).one()
            assert source.sha256 == sample.asset.sha256
            assert (source.width, source.height) == (sample.asset.width, sample.asset.height)
            connection.execute(
                insert(assignment_table).values(
                    assignment_version=assignment.assignment_version,
                    asset_id=source.id,
                    group_id=sample.group_id,
                    split=sample.split,
                    seed=assignment.seed,
                    algorithm=assignment.algorithm,
                    thresholds={"phash_max_distance": 8, "embed_min_cosine": 0.95},
                    reason="Validated fixture grouping, not a management grouping producer",
                    locked=True,
                )
            )
            objects = [
                {
                    "object_id": obj.object_id,
                    "class_id": obj.class_id,
                    "box": [obj.box.x_min, obj.box.y_min, obj.box.x_max, obj.box.y_max],
                    "occluded": obj.occluded,
                    "truncated_at_image_edge": obj.truncated_at_image_edge,
                }
                for obj in sample.revision.objects
            ]
            # Fixture arrangement only: establish completeness for the imported object set,
            # then exercise the delivered human review/eligibility transaction unchanged.
            append_review(
                connection,
                asset_id=asset.id,
                actor_kind="human",
                actor_id=Actor().id,
                expected_revision=1,
                approval_state="proposed",
                review_tier="human_reviewed",
                scope_completeness="partial",
                objects=objects,
            )
            connection.execute(
                insert(completeness_assessment).values(
                    asset_id=source.id,
                    revision=2,
                    ontology_hash=completeness_context_hash(
                        ontology.class_map, ontology.classes, scope
                    ),
                    scope_completeness="exhaustive_for_scope",
                    actor_kind="human",
                    actor_id=Actor().id,
                    output_truncated=False,
                    unresolved_regions=[],
                )
            )
            revisions[asset.id] = append_review(
                connection,
                asset_id=asset.id,
                actor_kind="human",
                actor_id=Actor().id,
                expected_revision=2,
                approval_state="human_accepted",
                review_tier="human_reviewed",
                scope_completeness="exhaustive_for_scope",
                objects=objects,
                training_eligible=True,
            )
    release = freeze(
        service,
        created.id,
        ReleaseFreeze(
            expected_revision=1 + len(revisions),
            ontology_id=ontology.ontology_key,
            assignment_version=assignment.assignment_version,
            annotation_revisions=revisions,
        ),
    )
    return created, release, fixture, source_ids


def saved_job(owner, accepted):
    with owner.connect() as connection:
        job = connection.execute(
            text("SELECT status, generation, error_code FROM worker_job WHERE job_id=:id"),
            {"id": accepted.job_id},
        ).one()
        stages = dict(
            connection.execute(
                text("SELECT stage,payload FROM worker_stage_artifact WHERE job_id=:id"),
                {"id": accepted.job_id},
            ).all()
        )
        applications = connection.execute(
            text("SELECT count(*) FROM worker_application WHERE job_id=:id"),
            {"id": accepted.job_id},
        ).scalar_one()
    assert job.status == "succeeded", (job, stages.keys())
    assert job.generation == applications == 1
    return stages


@pytest.mark.parametrize("side", [320, 640])
def test_management_queue_runs_real_cpu_project_to_export(
    database, compilation, compiler_worker, tmp_path, side
):
    import torch
    from test_management_canonical_db import (
        Actor,
        compile_request,
        evaluation_request,
        training_request,
    )
    from ultralytics import YOLO
    from ultralytics.utils import SETTINGS

    from labelloop_api.management_compile_worker import content_key
    from labelloop_worker_ml.export import export_and_compare
    from labelloop_worker_ml.training import LocalUltralyticsBackend, file_digest, predict_samples

    service, owner, role = database
    with owner.begin() as connection:
        connection.execute(
            text(
                f"GRANT SELECT, INSERT ON training_run, evaluation_run, "
                f'model_release, model_evaluation TO "{role}"'
            )
        )
    created, release, fixture, source_ids = seed_reviewed_fixture(service, owner)
    recipe = f"full-letterbox-{side}-v1"
    compiled = compile_request(compilation, release.id, recipe=recipe)
    compiler = compilation_runner(
        compiler_worker.store, compiler_worker.objects, service.workspace_id, job_id=compiled.job_id
    )
    assert compiler.run_once("cpu-compiler") and not compiler.run_once("cpu-compiler")
    compiled_stages = saved_job(owner, compiled)
    manifest = compiled_stages["compilation"]["manifest"]
    assert len(manifest["samples"]) >= 16
    assert {item["asset_id"] for item in manifest["samples"]} == set(source_ids.values())
    assert set(manifest["class_map"]) == set(fixture.release.ontology.class_map)

    torch.set_num_threads(2)
    torch.manual_seed(0)
    SETTINGS.update({"sync": False})
    initialization = tmp_path / "initialization.pt"
    YOLO("yolo26n.yaml").save(str(initialization))
    digest = file_digest(initialization)
    compiler_worker.objects.put(content_key(digest), initialization.read_bytes())
    # The existing seeded CPU fixture uses this fixed coarse-head cohort. These are
    # trusted server profile settings, not request/model-selected quality thresholds.
    profile = ApprovedTrainingProfile(
        UUID(created.id),
        "cpu-baseline",
        1,
        recipe,
        digest,
        inference_policy=InferencePolicy(side, conf=0.005, max_det=1000),
    )
    training = CanonicalManagementTraining(service, compilation.enqueue, (profile,))
    accepted = training_request(training, compiled.resource.id, profile=profile.id)
    paths, exports = [], []

    def backend(**kwargs):
        paths.extend([kwargs["dataset"], kwargs["initialization"], kwargs["output"]])
        return LocalUltralyticsBackend(**kwargs)

    def exporter(**kwargs):
        paths.extend([kwargs["weights"], *kwargs["images"]])
        evidence = export_and_compare(**kwargs)
        (tmp_path / "export-equivalence.json").write_bytes(kwargs["evidence"].read_bytes())
        exports.append(evidence)
        return asdict(evidence)

    runner = local_training_runner(
        compiler_worker.store,
        service.engine,
        compiler_worker.objects,
        service.workspace_id,
        backend,
        exporter,
        job_id=accepted.job_id,
    )
    assert runner.run_once("cpu-trainer") and not runner.run_once("cpu-trainer")
    trained = saved_job(owner, accepted)
    assert set(trained) == {"training", "export"}
    assert len(exports) == 1
    result = trained["training"]["result"]
    assert result["request"]["policy"] == asdict(profile.inference_policy)
    assert result["selected"]["sha256"] == exports[0].weights_sha256
    assert exports[0].static_input_signature == (1, 3, side, side)
    assert all(
        0 < count < profile.inference_policy.max_det for count in exports[0].detection_counts
    )
    exported_bytes = compiler_worker.objects.read(trained["export"]["export_key"])
    assert hashlib.sha256(exported_bytes).hexdigest() == exports[0].export_sha256
    for kind in ("tensor_shapes", "target_retention", "reports"):
        (tmp_path / f"{kind}.json").write_text(json.dumps(trained["training"][kind]))
    model_id = "model-" + accepted.resource.id
    with service.engine.connect() as connection:
        model_uuid = connection.execute(
            text("SELECT id FROM model_release WHERE release_key=:id"), {"id": model_id}
        ).scalar_one()
        model = load_release(connection, model_uuid)
        assert model.weights_sha256 == result["selected"]["sha256"]
        assert model.recipe_id == recipe and model.policy.imgsz == side
        assert model.export_sha256 == exports[0].export_sha256

    protocol = ApprovedDevelopmentProtocol(
        UUID(created.id), "cpu-validation", 1, UUID(compiled.resource.id)
    )
    evaluation = CanonicalManagementEvaluation(service, compilation.enqueue, (protocol,))
    evaluated = evaluation_request(evaluation, model_id, release.id, protocol=protocol.id)
    evaluator = local_evaluation_runner(
        compiler_worker.store,
        service.engine,
        compiler_worker.objects,
        service.workspace_id,
        predict_samples,
        job_id=evaluated.job_id,
    )
    assert evaluator.run_once("cpu-evaluator") and not evaluator.run_once("cpu-evaluator")
    evaluated_stages = saved_job(owner, evaluated)
    report = evaluated_stages["evaluation"]["report"]
    selected_report = next(
        item
        for item in trained["training"]["reports"]
        if item["run_id"] == result["selected"]["evaluation_run_id"]
    )
    assert report["overall"] == selected_report["overall"]
    assert report["slices"] == selected_report["slices"]
    assert report["role"] == "development" and report["weights_sha256"] == model.weights_sha256
    expected_validation = {
        source_ids[sample.asset.asset_id]
        for sample in fixture.release.samples
        if sample.split == "val"
    }
    assert {row["asset_id"] for row in report["coverage"]} == expected_validation
    assert all(row["covered_fraction"] == 1 for row in report["coverage"])
    assert report["overall"]["support"] == sum(
        len(sample.revision.objects) for sample in fixture.release.samples if sample.split == "val"
    )
    with service.engine.connect() as connection:
        linked = load_release(connection, model_uuid)
        assert evaluated.resource.id in linked.evaluation_run_ids
        persisted = connection.execute(
            text("SELECT record FROM evaluation_run WHERE run_key=:id"),
            {"id": evaluated.resource.id},
        ).scalar_one()
        assert persisted == report
        events = connection.execute(
            text(
                "SELECT event_type,actor_kind,actor_id,payload FROM canonical_event "
                "WHERE aggregate_id IN (:variant,:training,:evaluation)"
            ),
            {
                "variant": compiled.resource.id,
                "training": accepted.resource.id,
                "evaluation": evaluated.resource.id,
            },
        ).all()
    completions = [row for row in events if row.actor_kind == "system"]
    assert {row.actor_id for row in completions} == {"cpu-compiler", "cpu-trainer", "cpu-evaluator"}
    assert all(row.payload["requested_by"] == Actor().id for row in completions)
    assert paths and all(not path.exists() for path in paths)
    assert training_request(training, compiled.resource.id, profile=profile.id) == accepted
    assert evaluation_request(evaluation, model_id, release.id, protocol=protocol.id) == evaluated
    (tmp_path / "management-pipeline.json").write_text(
        json.dumps(
            {
                "scope": "disposable-db-real-cpu-not-qualification-or-http-admission",
                "recipe": recipe,
                "project_id": created.id,
                "dataset_release_id": release.id,
                "source_count": len(source_ids),
                "validation_source_count": len(expected_validation),
                "jobs": [compiled.job_id, accepted.job_id, evaluated.job_id],
                "model": asdict(linked),
                "report": report,
                "export": asdict(exports[0]),
            }
        )
    )
