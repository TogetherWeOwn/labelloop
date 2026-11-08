"""Fixture-only facade checks; these do not prove database/queue/auth integration."""

import json
from dataclasses import dataclass

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from labelloop_api.management import management_router
from labelloop_api.management_codegen import contract_app, generate, render_client
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
]
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


def test_codegen_is_deterministic_and_has_all_operations(tmp_path):
    generate(tmp_path)
    spec = json.loads((tmp_path / "management.openapi.json").read_text())
    client = (tmp_path / "management-client.ts").read_text()
    assert client == render_client(spec)
    assert len(spec["paths"]) == 10
    assert sum(len(methods) for methods in spec["paths"].values()) == 11
    assert "credentials: 'same-origin'" in client
    assert "redirect: 'error'" in client
    assert "X-CSRF-Token" in client
    assert "Idempotency-Key" in client
    assert "export type PromotionRequest" in client
    assert "baseUrl" not in client
    first = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    generate(tmp_path)
    assert first == {path.name: path.read_bytes() for path in tmp_path.iterdir()}
