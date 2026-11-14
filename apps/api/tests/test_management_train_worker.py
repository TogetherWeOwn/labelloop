"""Artifact checks with synthetic backend output, not real training acceptance."""

import hashlib
import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from labelloop_api.management_compile_worker import content_key
from labelloop_api.management_train_worker import collect_training, materialize_dataset
from labelloop_core.evaluation import ClassMetrics, EvaluationReport, Metrics
from labelloop_core.training import (
    ULTRALYTICS_VERSION,
    Checkpoint,
    InferencePolicy,
    TrainingRequest,
    TrainingResult,
)


@pytest.fixture
def outputs(tmp_path):
    weights = b"synthetic checkpoint, never loaded as a model"
    digest = hashlib.sha256(weights).hexdigest()
    request = TrainingRequest(
        "fixture-run",
        "fixture-project",
        "full-letterbox-320-v1",
        "fixture-release",
        "1" * 64,
        "2" * 64,
        "assignment-1",
        "3" * 64,
        InferencePolicy(320),
    )
    checkpoint = Checkpoint(0, digest, 0.5, "fixture-evaluation")
    result = TrainingResult(
        request,
        (checkpoint,),
        checkpoint,
        request.arguments(),
        ULTRALYTICS_VERSION,
        "fixture-only",
        False,
    )
    manifest = {
        "class_map": {"package": 0},
        "samples": [{"split": "train", "image_path": "images/train/a.png"}],
    }
    directory = tmp_path / "trainer/weights"
    directory.mkdir(parents=True)
    (directory / "epoch0.pt").write_bytes(weights)
    metrics = Metrics(
        1,
        1,
        0.5,
        0.5,
        1.0,
        1.0,
        {"package": ClassMetrics(True, 1, 1, 1, 0, 0.5, 0.5, 1.0, 1.0)},
    )
    report = EvaluationReport(
        checkpoint.evaluation_run_id,
        request.dataset_release_id,
        request.recipe_id,
        digest,
        request.class_map_sha256,
        "development",
        request.policy,
        metrics,
        {},
        (),
    )
    (tmp_path / "evaluation-epoch-0.json").write_text(json.dumps(asdict(report)))
    (tmp_path / "tensor-shapes.json").write_text(
        json.dumps({key: [[1, 3, 320, 320]] for key in ("training", "validation", "inference")})
    )
    (tmp_path / "target-retention.json").write_text(
        json.dumps(
            [
                {
                    "sample": "a.png",
                    "before": 1,
                    "after": 1,
                    "before_at_or_below_6px": 0,
                    "after_at_or_below_6px": 0,
                }
            ]
        )
    )
    saved = {}
    objects = SimpleNamespace(put=lambda key, data: saved.__setitem__(key, data))
    return request, result, manifest, tmp_path, objects, saved


def collect(outputs):
    return collect_training(*outputs[:5])


def test_training_artifacts_are_content_addressed_without_canonical_publication(outputs):
    request, result, _, root, _, saved = outputs
    record = collect(outputs)
    assert record["result"] == asdict(result)
    assert record["reports"][0]["role"] == "development"
    assert record["checkpoint_keys"] == {
        result.selected.sha256: content_key(result.selected.sha256)
    }
    assert saved == {
        content_key(result.selected.sha256): (root / "trainer/weights/epoch0.pt").read_bytes()
    }
    assert request.run_id == record["result"]["request"]["run_id"]


@pytest.mark.parametrize("change", ["checkpoint", "symlink", "request", "epoch", "arguments"])
def test_training_output_refusal_uploads_no_checkpoint(outputs, change):
    request, result, manifest, root, objects, saved = outputs
    checkpoint = root / "trainer/weights/epoch0.pt"
    if change == "checkpoint":
        checkpoint.write_bytes(b"changed")
    elif change == "symlink":
        target = root / "other.pt"
        target.write_bytes(checkpoint.read_bytes())
        checkpoint.unlink()
        checkpoint.symlink_to(target)
    elif change == "request":
        request = replace(request, run_id="different")
    elif change == "epoch":
        request = replace(request, epochs=2)
        result = replace(result, request=request, resolved_arguments=request.arguments())
    else:
        # Bypass construction to model a broken trusted adapter returning an invalid record.
        result.resolved_arguments["tile"] = True
    with pytest.raises(ValueError):
        collect_training(request, result, manifest, root, objects)
    assert saved == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "different"),
        ("role", "locked_final_test"),
        ("weights_sha256", "a" * 64),
        ("class_map_sha256", "a" * 64),
        ("dataset_release_id", "other"),
        ("recipe_id", "other"),
    ],
)
def test_training_report_lineage_is_checked_before_upload(outputs, field, value):
    report = outputs[3] / "evaluation-epoch-0.json"
    data = json.loads(report.read_text())
    data[field] = value
    report.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="report differs"):
        collect(outputs)
    assert outputs[-1] == {}


@pytest.mark.parametrize(
    "change", ["empty-shape", "wrong-shape", "missing-stage", "loss", "negative", "missing-sample"]
)
def test_training_shape_and_retention_evidence_is_required(outputs, change):
    root = outputs[3]
    shapes = root / "tensor-shapes.json"
    retention = root / "target-retention.json"
    if "shape" in change or change == "missing-stage":
        data = json.loads(shapes.read_text())
        if change == "empty-shape":
            data["training"] = []
        elif change == "wrong-shape":
            data["validation"] = [[1, 3, 352, 352]]
        else:
            del data["inference"]
        shapes.write_text(json.dumps(data))
    else:
        data = json.loads(retention.read_text())
        if change == "loss":
            data[0]["after"] = 0
        elif change == "negative":
            data[0]["before"] = data[0]["after"] = -1
        else:
            data[0]["sample"] = "unlisted.png"
        retention.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        collect(outputs)
    assert outputs[-1] == {}


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "a/../escape", "./alias", "a//b", "a\\b", "manifest.json"]
)
def test_dataset_paths_are_refused_before_object_access(tmp_path, name):
    def unexpected(_key):
        pytest.fail("unsafe allowlist must be refused before storage access")

    with pytest.raises(ValueError, match="artifact path"):
        materialize_dataset(
            SimpleNamespace(read=unexpected),
            {"file_hashes": {name: "a" * 64}},
            tmp_path / "dataset",
        )
    assert not (tmp_path / "dataset").exists()


def test_dataset_materialization_checks_actual_bytes_and_exact_membership(tmp_path):
    data = b"fixture"
    digest = hashlib.sha256(data).hexdigest()
    objects = SimpleNamespace(read=lambda key: data if key == content_key(digest) else b"bad")
    manifest = {"file_hashes": {"images/train/a.png": digest}}
    root = tmp_path / "dataset"
    materialize_dataset(objects, manifest, root)
    assert (root / "images/train/a.png").read_bytes() == data
    assert json.loads((root / "manifest.json").read_bytes()) == manifest
    with pytest.raises(ValueError, match="digest differs"):
        materialize_dataset(objects, {"file_hashes": {"data.yaml": "a" * 64}}, tmp_path / "corrupt")
