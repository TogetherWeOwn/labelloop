"""Fixture-only facade checks; these do not prove database/queue/auth integration."""

import json
from dataclasses import dataclass

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from labelloop_api.management import management_router
from labelloop_api.management_codegen import (
    check_published,
    contract_app,
    generate,
    render_client,
    typescript_type,
)
from labelloop_api.management_contracts import (
    ExportRead,
    JobAccepted,
    ManagementError,
    ProjectCreate,
    PromotionResult,
    Resource,
    ResourcePage,
)
from labelloop_api.management_ports import ManagementFailure, MutationContext

PROJECT = {
    "project_key": "wildlife",
    "scope": "Birds on source images",
    "domain": "wildlife",
    "annotation_guide": "Label visible extent",
    "source_permitted_use": "CC0 fixture",
    "classes": [{"stable_id": "bird", "name": "Bird", "definition": "Visible bird"}],
}
CATALOG = {
    "expected_revision": 1,
    "base_ontology_id": "ontology-1",
    "classes": [
        {
            "stable_id": "bird",
            "name": "Bird",
            "definition": "A visible bird",
            "aliases": ["Avian"],
            "parent_id": None,
            "positive_examples": ["Perched bird"],
            "exclusions": ["Printed depictions"],
            "mirror_safe": False,
        }
    ],
}
MUTATIONS = [
    ("/projects", "create_project", PROJECT, 201),
    (
        "/projects/p/assets",
        "ingest_asset",
        {"expected_revision": 1, "original_base64": "aW1hZ2U=", "source_group": "session-a"},
        201,
    ),
    (
        "/projects/p/dataset-releases",
        "freeze_release",
        {
            "expected_revision": 1,
            "ontology_id": "ontology",
            "assignment_version": "split-1",
            "annotation_revisions": {"source-a": 3},
        },
        201,
    ),
    (
        "/dataset-releases/r/variants",
        "compile_variant",
        {"expected_revision": 1, "recipe_id": "full-letterbox-320-v1"},
        202,
    ),
    (
        "/variants/v/training-runs",
        "start_training",
        {"expected_revision": 1, "training_profile_id": "cpu-smoke"},
        202,
    ),
    (
        "/models/m/evaluations",
        "start_evaluation",
        {"expected_revision": 1, "dataset_release_id": "gold", "protocol_id": "source-frame"},
        202,
    ),
    (
        "/projects/p/promotions",
        "promote",
        {
            "expected_revision": 1,
            "recipe_id": "full-letterbox-640-v1",
            "candidate_id": "candidate",
            "qualification_id": "qualification",
            "expected_incumbent_id": None,
            "reason": "Fixture decision",
        },
        200,
    ),
    (
        "/projects/p/setup/scope",
        "save_scope_draft",
        {
            "expected_revision": 1,
            "name": "Wildlife",
            "scope": "Birds on source images",
            "domain": "wildlife",
            "representation": "scene_object",
            "intended_use": "Fixture detection",
            "specificity": "Species",
            "source_permitted_use": "CC0 fixture",
        },
        200,
    ),
    (
        "/projects/p/setup/guide",
        "save_guide_draft",
        {
            "expected_revision": 1,
            "annotation_guide": "Visible birds only",
            "extent": "visible",
            "truncation": "Clip to image",
            "occlusion": "Visible extent only",
            "reflections": "Out of scope",
            "min_target_px": 4.0,
            "exhaustive_for_scope": True,
            "background": "Out-of-scope objects only",
        },
        200,
    ),
    ("/projects/p/setup/classes", "save_class_catalog_draft", CATALOG, 200),
    (
        "/projects/p/setup/sources",
        "save_source_policy_draft",
        {
            "expected_revision": 1,
            "metadata_keys": ["session", "sequence"],
            "collection_notes": "Independent capture sessions",
        },
        200,
    ),
]


