"""Synthetic predictions test orchestration/coverage, not actual inference or DB gates."""

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from labelloop_api.management_compile_worker import content_key
from labelloop_api.management_evaluate_worker import evaluate_model
from labelloop_api.model_store import record_digest
from labelloop_compiler.compiler import json_bytes, transform_document
from labelloop_core.evaluation import DerivedPredictions, Detection, EvaluationImage, GroundTruth
from labelloop_core.geometry import Box, full_frame_transform
from labelloop_core.training import InferencePolicy


@pytest.fixture
def inputs():
    image = EvaluationImage(
        "visible",
        24,
        12,
        (GroundTruth("one", "package", Box(2, 2, 6, 6)),),
        revision=1,
        source_sha256="1" * 64,
    )
    excluded = replace(image, asset_id="excluded", source_sha256="2" * 64)
    transform = full_frame_transform(24, 12, 320)
    weights = b"synthetic weights, never loaded"
    digest = hashlib.sha256(weights).hexdigest()
    pixels = b"synthetic pixels, not decoded by this unit fixture"
    pixel_digest = hashlib.sha256(pixels).hexdigest()
    manifest = {
        "release_id": "frozen",
        "recipe": {"recipe_id": "full-letterbox-320-v1"},
        "class_map": {"package": 0},
        "file_hashes": {"class-map.json": "3" * 64, "images/val/visible.png": pixel_digest},
        "samples": [
            {
                "split": "val",
                "asset_id": "visible",
                "image_path": "images/val/visible.png",
                "source_truth_sha256": image.truth_digest(),
                "transform": transform_document(transform),
            }
        ],
    }
    model = SimpleNamespace(
        weights_sha256=digest,
        class_map_sha256="3" * 64,
        class_ids=("package",),
        preprocessing_sha256=record_digest(manifest["recipe"]),
        recipe_id="full-letterbox-320-v1",
        policy=InferencePolicy(320),
    )
    stored = {
        content_key(digest): weights,
        content_key(pixel_digest): pixels,
        content_key(hashlib.sha256(json_bytes(manifest)).hexdigest()): json_bytes(manifest),
    }
    reads, paths = [], []

    def read(key):
        reads.append(key)
        return stored[key]

    def predictor(weights, root, samples, classes, policy, shapes):
        paths.extend([weights, root])
        assert list(root.rglob("*.png")) == [root / "images/val/visible.png"]
        shapes.append([1, 3, 320, 320])
        box = transform.apply(image.objects[0].box)
        assert box is not None
        return (DerivedPredictions("visible", transform, (Detection("package", box, 1.0),)),)

    return model, manifest, (image, excluded), SimpleNamespace(read=read), predictor, reads, paths


def test_original_frame_scoring_retains_excluded_positive_source_and_cleanup(inputs):
    model, manifest, images, objects, predictor, reads, paths = inputs
    record = evaluate_model("development-run", model, manifest, images, objects, predictor)
    report = record["report"]
    assert report["run_id"] == "development-run" and report["role"] == "development"
    assert report["overall"]["images"] == report["overall"]["support"] == 2
    assert report["overall"]["per_class"]["package"]["true_positives"] == 1
    assert report["overall"]["recall"] == 0.5
    coverage = {item["asset_id"]: item for item in report["coverage"]}
    assert coverage["visible"]["covered_fraction"] == 1
    assert coverage["excluded"]["covered_fraction"] == 0
    assert coverage["excluded"]["uncovered_targets"] == 1
    assert record["sample_count"] == 1 and record["tensor_shapes"] == [[1, 3, 320, 320]]
    assert len(reads) == 3 and paths and all(not path.exists() for path in paths)
    # A resumed stage must have the same shape as its freshly computed payload.
    assert json.loads(json.dumps(record, allow_nan=False)) == record


@pytest.mark.parametrize(
    "change", ["truth", "missing", "duplicate", "recipe", "class-map", "split"]
)
def test_frozen_input_refusal_precedes_object_or_predictor_access(inputs, change):
    model, manifest, images, _, _, reads, _ = inputs
    if change == "truth":
        images = (replace(images[0], revision=2), images[1])
    elif change == "missing":
        images = images[1:]
    elif change == "duplicate":
        manifest["samples"] *= 2
    elif change == "recipe":
        manifest["recipe"]["recipe_id"] = "unapproved"
    elif change == "class-map":
        manifest["class_map"] = {"foreign": 0}
    else:
        manifest["samples"][0]["image_path"] = "images/test/visible.png"

    def unexpected(*args):
        pytest.fail("invalid authority must fail before I/O")

    with pytest.raises(ValueError):
        evaluate_model("run", model, manifest, images, SimpleNamespace(read=unexpected), unexpected)
    assert not reads


@pytest.mark.parametrize("failure", ["absent", "few", "batch", "shape", "source", "class", "box"])
def test_prediction_and_observed_tensor_refusals_cleanup(inputs, failure):
    model, manifest, images, objects, predictor, _, paths = inputs

    def invalid(weights, root, samples, classes, policy, shapes):
        result = predictor(weights, root, samples, classes, policy, shapes)
        if failure == "absent":
            shapes.clear()
        elif failure == "few":
            return ()
        elif failure == "batch":
            shapes[:] = [[2, 3, 320, 320]]
        elif failure == "shape":
            shapes[:] = [[1, 3, 640, 640]]
        elif failure == "source":
            return (replace(result[0], asset_id="unlisted"),)
        elif failure == "class":
            return (replace(result[0], detections=(Detection("foreign", Box(1, 1, 2, 2), 1),)),)
        else:
            return (replace(result[0], detections=(Detection("package", Box(1, 1, 321, 321), 1),)),)
        return result

    with pytest.raises(ValueError):
        evaluate_model("run", model, manifest, images, objects, invalid)
    assert paths and all(not path.exists() for path in paths)
