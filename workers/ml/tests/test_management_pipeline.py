"""Opt-in HTTP -> disposable Actions DB -> queue -> CPU, never qualification.

Reuse the API's restricted session/canonical/worker roles and actual adapters.
Admission and annotations remain explicit licence-clean fixture setup.
Initial grouping and configuration publication are exercised over HTTP; real
admission, complete bootstrap, quality and default app mounting are not proved.
"""

import base64
import hashlib
import io
import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
import urllib3
from fastapi.testclient import TestClient
from minio import Minio
from minio.error import S3Error
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.exc import DBAPIError

from labelloop_api.main import create_app
from labelloop_api.management_configuration import ConfigurationTrainingBinding
from labelloop_api.management_contracts import AssetIngest, JobAccepted, ProjectCreate, Resource
from labelloop_api.management_evaluation import ApprovedDevelopmentProtocol
from labelloop_api.management_objects import BoundedMinioObjects
from labelloop_api.management_runtime import api_operations, compiler_dispatch, local_ml_dispatch
from labelloop_api.management_training import ApprovedTrainingProfile
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
def pipeline_objects(tmp_path):
    assert os.environ.get("LABELLOOP_MANAGEMENT_MINIO_TESTS") == "1", (
        "CPU pipeline requires the disposable Actions MinIO service"
    )
    pool = urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=20), retries=False)
    # Fixed loopback fixture only; never consume deployment endpoints or credentials.
    client = Minio(
        "127.0.0.1:9000",
        access_key="labelloop-local-only",
        secret_key="labelloop-local-secret",
        secure=False,
        region="us-east-1",
        http_client=pool,
    )
    bucket = "pipeline-" + uuid4().hex
    client.make_bucket(bucket)
    try:
        yield BoundedMinioObjects(client, bucket, 64 * 1024 * 1024)
    finally:
        try:
            for item in client.list_objects(bucket, recursive=True):
                client.remove_object(bucket, item.object_name)
            client.remove_bucket(bucket)
            assert not client.bucket_exists(bucket)
            (tmp_path / "minio-cleanup.json").write_text(
                json.dumps({"scope": "disposable-actions-fixture", "bucket_removed": True})
            )
        finally:
            pool.clear()


@pytest.mark.parametrize("length", [0, 8, 1024 * 1024 + 1])
def test_live_minio_content_round_trip_at_transport_limit(pipeline_objects, length):
    from labelloop_api.management_compile_worker import content_key

    data = b"x" * length
    key = content_key(hashlib.sha256(data).hexdigest())
    transport = replace(pipeline_objects, max_object_bytes=max(1, length))
    transport.put(key, data)
    assert transport.read(key) == data
    assert pipeline_objects.client.stat_object(pipeline_objects.bucket, key).content_type == (
        "application/octet-stream"
    )


@pytest.mark.parametrize("data", [b"wrong", b"123456789"])
def test_live_minio_corrupt_or_oversized_content_refuses(pipeline_objects, data):
    from labelloop_api.management_compile_worker import content_key

    key = content_key(hashlib.sha256(b"1234").hexdigest())
    pipeline_objects.client.put_object(
        pipeline_objects.bucket, key, io.BytesIO(data), length=len(data)
    )
    transport = replace(pipeline_objects, max_object_bytes=8)
    reason = "byte limit" if len(data) > 8 else "digest mismatch"
    with pytest.raises(ValueError, match=reason):
        transport.read(key)
    # A refused response must not prevent the same client from reading intact content.
    transport.put(key, b"1234")
    assert transport.read(key) == b"1234"


def test_live_minio_missing_content_refuses(pipeline_objects):
    from labelloop_api.management_compile_worker import content_key

    with pytest.raises(S3Error) as error:
        pipeline_objects.read(content_key(hashlib.sha256(b"missing").hexdigest()))
    assert error.value.code == "NoSuchKey"


