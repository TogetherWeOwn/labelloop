"""Trusted profile configuration validation; DB/queue evidence lives in Actions cases."""

from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest

from labelloop_api.management_training import ApprovedTrainingProfile, CanonicalManagementTraining
from labelloop_core.training import InferencePolicy


@pytest.fixture
def profile():
    return ApprovedTrainingProfile(uuid4(), "baseline", 1, "full-letterbox-320-v1", "1" * 64)


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", "unbound"),
        ("id", "../../checkpoint"),
        ("revision", -1),
        ("revision", True),
        ("recipe_id", "arbitrary-recipe"),
        ("initialization_sha256", "mutable.pt"),
        ("epochs", 0),
        ("epochs", True),
        ("batch", 0),
        ("batch", 1.5),
        ("seed", "0"),
        ("inference_policy", {"imgsz": 320}),
        ("inference_policy", InferencePolicy(640)),
    ],
)
def test_training_profile_rejects_invalid_server_configuration(profile, field, value):
    with pytest.raises(ValueError):
        replace(profile, **{field: value})


def test_training_profiles_are_immutable_and_project_scoped(profile):
    with pytest.raises(FrozenInstanceError):
        profile.epochs = 100
    with pytest.raises(ValueError, match="uniquely project-scoped"):
        CanonicalManagementTraining(None, None, (profile, profile))
    with pytest.raises(ValueError, match="uniquely project-scoped"):
        CanonicalManagementTraining(None, None, [profile])
    # Matching public IDs in distinct projects are not a global profile authority.
    CanonicalManagementTraining(None, None, (profile, replace(profile, project_id=uuid4())))


@pytest.mark.parametrize("size", [320, 640])
def test_training_profile_derives_all_arguments_and_shape_from_delivered_contract(profile, size):
    profile = replace(profile, recipe_id=f"full-letterbox-{size}-v1", batch=4, seed=7)
    read = profile.read()
    assert read.backend == "local_ultralytics" and read.revision == 1
    assert read.arguments["imgsz"] == size and read.arguments["device"] == "cpu"
    assert read.arguments["batch"] == 4 and read.arguments["seed"] == 7
    assert read.arguments["conf"] == 0.25 and read.arguments["max_det"] == 300
    assert read.arguments["rect"] is False and read.arguments["tile"] is False
    assert read.arguments["nms"] is True and read.arguments["multi_scale"] == 0.0
    assert read.arguments["mosaic"] == read.arguments["fliplr"] == 0.0
    assert (
        not {"path", "model", "data", "endpoint", "provider", "workspace_id"}
        & read.arguments.keys()
    )


@pytest.mark.parametrize("size", [320, 640])
def test_trusted_profile_pins_inference_policy_without_changing_recipe_or_baseline(profile, size):
    policy = InferencePolicy(size, conf=0.005, max_det=1000)
    profile = replace(profile, recipe_id=f"full-letterbox-{size}-v1", inference_policy=policy)
    request = profile.request(
        "run",
        "release",
        {"file_hashes": {"class-map.json": "0" * 64}, "split_assignment_version": "assignment"},
        "2" * 64,
    )
    assert request.policy == policy
    assert profile.read().arguments == request.arguments()
    assert request.arguments()["conf"] == 0.005 and request.arguments()["max_det"] == 1000
    assert request.arguments()["device"] == "cpu"
    assert request.arguments()["mosaic"] == request.arguments()["multi_scale"] == 0.0
    assert request.arguments()["tile"] is False and request.validation_role == "development"
    with pytest.raises(FrozenInstanceError):
        policy.conf = 0.5
