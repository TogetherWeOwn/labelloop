"""Opt-in HTTP -> disposable Actions DB -> queue -> CPU, never qualification.

Reuse the API's restricted session/canonical/worker roles and actual adapters.
Admission and annotation/grouping remain explicit licence-clean fixture setup.
Configuration publication is exercised over HTTP; real admission, complete
bootstrap, quality qualification and default app mounting are not proved.
"""

import base64
import hashlib
import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text

from labelloop_api.main import create_app
from labelloop_api.management_configuration import ConfigurationTrainingBinding
from labelloop_api.management_contracts import AssetIngest, JobAccepted, ProjectCreate, Resource
from labelloop_api.management_evaluation import ApprovedDevelopmentProtocol
from labelloop_api.management_operations import CanonicalManagementOperations
from labelloop_api.management_training import ApprovedTrainingProfile
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


@pytest.fixture
def management_http(database, restricted_auth_store, auth_account):
    service, _, _ = database
    operations = CanonicalManagementOperations(service.engine, service.store, service.workspace_id)

    def configure(**settings):
        nonlocal operations
        # Trusted fixture-server configuration, never an HTTP/request-selected profile.
        operations = replace(operations, **settings)

    origin = "https://testserver"
    with TestClient(create_app(lambda: operations), base_url=origin) as client:
        assert client.get("/projects").status_code == 401
        login = client.post("/auth/login", json=auth_account[1], headers={"origin": origin})
        assert login.status_code == 200, login.text
        headers = {"origin": origin, "x-csrf-token": login.json()["csrf_token"]}
        yield client, headers, configure
        assert client.post("/auth/logout", headers=headers).status_code == 204
        assert client.get("/projects").status_code == 401


def http_command(client, headers, path, body, key, status):
    headers = {**headers, "Idempotency-Key": key}
    denied = client.post(path, json=body, headers={"Idempotency-Key": key})
    assert denied.status_code == 403 and denied.json() == {"detail": "csrf_denied"}
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == status, response.text
    replay = client.post(path, json=body, headers=headers)
    assert replay.status_code == status and replay.json() == response.json()
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def project_detail(client, project_id):
    response = client.get(f"/projects/{project_id}")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def project_command(client, headers, project_id, suffix, body, key, status=200):
    revision = project_detail(client, project_id)["resource"]["revision"]
    return http_command(
        client,
        headers,
        f"/projects/{project_id}/{suffix}",
        {"expected_revision": revision, **body},
        key,
        status,
    )


def save_cpu_setup(client, headers, project_id, fixture, scope):
    project_command(
        client,
        headers,
        project_id,
        "setup/scope",
        {
            "name": "CPU interface fixture",
            "scope": scope,
            "domain": "software-interface",
            "representation": "interface_element",
            "intended_use": "Offline wiring acceptance, never quality qualification",
            "specificity": "Coarse fixture rectangles",
            "source_permitted_use": "Licence-clean checked-in fixture; see fixture manifest",
        },
        "cpu-scope",
    )
    project_command(
        client,
        headers,
        project_id,
        "setup/guide",
        {
            "annotation_guide": "Fixture-only visible extents; not independent gold",
            "extent": "visible",
            "truncation": "Clip to visible source edges",
            "occlusion": "Label visible extents",
            "reflections": "Out of scope",
            "min_target_px": 4.0,
            "exhaustive_for_scope": True,
            "background": "Only verified complete negatives",
        },
        "cpu-guide",
    )
    project_command(
        client,
        headers,
        project_id,
        "setup/sources",
        {"metadata_keys": ["session"], "collection_notes": "Seeded fixture split groups"},
        "cpu-sources",
    )
    detail = project_detail(client, project_id)
    project_command(
        client,
        headers,
        project_id,
        "setup/classes",
        {
            "base_ontology_id": detail["versions"]["ontology_id"],
            "classes": [
                {
                    "stable_id": item.stable_id,
                    "name": item.name,
                    "definition": item.definition,
                    "aliases": [],
                    "parent_id": None,
                    "positive_examples": [],
                    "exclusions": [],
                    "mirror_safe": False,
                }
                for item in fixture.release.ontology.classes
            ],
        },
        "cpu-classes",
    )
    detail = project_detail(client, project_id)
    project_command(
        client,
        headers,
        project_id,
        "setup/classes/publish",
        {
            "base_ontology_id": detail["versions"]["ontology_id"],
            "draft_sha256": detail["setup_draft_sha256"]["classes"],
            "reason": "Preserve fixture class identities",
        },
        "cpu-publish-classes",
    )


