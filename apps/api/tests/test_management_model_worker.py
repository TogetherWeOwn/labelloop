"""Synthetic export records test collection, never actual ONNX equivalence."""

import hashlib
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from labelloop_api.management_compile_worker import content_key
from labelloop_api.management_model_worker import collect_export
from labelloop_api.model_store import save_model_transaction
from labelloop_core.training import Checkpoint, InferencePolicy, TrainingRequest, TrainingResult


@pytest.fixture
def outputs(tmp_path):
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
    checkpoint = Checkpoint(0, "4" * 64, 0.5, "fixture-evaluation")
    result = TrainingResult(
        request, (checkpoint,), checkpoint, request.arguments(), "8.4.204", "fixture-only", False
    )
    data = b"synthetic ONNX; not loaded or executed"
    artifact = tmp_path / "candidate.onnx"
    artifact.write_bytes(data)
    evidence = {
        "weights_sha256": checkpoint.sha256,
        "export_sha256": hashlib.sha256(data).hexdigest(),
        "static_input_signature": [1, 3, 320, 320],
        "policy": asdict(request.policy),
        "sample_count": 1,
        "detection_counts": [1],
        "coordinate_atol_px": 0.01,
        "confidence_atol": 1e-5,
    }
    manifest = {"recipe": {"output_size": 320}, "class_map": {"package": 0}}
    saved = {}
    objects = SimpleNamespace(put=lambda key, value: saved.__setitem__(key, value))
    return result, manifest, artifact, evidence, objects, saved


def test_export_collection_uploads_only_checked_content(outputs):
    result, manifest, artifact, evidence, objects, saved = outputs
    record = collect_export(result, manifest, artifact, evidence, objects)
    assert record == {
        "evidence": evidence,
        "export_key": content_key(evidence["export_sha256"]),
        "weights_key": content_key(result.selected.sha256),
    }
    assert saved == {record["export_key"]: artifact.read_bytes()}


@pytest.mark.parametrize("change", ["bytes", "empty", "missing", "symlink", "oversize"])
def test_export_file_refusal_uploads_nothing(outputs, change):
    result, manifest, artifact, evidence, objects, saved = outputs
    if change == "bytes":
        artifact.write_bytes(b"changed")
    elif change == "empty":
        artifact.write_bytes(b"")
    elif change == "missing":
        artifact.unlink()
    elif change == "symlink":
        target = artifact.with_suffix(".other")
        artifact.rename(target)
        artifact.symlink_to(target)
    else:
        with artifact.open("wb") as stream:
            stream.truncate(256_000_001)
    with pytest.raises(ValueError):
        collect_export(result, manifest, artifact, evidence, objects)
    assert saved == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("weights_sha256", "0" * 64),
        ("export_sha256", "0" * 64),
        ("static_input_signature", [1, 3, 352, 352]),
        ("sample_count", True),
        ("sample_count", 0),
        ("sample_count", 1.0),
        ("detection_counts", []),
        ("detection_counts", [0]),
        ("detection_counts", [-1]),
        ("detection_counts", [True]),
        ("detection_counts", None),
        ("coordinate_atol_px", 1),
        ("confidence_atol", 0.1),
        ("policy", {}),
    ],
)
def test_export_evidence_refusal_uploads_nothing(outputs, field, value):
    result, manifest, artifact, evidence, objects, saved = outputs
    evidence[field] = value
    with pytest.raises(ValueError):
        collect_export(result, manifest, artifact, evidence, objects)
    assert saved == {}


def test_native_model_store_refuses_idle_connection_before_any_query():
    import psycopg

    idle = SimpleNamespace(
        info=SimpleNamespace(transaction_status=psycopg.pq.TransactionStatus.IDLE)
    )
    with pytest.raises(ValueError, match="active caller transaction"):
        save_model_transaction(idle, training_run_key="fixture", release=None, export_evidence={})