@pytest.fixture
def pipeline_worker(database, compilation, pipeline_objects):
    from labelloop_worker_core.jobs import JobStore

    service, owner, api_role = database
    role = "pipeline_worker_" + uuid4().hex
    with owner.begin() as connection:
        connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
        connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        connection.execute(
            text(
                f"GRANT SELECT ON project, ontology_release, source_asset, "
                f"annotation_revision, completeness_assessment, source_split_assignment, "
                f"similarity_relation, borderline_review, reference_example, "
                f'canonical_dataset_release, worker_job TO "{role}"'
            )
        )
        connection.execute(
            text(
                f"GRANT SELECT, INSERT ON canonical_event, worker_stage_artifact, "
                f"worker_application, preprocessing_recipe, yolo_dataset_variant, "
                f"derived_sample, training_run, evaluation_run, model_release, "
                f'model_evaluation TO "{role}"'
            )
        )
        connection.execute(text(f'GRANT UPDATE ON worker_job TO "{role}"'))
        connection.execute(
            text(
                f"GRANT SELECT ON preprocessing_recipe, yolo_dataset_variant, derived_sample, "
                f"worker_stage_artifact, worker_application, training_run, evaluation_run, "
                f'model_release, model_evaluation TO "{api_role}"'
            )
        )
    options = f"-c role={role} -c statement_timeout=15000 -c lock_timeout=10000"
    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"options": options})
    store = JobStore(
        lambda: psycopg.connect(os.environ["DATABASE_URL"].replace("+psycopg", ""), options=options)
    )
    try:
        yield SimpleNamespace(engine=engine, store=store, objects=pipeline_objects, role=role)
    finally:
        engine.dispose()
        with owner.begin() as connection:
            connection.execute(text(f'DROP OWNED BY "{role}"'))
            connection.execute(text(f'DROP ROLE "{role}"'))


def assert_runtime_separation(service, worker, auth_store, api_role):
    with service.engine.connect() as connection:
        assert connection.scalar(text("SELECT current_user")) == api_role
    with worker.engine.connect() as connection:
        assert connection.scalar(text("SELECT current_user")) == worker.role
    with worker.store.connect() as connection:
        assert connection.execute("SELECT current_user").fetchone()[0] == worker.role
    with auth_store.engine.connect() as connection:
        auth_role = connection.scalar(text("SELECT current_user"))
    assert len({api_role, worker.role, auth_role}) == 3
    refusals = (
        (service.engine, "UPDATE worker_job SET status=status WHERE false"),
        *(
            (service.engine, f"INSERT INTO {table} SELECT * FROM {table} WHERE false")
            for table in (
                "worker_stage_artifact",
                "worker_application",
                "preprocessing_recipe",
                "yolo_dataset_variant",
                "derived_sample",
                "training_run",
                "evaluation_run",
                "model_release",
                "model_evaluation",
            )
        ),
        *(
            (worker.engine, f"INSERT INTO {table} SELECT * FROM {table} WHERE false")
            for table in (
                "project",
                "ontology_release",
                "source_asset",
                "annotation_revision",
                "completeness_assessment",
                "source_split_assignment",
                "similarity_relation",
                "borderline_review",
                "reference_example",
                "canonical_dataset_release",
                "worker_job",
            )
        ),
        (worker.engine, "UPDATE reference_example SET approved=approved WHERE false"),
        (worker.engine, "UPDATE project SET access_policy=access_policy WHERE false"),
        (worker.engine, "UPDATE source_asset SET source_uri=source_uri WHERE false"),
        (worker.engine, "UPDATE annotation_revision SET actor_kind=actor_kind WHERE false"),
        (worker.engine, "UPDATE source_split_assignment SET split=split WHERE false"),
        (worker.engine, "SELECT * FROM auth_principal WHERE false"),
        (service.engine, "SELECT * FROM auth_principal WHERE false"),
        *(
            (engine, statement)
            for engine in (service.engine, worker.engine)
            for statement in (
                "UPDATE canonical_event SET actor_kind=actor_kind WHERE false",
                "DELETE FROM canonical_event WHERE false",
            )
        ),
    )
    for engine, statement in refusals:
        with pytest.raises(DBAPIError) as error, engine.begin() as connection:
            connection.execute(text(statement))
        assert error.value.orig.sqlstate == "42501", statement
    return {"distinct_auth_api_worker_roles": True, "privilege_refusals": len(refusals)}


def runtime_object_settings(objects, workspace_id):
    return {
        "client": objects.client,
        "bucket": objects.bucket,
        "max_object_bytes": objects.max_object_bytes,
        "workspace_id": workspace_id,
    }


@pytest.fixture
def management_http(database, restricted_auth_store, auth_account, pipeline_objects):
    service, _, _ = database
    settings = runtime_object_settings(pipeline_objects, service.workspace_id)
    operations = api_operations(engine=service.engine, **settings)
    assert not hasattr(operations.grouping_objects, "put")

    def configure(**updates):
        nonlocal operations
        # Trusted fixture-server configuration, never an HTTP/request-selected profile.
        settings.update(updates)
        operations = api_operations(engine=service.engine, **settings)

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
        {"metadata_keys": ["session"], "collection_notes": "Declared fixture capture groups"},
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