def seed_reviewed_fixture(service, owner, client, headers):
    from load_canonical import FixtureDomain
    from test_management_canonical_db import Actor, ingest

    fixture = FixtureDomain("cc0-interface")
    scope = "Coarse visible interface rectangles for fixture wiring only"
    created = Resource.model_validate(
        http_command(
            client,
            headers,
            "/projects",
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
            ).model_dump(mode="json"),
            "cpu-create",
            201,
        )
    )
    save_cpu_setup(client, headers, created.id, fixture, scope)
    with owner.connect() as connection:
        ontology = connection.execute(
            select(ontology_release)
            .where(ontology_release.c.project_id == UUID(created.id))
            .order_by(ontology_release.c.version.desc())
            .limit(1)
        ).one()
    ingest_revision = project_detail(client, created.id)["resource"]["revision"]
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
                expected_revision=ingest_revision + index,
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
    plan = client.get(f"/projects/{created.id}/freeze-plan")
    assert plan.status_code == 200, plan.text
    assert plan.json()["payload"] is None and plan.json()["configuration"] is None
    return created, fixture, source_ids, revisions


def publish_cpu_configuration(client, headers, project_id, fixture, source_ids, configure):
    detail = project_detail(client, project_id)
    entries = {}
    for sample in fixture.release.samples:
        if sample.split != "train" or not sample.revision.objects:
            continue
        class_id = sample.revision.objects[0].class_id
        project_command(
            client,
            headers,
            project_id,
            "references",
            {
                "base_ontology_id": detail["versions"]["ontology_id"],
                "assignment_version": detail["versions"]["assignment"],
                "asset_id": source_ids[sample.asset.asset_id],
                "class_id": class_id,
                "source": "Checked-in interface fixture",
                "permitted_use": "CC0 fixture",
                "view": "full frame",
                "lighting": "synthetic",
                "scale": "source pixels",
                "validity_scope": "Fixture wiring only",
                "confused_with": [],
            },
            f"cpu-reference-{len(entries)}",
            201,
        )
        (reference,) = [
            item
            for item in project_detail(client, project_id)["references"]
            if item["asset_id"] == source_ids[sample.asset.asset_id]
            and item["class_id"] == class_id
        ]
        project_command(
            client,
            headers,
            project_id,
            "references/accept",
            {
                "reference_id": reference["id"],
                "base_ontology_id": detail["versions"]["ontology_id"],
                "assignment_version": detail["versions"]["assignment"],
                "reason": "Accept existing source-bound fixture annotation",
            },
            f"cpu-accept-{len(entries)}",
        )
        entries[reference["id"]] = {
            "annotation_revision": 3,
            "scene": "interface-fixture",
            "cluster_id": sample.group_id,
            "validity_tags": ["fixture-only"],
            "utility": 1.0,
        }
    assert entries
    pack = {
        "pack_id": "cpu-anchors",
        "version": "v1",
        "ontology_id": detail["versions"]["ontology_id"],
        "assignment_version": detail["versions"]["assignment"],
        "entries": entries,
    }
    project_command(
        client,
        headers,
        project_id,
        "reference-packs",
        {"plan": pack, "reason": "Pin fixture human anchors"},
        "cpu-pack",
    )
    summary = client.get(f"/projects/{project_id}/reference-packs/cpu-anchors/versions/v1")
    assert summary.status_code == 200, summary.text
    project_command(
        client,
        headers,
        project_id,
        "setup/approvals",
        {
            "step": "evidence",
            "draft_sha256": summary.json()["snapshot_sha256"],
            "anchor_pack": {k: v for k, v in pack.items() if k != "entries"},
            "reason": "Approve fixture anchors, not independent gold",
        },
        "cpu-approve-evidence",
    )
    for step in ("scope", "classes", "guide", "sources"):
        detail = project_detail(client, project_id)
        project_command(
            client,
            headers,
            project_id,
            "setup/approvals",
            {
                "step": step,
                "draft_sha256": detail["setup_draft_sha256"][step],
                "reason": "Approve fixture setup intent",
            },
            f"cpu-approve-{step}",
        )
    detail = project_detail(client, project_id)
    assert detail["setup_summary_sha256"] is not None
    snapshot = project_command(
        client,
        headers,
        project_id,
        "configuration-snapshots",
        {
            "version": "v1",
            "setup_summary_sha256": detail["setup_summary_sha256"],
            "reason": "Pin local CPU fixture configuration",
        },
        "cpu-snapshot",
    )
    summary = client.get(f"/projects/{project_id}/configuration-snapshots/v1")
    assert summary.status_code == 200, summary.text
    summary = summary.json()
    pin = summary["configuration"]
    assert summary["qualification"] == "not_assessed" and summary["runtime_binding"] is None
    body = {
        "expected_revision": snapshot["revision"],
        "configuration": pin,
        "reason": "Activate controlled local fixture configuration",
    }
    path = f"/projects/{project_id}/local-configuration-releases"
    assert (
        client.post(
            path, json=body, headers={**headers, "Idempotency-Key": "cpu-local"}
        ).status_code
        == 503
    )
    configure(
        configurations=(
            ConfigurationTrainingBinding(UUID(project_id), pin["version"], pin["snapshot_sha256"]),
        )
    )
    assert client.get(f"/projects/{project_id}/freeze-plan").json()["payload"] is None
    publication = http_command(client, headers, path, body, "cpu-local", 200)
    assert publication["state"] == "frozen"
    return pin, summary, publication


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
def test_management_http_runs_real_cpu_project_to_export(
    database, compilation, compiler_worker, management_http, auth_account, tmp_path, side
):
    import torch
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
    client, headers, configure = management_http
    identity, _ = auth_account
    created, fixture, source_ids, revisions = seed_reviewed_fixture(service, owner, client, headers)
    recipe = f"full-letterbox-{side}-v1"
    torch.set_num_threads(2)
    torch.manual_seed(0)
    SETTINGS.update({"sync": False})
    initialization = tmp_path / "initialization.pt"
    YOLO("yolo26n.yaml").save(str(initialization))
    digest = file_digest(initialization)
    compiler_worker.objects.put(content_key(digest), initialization.read_bytes())
    # Trusted server settings for the existing cohort, not requested quality targets.
    profile = ApprovedTrainingProfile(
        UUID(created.id),
        "cpu-baseline",
        1,
        recipe,
        digest,
        inference_policy=InferencePolicy(side, conf=0.005, max_det=1000),
    )
    configure(profiles=(profile,))
    pin, configuration_summary, publication = publish_cpu_configuration(
        client, headers, created.id, fixture, source_ids, configure
    )
    plan = client.get(f"/projects/{created.id}/freeze-plan")
    assert plan.status_code == 200 and plan.headers["cache-control"] == "no-store"
    plan = plan.json()
    assert plan["configuration"] == pin
    payload = plan["payload"]
    assert payload["annotation_revisions"] == revisions
    assert payload["expected_revision"] == publication["revision"]
    assert payload["assignment_version"] == fixture.release.assignment.assignment_version
    release = Resource.model_validate(
        http_command(
            client, headers, f"/projects/{created.id}/dataset-releases", payload, "cpu-freeze", 201
        )
    )
    detail = client.get(f"/dataset-releases/{release.id}")
    assert detail.status_code == 200 and detail.json()["resource"] == release.model_dump(
        mode="json"
    )
    compile_path = f"/dataset-releases/{release.id}/variants"
    compile_body = {"expected_revision": 1, "recipe_id": recipe}
    compiled = JobAccepted.model_validate(
        http_command(client, headers, compile_path, compile_body, "cpu-compile", 202)
    )
    assert client.get(f"/resources/job/{compiled.job_id}").json()["state"] == "queued"
    compiler = compilation_runner(
        compiler_worker.store, compiler_worker.objects, service.workspace_id, job_id=compiled.job_id
    )
    assert compiler.run_once("cpu-compiler") and not compiler.run_once("cpu-compiler")
    compiled_stages = saved_job(owner, compiled)
    manifest = compiled_stages["compilation"]["manifest"]
    assert len(manifest["samples"]) >= 16
    assert {item["asset_id"] for item in manifest["samples"]} == set(source_ids.values())
    assert set(manifest["class_map"]) == set(fixture.release.ontology.class_map)

    profiles = client.get(f"/projects/{created.id}/training-profiles")
    assert profiles.status_code == 200 and profiles.json()["profiles"] == [
        profile.read().model_dump(mode="json")
    ]
    training_path = f"/variants/{compiled.resource.id}/training-runs"
    training_body = {"expected_revision": 1, "training_profile_id": profile.id}
    accepted = JobAccepted.model_validate(
        http_command(client, headers, training_path, training_body, "cpu-training", 202)
    )
    with owner.connect() as connection:
        job_input = connection.execute(
            text("SELECT input FROM worker_job WHERE job_id=:id"), {"id": accepted.job_id}
        ).scalar_one()
        (intent,) = connection.execute(
            text(
                "SELECT actor_id,actor_kind,payload FROM canonical_event "
                "WHERE aggregate_id=:id AND event_type='management.start_training'"
            ),
            {"id": compiled.resource.id},
        ).all()
        (freeze_receipt,) = connection.execute(
            text(
                "SELECT actor_id,actor_kind,payload FROM canonical_event "
                "WHERE aggregate_id=:id AND event_type='management.freeze_release'"
            ),
            {"id": created.id},
        ).all()
    assert job_input == intent.payload["evidence"]["inputs"]
    assert job_input["configuration"] == {
        "project_id": created.id,
        **pin,
        "setup_summary_sha256": configuration_summary["setup_summary_sha256"],
    }
    assert freeze_receipt.payload["evidence"]["configuration"] == pin
    assert intent.actor_id == freeze_receipt.actor_id == identity
    assert intent.actor_kind == freeze_receipt.actor_kind == "human"
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
    configure(protocols=(protocol,))
    evaluation_path = f"/models/{model_id}/evaluations"
    evaluation_body = {
        "expected_revision": 1,
        "dataset_release_id": release.id,
        "protocol_id": protocol.id,
    }
    evaluated = JobAccepted.model_validate(
        http_command(client, headers, evaluation_path, evaluation_body, "cpu-evaluation", 202)
    )
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
    assert all(row.payload["requested_by"] == identity for row in completions)
    assert paths and all(not path.exists() for path in paths)
    for path, body, key, result in (
        (compile_path, compile_body, "cpu-compile", compiled),
        (training_path, training_body, "cpu-training", accepted),
        (evaluation_path, evaluation_body, "cpu-evaluation", evaluated),
    ):
        assert http_command(client, headers, path, body, key, 202) == result.model_dump(mode="json")
        observed = client.get(f"/resources/job/{result.job_id}")
        assert observed.status_code == 200 and observed.json()["state"] == "completed"
        saved_job(owner, result)

    # The same persisted role is reloaded on the next request; reads keep the same
    # historical publication authority, but a reviewer cannot replay admin writes.
    from labelloop_api.auth_schema import principal

    with owner.begin() as connection:
        connection.execute(
            principal.update().where(principal.c.id == identity).values(role="reviewer")
        )
    summary = client.get(f"/projects/{created.id}/configuration-snapshots/v1")
    assert summary.status_code == 200 and summary.json() == configuration_summary
    assert summary.headers["cache-control"] == "no-store"
    assert client.get(f"/projects/{created.id}/freeze-plan").json()["configuration"] == pin
    training_detail = client.get(f"/training-runs/{accepted.resource.id}")
    assert training_detail.status_code == 200, training_detail.text
    assert training_detail.json()["training_profile_id"] == profile.id
    assert training_detail.json()["resource"]["state"] == "completed"
    assert training_detail.json()["variant_id"] == compiled.resource.id
    exported = client.get(f"/models/{model_id}/export")
    assert exported.status_code == 200, exported.text
    assert exported.json() == {
        "model_id": model_id,
        "recipe_id": recipe,
        "weights_sha256": exports[0].weights_sha256,
        "artifact_sha256": exports[0].export_sha256,
        "format": "onnx",
        "input_hw": [side, side],
        "artifact_id": exports[0].export_sha256,
    }
    evaluation_detail = client.get(f"/evaluations/{evaluated.resource.id}")
    assert evaluation_detail.status_code == 200, evaluation_detail.text
    detail = evaluation_detail.json()
    assert detail["report"] == report and detail["qualification"] == "unqualified"
    assert detail["qualification_id"] is None and detail["incumbent_id"] is None
    assert detail["opened_final_test_count"] == 0
    for kind, identifiers in (
        ("variant", {compiled.resource.id}),
        ("training-run", {accepted.resource.id}),
        ("model", {model_id}),
        ("evaluation", {evaluated.resource.id}),
        ("job", {compiled.job_id, accepted.job_id, evaluated.job_id}),
    ):
        page = client.get(f"/projects/{created.id}/resources", params={"kind": kind})
        assert page.status_code == 200, page.text
        assert {item["id"] for item in page.json()["items"]} == identifiers
        assert page.headers["cache-control"] == "no-store"
    forbidden = client.post(
        training_path, json=training_body, headers={**headers, "Idempotency-Key": "cpu-training"}
    )
    assert forbidden.status_code == 403 and forbidden.json() == {"detail": "permission_denied"}
    (tmp_path / "management-pipeline.json").write_text(
        json.dumps(
            {
                "scope": "opt-in-http-disposable-db-real-cpu-not-real-admission-or-qualification",
                "http": {
                    "identity": "restricted-shared-session",
                    "commands": [
                        "create",
                        "setup-drafts",
                        "class-publication",
                        "references",
                        "anchor-pack",
                        "setup-approvals",
                        "configuration-snapshot",
                        "local-publication",
                        "freeze",
                        "compile",
                        "train",
                        "evaluate",
                    ],
                    "csrf_refusals": True,
                    "completed_replays": True,
                    "reviewer_reads": True,
                    "reviewer_mutation_refusal": True,
                    "admission": "independent-canonical-fixture-setup-only",
                    "grouping_and_annotation": "seeded-fixture-not-producer-acceptance",
                    "default_mount": False,
                },
                "configuration": {
                    "pin": pin,
                    "summary": configuration_summary,
                    "local_publication": publication,
                    "freeze_receipt_pin": freeze_receipt.payload["evidence"]["configuration"],
                    "training_input_pin": job_input["configuration"],
                    "unbound_activation_refused": True,
                },
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