@pytest.mark.parametrize(
    "field,value",
    [
        ("metadata_keys", []),
        ("metadata_keys", ["session", "session"]),
        ("metadata_keys", ["class_id"]),
        ("metadata_keys", "session"),
        ("collection_notes", ""),
        ("collection_notes", "x" * 8193),
        ("files", [{"name": "fixture.png", "bytes": 100}]),
        ("original_base64", "aW1hZ2U="),
        ("assignments", {"asset": "test"}),
        ("holdout_references_allowed", True),
        ("approved", True),
        ("expected_revision", True),
    ],
)
def test_source_policy_refuses_file_capture_split_and_approval_authority(field, value):
    client, service = client_for(Actor())
    body = {
        "expected_revision": 1,
        "metadata_keys": ["session"],
        "collection_notes": "Independent capture sessions",
        field: value,
    }
    response = client.post(
        "/projects/p/setup/sources", json=body, headers={"Idempotency-Key": "source-policy"}
    )
    assert response.status_code == 422 and response.json() == {"detail": "invalid_request"}
    assert service.calls == []


READS = [
    "/projects",
    "/projects/p/resources?kind=variant",
    "/resources/model/m",
    "/models/m/export",
]


@dataclass
class Actor:
    id: str = "human-a"
    kind: str = "human"
    role: str = "admin"

    def permits(self, permission):
        return self.kind == "human" and (
            (permission == "review" and self.role in {"admin", "reviewer"})
            or (permission == "admin" and self.role == "admin")
        )


class RecordingOperations:
    """Records invocations, never emulates durable state, leases or idempotency."""

    def __init__(self):
        self.calls = []
        self.failure = None

    def __getattr__(self, operation):
        def call(*args):
            self.calls.append((operation, args))
            if self.failure:
                raise ManagementFailure(ManagementError(detail=self.failure))
            resource = Resource(
                id="result", project_id="p", kind="project", revision=1, state="draft"
            )
            if operation in {"compile_variant", "start_training", "start_evaluation"}:
                return JobAccepted(job_id="shared-job", resource=resource)
            if operation == "promote":
                return PromotionResult(
                    project_id="p",
                    recipe_id=args[2].recipe_id,
                    revision=1,
                    incumbent_id=None,
                    decision="retained",
                    gate_codes=["qualification_required"],
                )
            if operation in {"list_projects", "list_resources"}:
                return ResourcePage(items=[resource])
            if operation == "read_export":
                return ExportRead(
                    model_id="m",
                    recipe_id="full-letterbox-320-v1",
                    weights_sha256="a" * 64,
                    artifact_sha256="b" * 64,
                    format="onnx",
                    input_hw=(320, 320),
                    artifact_id="onnx-artifact",
                )
            return resource

        return call


def client_for(actor):
    service = RecordingOperations()
    app = FastAPI()

    def principal(request):
        if actor is None:
            raise HTTPException(401, detail="authentication_required")
        return actor

    app.include_router(management_router(principal, lambda: service))
    return TestClient(app), service


@pytest.mark.parametrize("path,operation,payload,status", MUTATIONS)
def test_admin_dispatches_typed_payload_with_attributed_context(path, operation, payload, status):
    actor = Actor()
    client, service = client_for(actor)
    response = client.post(path, json=payload, headers={"Idempotency-Key": "request-1"})
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert len(service.calls) == 1
    name, (received_actor, context, received) = service.calls[0]
    assert name == operation
    assert received_actor is actor
    assert context.actor_id == actor.id
    assert context.idempotency_key == "request-1"
    assert context.operation == operation
    assert (
        context.fingerprint
        == MutationContext.bind(
            actor, "request-1", operation, context.target_id, received
        ).fingerprint
    )
    assert received.expected_revision == payload.get("expected_revision", 0)


@pytest.mark.parametrize(
    "patch",
    [
        {"name": ""},
        {"representation": "invented"},
        {"expected_revision": True},
        {"expected_revision": "1"},
        {"approved_steps": ["scope"]},
        {"approval": "admin_approved"},
        {"external_provider_ids": ["provider"]},
        {"published_release_id": "release"},
    ],
)
def test_scope_draft_rejects_invalid_or_authority_fields(patch):
    client, service = client_for(Actor())
    path, _, payload, _ = next(item for item in MUTATIONS if item[1] == "save_scope_draft")
    response = client.post(path, json={**payload, **patch}, headers={"Idempotency-Key": "scope"})
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert not service.calls