def publish_cpu_grouping(service, owner, client, headers, project_id, revisions, identity):
    from test_management_canonical_db import initial_grouping_snapshot

    before = initial_grouping_snapshot(owner, project_id)
    assert before[:2] == ([], [])
    assert not any(row.event_type == "management.publish_initial_grouping" for row in before[2])
    detail = project_detail(client, project_id)
    body = {
        "expected_revision": detail["resource"]["revision"],
        "ontology_id": detail["versions"]["ontology_id"],
        "source_policy_sha256": detail["setup_draft_sha256"]["sources"],
        "annotation_revisions": revisions,
    }
    path = f"/projects/{project_id}/source-assignments/initial"
    grouped = http_command(client, headers, path, body, "cpu-grouping", 200)
    assignments, relations, receipts, _ = initial_grouping_snapshot(owner, project_id)
    assert len(assignments) == len(revisions) and all(row.locked for row in assignments)
    assert len({row.group_id for row in assignments}) == 15
    assert {row.split for row in assignments} == {"train", "val"}
    assert relations
    (receipt,) = [
        row for row in receipts if row.event_type == "management.publish_initial_grouping"
    ]
    assert receipt.actor_id == identity and receipt.actor_kind == "human"
    evidence = receipt.payload["evidence"]
    assert evidence["annotation_revisions"] == revisions
    assert evidence["source_policy_sha256"] == body["source_policy_sha256"]
    assert {row["asset_id"] for row in evidence["sources"]} == set(revisions)
    assert {row.assignment_version for row in assignments} == {evidence["assignment_version"]}
    with service.engine.connect() as connection:
        rows = connection.execute(
            select(source_asset.c.asset_key, assignment_table)
            .join(assignment_table, assignment_table.c.asset_id == source_asset.c.id)
            .where(source_asset.c.project_id == UUID(project_id))
        ).all()
    by_asset = {row.asset_key: {"group_id": row.group_id, "split": row.split} for row in rows}
    assert set(by_asset) == set(revisions)
    plan = client.get(f"/projects/{project_id}/freeze-plan")
    assert plan.status_code == 200 and plan.json()["payload"] is None
    # Grouping cannot clear draft setup by itself. Publication remains mandatory.
    refused = client.post(
        f"/projects/{project_id}/dataset-releases",
        json={
            "expected_revision": grouped["revision"],
            "ontology_id": body["ontology_id"],
            "assignment_version": evidence["assignment_version"],
            "annotation_revisions": revisions,
        },
        headers={**headers, "Idempotency-Key": "cpu-before-publication"},
    )
    assert refused.status_code == 422 and refused.json() == {"detail": "missing_evidence"}
    assert initial_grouping_snapshot(owner, project_id)[:2] == (assignments, relations)
    assert project_detail(client, project_id)["resource"]["revision"] == grouped["revision"]
    return evidence, by_asset


