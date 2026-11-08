"""Read-port contract coverage only, not final store/auth integration evidence."""

import pytest
from pydantic import ValidationError
from test_management_contract import Actor, client_for

from labelloop_api.management_details import TrainingProfileRead
from labelloop_core.geometry import full_frame_transform

RESOURCE = {"id": "result", "project_id": "p", "kind": "project", "revision": 1, "state": "draft"}
DETAIL_READS = [
    (
        "/projects/p",
        "read_project",
        ("p",),
        {
            "resource": RESOURCE,
            "name": "Birds",
            "scope": "Birds",
            "domain": "wildlife",
            "representation": "scene_object",
            "intended_use": "fixture",
            "specificity": "species",
            "source_permitted_use": "CC0",
            "external_provider_ids": [],
            "classes": [],
            "references": [],
            "guide": {
                "extent": "visible",
                "truncation": "visible",
                "occlusion": "visible",
                "reflections": "exclude",
                "min_target_px": 4.0,
                "exhaustive_for_scope": True,
                "background": "out of scope",
            },
            "sources": {
                "images": 0,
                "groups": 0,
                "metadata_keys": [],
                "pending_pairs": [],
                "split_counts": {},
                "assignment_version": None,
            },
            "approved_steps": [],
            "versions": {},
            "published_release_id": None,
            "saved_at": "2026-11-08T00:00:00Z",
        },
    ),
    (
        "/projects/p/freeze-plan",
        "read_freeze_plan",
        ("p",),
        {
            "project_id": "p",
            "payload": None,
            "checks": [{"code": "incomplete_labels", "passed": False, "reason": "No members"}],
            "exclusions": [],
        },
    ),
    (
        "/dataset-releases/r",
        "read_release",
        ("r",),
        {
            "resource": RESOURCE,
            "previous_release_id": None,
            "ontology_id": "ontology-1",
            "assignment_version": "split-1",
            "members": [],
            "classes": [],
            "exclusions": [],
            "checks": [],
            "changed_asset_ids": [],
            "auto_accepted_objects": 0,
            "human_reviewed_objects": 0,
            "acceptance_policy_id": "policy-1",
            "quarantined_reason": None,
            "manifest_artifact_id": None,
            "manifest_sha256": None,
            "frozen_at": None,
            "frozen_by": None,
        },
    ),
    (
        "/dataset-releases/r/recipes/full-letterbox-320-v1/preview?asset_id=a",
        "read_recipe_preview",
        ("r", "full-letterbox-320-v1", "a"),
        {
            "recipe_id": "full-letterbox-320-v1",
            "recipe_revision": 1,
            "source_asset_id": "a",
            "source_width": 1920,
            "source_height": 1080,
            "transform": full_frame_transform(1920, 1080, 320),
            "boxes": [],
            "tiny_targets": 0,
            "below_minimum_targets": 0,
            "clipped_targets": 0,
            "excluded_images": 0,
            "checks": [],
            "manifest_artifact_id": None,
            "validation_artifact_id": None,
        },
    ),
    (
        "/projects/p/training-profiles",
        "list_training_profiles",
        ("p",),
        {"project_id": "p", "profiles": []},
    ),
    (
        "/training-runs/t",
        "read_training",
        ("t",),
        {
            "resource": RESOURCE,
            "recipe_id": "full-letterbox-320-v1",
            "dataset_release_id": "r",
            "variant_id": "v",
            "training_profile_id": "cpu-1",
            "training_profile_revision": 1,
            "initialization_sha256": "a" * 64,
            "epoch": 0,
            "epochs": 1,
            "images": 16,
            "reason": None,
            "updated_at": "2026-11-08T00:00:00Z",
            "requested_arguments": {"epochs": 1},
            "resolved_arguments": None,
            "checkpoints": [],
            "retention": None,
            "mismatches": [],
        },
    ),
    (
        "/evaluations/e",
        "read_evaluation",
        ("e",),
        {
            "resource": RESOURCE,
            "candidate_id": "m",
            "incumbent_id": None,
            "report": None,
            "incumbent_report": None,
            "checks": [],
            "qualification_id": None,
            "qualification": "unqualified",
            "reason": "Missing evidence",
            "minimum_class_support": None,
            "opened_final_test_count": 0,
        },
    ),
    (
        "/projects/p/registry/full-letterbox-320-v1?cursor=event-1&limit=10",
        "read_registry",
        ("p", "full-letterbox-320-v1", "event-1", 10),
        {
            "project_id": "p",
            "recipe_id": "full-letterbox-320-v1",
            "revision": 0,
            "champion": None,
            "candidate": None,
            "rollback": None,
            "qualification_id": None,
            "qualification": "unqualified",
            "checks": [],
            "reason": "Missing evidence",
            "history": [],
            "next_history_cursor": None,
        },
    ),
]


@pytest.mark.parametrize("path,operation,arguments,result", DETAIL_READS)
@pytest.mark.parametrize("role", ["admin", "reviewer"])
def test_details_dispatch_exact_scope_and_preserve_missing_evidence(
    path, operation, arguments, result, role
):
    actor = Actor(role=role)
    client, service = client_for(actor)
    calls = []

    def read(*args):
        calls.append(args)
        return result

    setattr(service, operation, read)
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert calls == [(actor, *arguments)]
    if operation == "read_evaluation":
        assert response.json()["report"] is None
        assert response.json()["qualification"] == "unqualified"
    if operation == "read_recipe_preview":
        assert response.json()["transform"]["top"] == 70
        assert response.json()["transform"]["output_size"] == 320


@pytest.mark.parametrize("path,operation,arguments,result", DETAIL_READS)
@pytest.mark.parametrize("actor", [None, Actor(kind="service")])
def test_details_deny_nonhuman_before_port(path, operation, arguments, result, actor):
    client, service = client_for(actor)
    assert client.get(path).status_code == (401 if actor is None else 403)
    assert not service.calls


@pytest.mark.parametrize("path,operation,arguments,result", DETAIL_READS)
def test_detail_adapter_authorization_failure_is_not_an_empty_success(
    path, operation, arguments, result
):
    client, service = client_for(Actor(role="reviewer"))
    service.failure = "not_found"
    response = client.get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "not_found"}


def test_registry_bounds_are_validated_before_port():
    client, service = client_for(Actor())
    for suffix in ("?limit=101", "?limit=0", "?cursor=bad%20cursor"):
        assert client.get("/projects/p/registry/full-letterbox-320-v1" + suffix).status_code == 422
    assert not service.calls


def test_training_profile_refuses_nonfinite_arguments():
    with pytest.raises(ValidationError):
        TrainingProfileRead(
            id="cpu-1",
            revision=1,
            recipe_id="full-letterbox-320-v1",
            backend="local_ultralytics",
            initialization_sha256="a" * 64,
            epochs=1,
            arguments={"conf": float("nan")},
        )