def test_asset_json_text_is_decoded_for_the_port_without_relaxing_revisions():
    client, service = client_for(Actor())
    payload = MUTATIONS[1][2]
    response = client.post(
        "/projects/p/assets", json=payload, headers={"Idempotency-Key": "asset-1"}
    )
    assert response.status_code == 201, response.text
    received = service.calls[0][1][2]
    assert received.original_base64 == b"image"
    assert received.model_dump(mode="json")["original_base64"] == payload["original_base64"]


@pytest.mark.parametrize(
    "patch",
    [
        {"original_base64": "%%%="},
        {"original_base64": "aW1hZ2U=\n"},
        {"original_base64": "aW1hZ2U"},
        {"original_base64": ""},
        {"original_base64": 123},
        {"original_base64": None},
        {"original_base64": [97, 98]},
        {"expected_revision": "1"},
        {"expected_revision": True},
    ],
)
def test_asset_encoding_and_revision_refusals_do_not_reach_port(patch):
    client, service = client_for(Actor())
    response = client.post(
        "/projects/p/assets",
        json={**MUTATIONS[1][2], **patch},
        headers={"Idempotency-Key": "asset-1"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert not service.calls


@pytest.mark.parametrize("path,operation,payload,status", MUTATIONS)
@pytest.mark.parametrize("actor", [None, Actor(role="reviewer"), Actor(kind="service")])
def test_mutations_deny_nonadmin_before_calling_port(path, operation, payload, status, actor):
    client, service = client_for(actor)
    response = client.post(path, json=payload, headers={"Idempotency-Key": "request-1"})
    assert response.status_code == (401 if actor is None else 403)
    assert response.headers["cache-control"] == "no-store"
    assert not service.calls


@pytest.mark.parametrize("path", READS)
@pytest.mark.parametrize("role", ["admin", "reviewer"])
def test_human_reader_dispatches_authorized_read_port(path, role):
    actor = Actor(role=role)
    client, service = client_for(actor)
    assert client.get(path).status_code == 200
    assert service.calls[0][1][0] is actor


@pytest.mark.parametrize("path", READS)
def test_service_principal_cannot_use_human_management_reads(path):
    client, service = client_for(Actor(kind="service", role="admin"))
    assert client.get(path).status_code == 403
    assert not service.calls


@pytest.mark.parametrize("path,operation,payload,status", MUTATIONS)
@pytest.mark.parametrize("field", ["actor_id", "review_tier", "gold_eligible", "autonomous_spend"])
def test_client_authority_fields_are_refused_and_not_reflected(
    path, operation, payload, status, field
):
    client, service = client_for(Actor())
    response = client.post(
        path,
        json={**payload, field: "untrusted-content-do-not-reflect"},
        headers={"Idempotency-Key": "request-1"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert "untrusted-content" not in response.text
    assert not service.calls


@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": " "}, {"Idempotency-Key": "a" * 129}])
def test_idempotency_key_is_required_and_bounded(headers):
    client, service = client_for(Actor())
    assert client.post("/projects", json=PROJECT, headers=headers).status_code == 422
    assert not service.calls


@pytest.mark.parametrize(
    "patch", [{"expected_revision": True}, {"classes": PROJECT["classes"] * 2}, {"task": "segment"}]
)
def test_bootstrap_pack_refuses_coercion_duplicate_classes_and_other_tasks(patch):
    client, service = client_for(Actor())
    response = client.post(
        "/projects", json={**PROJECT, **patch}, headers={"Idempotency-Key": "request-1"}
    )
    assert response.status_code == 422
    assert not service.calls


@pytest.mark.parametrize(
    "code,status",
    [
        ("revision_conflict", 409),
        ("idempotency_conflict", 409),
        ("qualification_required", 422),
        ("not_found", 404),
        ("transport_failure", 503),
    ],
)
def test_transaction_refusals_are_not_successful_http_results(code, status):
    client, service = client_for(Actor())
    service.failure = code
    response = client.post("/projects", json=PROJECT, headers={"Idempotency-Key": "request-1"})
    assert response.status_code == status
    assert response.json() == {"detail": code}
    assert response.headers["cache-control"] == "no-store"


def test_fingerprint_binds_principal_operation_target_and_body_but_not_retry_key():
    payload = ProjectCreate.model_validate(PROJECT)
    actor = Actor()
    baseline = MutationContext.bind(actor, "first", "create_project", "projects", payload)
    retry = MutationContext.bind(actor, "second", "create_project", "projects", payload)
    assert baseline.fingerprint == retry.fingerprint
    for changed in [
        MutationContext.bind(Actor(id="human-b"), "first", "create_project", "projects", payload),
        MutationContext.bind(actor, "first", "other", "projects", payload),
        MutationContext.bind(actor, "first", "create_project", "other", payload),
        MutationContext.bind(
            actor,
            "first",
            "create_project",
            "projects",
            payload.model_copy(update={"scope": "new"}),
        ),
    ]:
        assert baseline.fingerprint != changed.fingerprint


def test_unbound_contract_never_accepts_real_requests():
    client = TestClient(contract_app())
    assert client.get("/projects").status_code == 503
    assert (
        client.post("/projects", json=PROJECT, headers={"Idempotency-Key": "first"}).status_code
        == 503
    )


@pytest.mark.parametrize(
    "schema,expected",
    [
        ({}, "unknown"),
        ({"type": "object"}, "Record<string, unknown>"),
        ({"type": "object", "additionalProperties": {}}, "Record<string, unknown>"),
        ({"type": "object", "additionalProperties": True}, "Record<string, unknown>"),
        ({"type": "object", "additionalProperties": False}, "Record<string, never>"),
        (
            {"type": "object", "additionalProperties": {"type": "integer"}},
            "Record<string, number>",
        ),
    ],
)
def test_codegen_handles_unconstrained_and_closed_dictionary_values(schema, expected):
    assert typescript_type(schema) == expected


@pytest.mark.parametrize(
    "parts,expected",
    [
        ([{"type": "integer"}, {"type": "number"}], "number"),
        (
            [{"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "null"}],
            "string | number | null",
        ),
        (
            [{"$ref": "#/components/schemas/Resource"}, {"type": "null"}],
            "Resource | null",
        ),
    ],
)
def test_codegen_deduplicates_typescript_unions_without_losing_nullability(parts, expected):
    assert typescript_type({"anyOf": parts}) == expected


def test_published_contract_check_reports_missing_files_without_writing(tmp_path):
    schema = tmp_path / "management.openapi.json"
    client = tmp_path / "management-client.ts"
    assert check_published(schema, client) == [schema, client]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["management.openapi.json", "management-client.ts"])
def test_published_contract_check_detects_drift_in_each_file_without_overwriting(tmp_path, name):
    generate(tmp_path)
    schema = tmp_path / "management.openapi.json"
    client = tmp_path / "management-client.ts"
    assert check_published(schema, client) == []
    changed = tmp_path / name
    changed.write_text("stale contract\n", encoding="utf-8")
    assert check_published(schema, client) == [changed]
    assert changed.read_text(encoding="utf-8") == "stale contract\n"


def test_codegen_does_not_silently_widen_unsupported_schema():
    with pytest.raises(ValueError, match="unsupported schema type"):
        typescript_type({"type": "unsupported"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("extent", "estimated"),
        ("min_target_px", 0),
        ("min_target_px", -1),
        ("min_target_px", "4"),
        ("min_target_px", True),
        ("annotation_guide", ""),
        ("background", ""),
        ("exhaustive_for_scope", "true"),
        ("approval", "admin_approved"),
        ("published_release_id", "forged"),
    ],
)
def test_guide_draft_refuses_unimplemented_or_untrusted_policy(field, value):
    client, service = client_for(Actor())
    guide = next(item[2] for item in MUTATIONS if item[1] == "save_guide_draft")
    payload = {**guide, field: value}
    response = client.post(
        "/projects/p/setup/guide", json=payload, headers={"Idempotency-Key": "guide"}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert not service.calls


@pytest.mark.parametrize(
    "patch",
    [
        {"base_ontology_id": ""},
        {"classes": []},
        {"expected_revision": "1"},
        {"approval": "admin_approved"},
        {"class_map": {"bird": 0}},
        {"published_release_id": "forged"},
        {"classes": CATALOG["classes"] * 2},
    ],
)
def test_catalog_refuses_untrusted_catalog_authority(patch):
    client, service = client_for(Actor())
    response = client.post(
        "/projects/p/setup/classes",
        json={**CATALOG, **patch},
        headers={"Idempotency-Key": "classes"},
    )
    assert response.status_code == 422 and response.json() == {"detail": "invalid_request"}
    assert not service.calls


@pytest.mark.parametrize(
    "patch",
    [
        {"stable_id": ""},
        {"name": ""},
        {"parent_id": "unknown"},
        {"parent_id": "bird"},
        {"aliases": ["Avian", "Avian"]},
        {"aliases": [""]},
        {"mirror_safe": "true"},
        {"positive_examples": ["x"] * 65},
        {"exclusions": [""]},
        {"approval": "admin_approved"},
        {"reference_ids": ["holdout"]},
        {"class_index": 0},
    ],
)
def test_catalog_refuses_invalid_or_privileged_class_fields(patch):
    client, service = client_for(Actor())
    response = client.post(
        "/projects/p/setup/classes",
        json={**CATALOG, "classes": [{**CATALOG["classes"][0], **patch}]},
        headers={"Idempotency-Key": "classes"},
    )
    assert response.status_code == 422 and response.json() == {"detail": "invalid_request"}
    assert not service.calls


def test_catalog_validates_multilevel_hierarchy_and_cycles():
    client, service = client_for(Actor())
    root = CATALOG["classes"][0]
    child = {**root, "stable_id": "sparrow", "parent_id": "bird"}
    leaf = {**root, "stable_id": "juvenile", "parent_id": "sparrow"}
    payload = {**CATALOG, "classes": [leaf, child, root]}
    response = client.post(
        "/projects/p/setup/classes", json=payload, headers={"Idempotency-Key": "classes"}
    )
    assert response.status_code == 200 and len(service.calls) == 1
    service.calls.clear()
    payload["classes"] = [leaf, child, {**root, "parent_id": "juvenile"}]
    response = client.post(
        "/projects/p/setup/classes", json=payload, headers={"Idempotency-Key": "cycle"}
    )
    assert response.status_code == 422 and not service.calls


def test_codegen_is_deterministic_and_has_all_operations(tmp_path):
    generate(tmp_path)
    spec = json.loads((tmp_path / "management.openapi.json").read_text())
    client = (tmp_path / "management-client.ts").read_text()
    assert client == render_client(spec)
    assert len(spec["paths"]) == 22
    assert sum(len(methods) for methods in spec["paths"].values()) == 23
    assert (
        spec["paths"]["/projects/{project_id}/setup/sources"]["post"]["operationId"]
        == "saveSourcePolicyDraft"
    )
    assert "export type SourcePolicyDraft =" in client
    assert "export type SourcePolicyRead =" in client
    assert (
        spec["paths"]["/projects/{project_id}/setup/classes"]["post"]["operationId"]
        == "saveClassCatalogDraft"
    )
    assert (
        spec["paths"]["/projects/{project_id}/setup/guide"]["post"]["operationId"]
        == "saveGuideDraft"
    )
    assert (
        spec["paths"]["/projects/{project_id}/setup/scope"]["post"]["operationId"]
        == "saveScopeDraft"
    )
    for model in (
        "ScopeDraft",
        "GuideDraft",
        "ClassCatalogDraft",
        "ClassCatalogRead",
        "ProjectDetail",
        "ReleaseDetail",
        "FreezePlanRead",
        "RecipePreviewRead",
        "TrainingDetail",
        "TrainingProfilesRead",
        "EvaluationDetail",
        "RegistryDetail",
        "ModelRelease",
        "EvaluationReport",
        "AffineTransform",
    ):
        assert f"export type {model} =" in client
    assert "credentials: 'same-origin'" in client
    assert "redirect: 'error'" in client
    assert "X-CSRF-Token" in client
    assert "Idempotency-Key" in client
    assert "export type PromotionRequest" in client
    assert "baseUrl" not in client
    first = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    generate(tmp_path)
    assert first == {path.name: path.read_bytes() for path in tmp_path.iterdir()}