def publish_cpu_configuration(client, headers, project_id, fixture, source_ids, groups, configure):
    detail = project_detail(client, project_id)
    entries = {}
    for sample in fixture.release.samples:
        if (
            groups[source_ids[sample.asset.asset_id]]["split"] != "train"
            or not sample.revision.objects
        ):
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
            "cluster_id": groups[source_ids[sample.asset.asset_id]]["group_id"],
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
    database, pipeline_worker, management_http, restricted_auth_store, auth_account, tmp_path, side
):
    import torch
    from ultralytics import YOLO
    from ultralytics.utils import SETTINGS

    from labelloop_api.management_compile_worker import content_key
    from labelloop_worker_ml.export import export_and_compare
    from labelloop_worker_ml.training import LocalUltralyticsBackend, file_digest, predict_samples

    service, owner, role = database
    service = replace(service, store=pipeline_worker.objects)
    role_evidence = assert_runtime_separation(
        service, pipeline_worker, restricted_auth_store[0], role
    )
    client, headers, configure = management_http
    identity, _ = auth_account
    created, fixture, source_ids, revisions = seed_reviewed_fixture(service, owner, client, headers)
    grouping_evidence, groups = publish_cpu_grouping(
        service, owner, client, headers, created.id, revisions, identity
    )
    recipe = f"full-letterbox-{side}-v1"
    torch.set_num_threads(2)
    torch.manual_seed(0)
    SETTINGS.update({"sync": False})
    initialization = tmp_path / "initialization.pt"
    YOLO("yolo26n.yaml").save(str(initialization))
    digest = file_digest(initialization)
    pipeline_worker.objects.put(content_key(digest), initialization.read_bytes())
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
        client, headers, created.id, fixture, source_ids, groups, configure
    )
    plan = client.get(f"/projects/{created.id}/freeze-plan")
    assert plan.status_code == 200 and plan.headers["cache-control"] == "no-store"
    plan = plan.json()
    assert plan["configuration"] == pin
    payload = plan["payload"]
    assert payload["annotation_revisions"] == revisions
    assert payload["expected_revision"] == publication["revision"]
    assert payload["assignment_version"] == grouping_evidence["assignment_version"]
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
    compiler = compiler_dispatch(
        store=pipeline_worker.store,
        **runtime_object_settings(pipeline_worker.objects, service.workspace_id),
    )
    assert compiler.run_once(compiled.job_id, "cpu-compiler")
    assert not compiler.run_once(compiled.job_id, "cpu-compiler")
    compiled_stages = saved_job(owner, compiled)
    manifest = compiled_stages["compilation"]["manifest"]
    assert len(manifest["samples"]) >= 16
    assert {item["asset_id"] for item in manifest["samples"]} == set(source_ids.values())
    assert set(manifest["class_map"]) == set(fixture.release.ontology.class_map)
    assert manifest["split_assignment_version"] == grouping_evidence["assignment_version"]
    for sample in manifest["samples"]:
        assert sample["split"] == groups[sample["asset_id"]]["split"]
        assert sample["group_id"] == groups[sample["asset_id"]]["group_id"]

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

    ml = local_ml_dispatch(
        store=pipeline_worker.store,
        engine=pipeline_worker.engine,
        backend=backend,
        exporter=exporter,
        predictor=predict_samples,
        **runtime_object_settings(pipeline_worker.objects, service.workspace_id),
    )
    with pytest.raises(ValueError, match="dispatch lane"):
        ml.run_once(compiled.job_id, "cpu-trainer")
    with pytest.raises(ValueError, match="dispatch lane"):
        compiler.run_once(accepted.job_id, "cpu-compiler")
    assert ml.run_once(accepted.job_id, "cpu-trainer")
    assert not ml.run_once(accepted.job_id, "cpu-trainer")
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
    exported_bytes = pipeline_worker.objects.read(trained["export"]["export_key"])
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
    assert ml.run_once(evaluated.job_id, "cpu-evaluator")
    assert not ml.run_once(evaluated.job_id, "cpu-evaluator")
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
    expected_validation = {key for key, row in groups.items() if row["split"] == "val"}
    assert {row["asset_id"] for row in report["coverage"]} == expected_validation
    assert all(row["covered_fraction"] == 1 for row in report["coverage"])
    assert report["overall"]["support"] == sum(
        len(sample.revision.objects)
        for sample in fixture.release.samples
        if source_ids[sample.asset.asset_id] in expected_validation
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
    assert (
        assert_runtime_separation(service, pipeline_worker, restricted_auth_store[0], role)
        == role_evidence
    )
    objects = pipeline_worker.objects
    stored = list(objects.client.list_objects(objects.bucket, recursive=True))
    assert len(stored) > len(source_ids)
    assert trained["export"]["export_key"] in {item.object_name for item in stored}
    assert not (database[0].store.root / "sha256").exists()
    for item in stored:
        assert objects.client.stat_object(objects.bucket, item.object_name).content_type == (
            "application/octet-stream"
        )
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
                        "initial-source-grouping",
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
                    "annotation": "seeded-fixture-not-producer-acceptance",
                    "grouping": "http-initial-publication-from-canonical-rasters",
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
                "grouping": {
                    "assignment_version": grouping_evidence["assignment_version"],
                    "source_policy_sha256": grouping_evidence["source_policy_sha256"],
                    "phash_algorithm": grouping_evidence["phash_algorithm"],
                    "toolchain": grouping_evidence["toolchain"],
                    "assignments": groups,
                    "no_seeded_assignments_or_relations": True,
                    "freeze_before_configuration_refused": True,
                },
                "runtime_composition": {
                    "module": "management_runtime",
                    "api_grouping_read_only_port": True,
                    "asset_admission": "unavailable",
                    "connections": "caller-owned-restricted-fixtures",
                    "default_mount_or_startup": False,
                },
                "dispatch": {
                    "selection": "persisted-kind-and-owned-human-intent",
                    "specific_job_only": True,
                    "compiler_ml_lane_refusals": True,
                    "automatic_startup": False,
                },
                "object_transport": {
                    "adapter": "BoundedMinioObjects",
                    "service": "disposable-checksum-pinned-minio",
                    "max_object_bytes": objects.max_object_bytes,
                    "stored_object_count": len(stored),
                    "all_content_types_binary": True,
                    "no_filesystem_content_fallback": True,
                    "provisioning": "fixture-root-not-production-iam",
                },
                "runtime_roles": {
                    **role_evidence,
                    "api": "canonical-human-commands-and-enqueue-no-worker-publication",
                    "worker": "fenced-execution-no-enqueue-or-human-annotation-writes",
                    "provisioning": "disposable-owner-created-nologin-fixtures-only",
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
