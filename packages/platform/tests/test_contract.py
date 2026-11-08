import hashlib
import json
import runpy
import shutil
from importlib.metadata import version
from pathlib import Path

import pytest

from labelloop_platform import Operation, PlatformRequest
from labelloop_platform.contract import (
    DOCUMENT,
    ROOT,
    ROUTES,
    SNAPSHOT_SHA256,
    WireError,
    prepare,
    validate_shape,
)


def test_valid_bbox_tuple_and_boolean_remainder_schema() -> None:
    request = PlatformRequest(
        Operation.REPLACE_LABELS,
        "replace-1",
        path={"imageId": "fixture-image"},
        body={"labels": [{"classId": 0, "bbox": [0.5, 0.5, 0.2, 0.2]}]},
    )
    assert prepare(request)[1] == "/api/images/fixture-image"
    tuple_schema = {"type": "array", "prefixItems": [{"type": "number"}], "items": False}
    validate_shape([0.5], tuple_schema)
    for invalid in ([0.5, 0.2], ["not-a-number"]):
        with pytest.raises(WireError):
            validate_shape(invalid, tuple_schema)
    validate_shape([0.5, "anything"], {**tuple_schema, "items": True})


@pytest.mark.parametrize("size", [0, -1])
def test_signed_upload_requires_exclusive_positive_size(size) -> None:
    with pytest.raises(WireError):
        prepare(
            PlatformRequest(
                Operation.SIGNED_UPLOAD,
                "upload-1",
                body={
                    "assetId": "fixture-dataset",
                    "assetType": "datasets",
                    "filename": "dataset.zip",
                    "contentType": "application/zip",
                    "totalBytes": size,
                },
            )
        )


def test_export_response_exclusive_version_bound() -> None:
    schema = DOCUMENT["components"]["schemas"]["DatasetExportResponse"]
    validate_shape({"downloadUrl": "https://fixture-storage.example/export", "version": 1}, schema)
    with pytest.raises(WireError):
        validate_shape(
            {"downloadUrl": "https://fixture-storage.example/export", "version": 0}, schema
        )
    with pytest.raises(WireError):
        validate_shape(1, {"type": "number", "exclusiveMaximum": 1})


PACKAGE = Path(__file__).resolve().parents[1]
GENERATOR = runpy.run_path(str(PACKAGE / "generate_contract.py"))


def test_sdk_snapshot_provenance_and_generated_client_do_not_drift() -> None:
    assert version("ultralytics-platform") == "0.1.107"
    provenance = json.loads(ROOT.joinpath("provenance.json").read_text())
    assert provenance["sha256"] == SNAPSHOT_SHA256
    assert hashlib.sha256(ROOT.joinpath("openapi.json").read_bytes()).hexdigest() == SNAPSHOT_SHA256
    assert provenance["sdk_version"] == DOCUMENT["info"]["version"] == "0.1.107"
    assert provenance["source_commit"].startswith("8af3921")
    assert set(ROUTES) == {operation.value for operation in Operation}
    assert ROOT.joinpath("operations.json").read_text() == GENERATOR["render"]()


def test_snapshot_corruption_fails_regeneration(tmp_path) -> None:
    contract = Path(str(ROOT))
    shutil.copytree(contract, tmp_path / "contract")
    snapshot = tmp_path / "contract/openapi.json"
    snapshot.write_bytes(snapshot.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        GENERATOR["render"](tmp_path / "contract")


def test_generated_route_drift_is_observable(tmp_path) -> None:
    # Changing policy without regenerating changes generated output; CI compares bytes.
    operations = GENERATOR["render"].__globals__["OPERATIONS"]
    original = operations["read_dataset"]
    try:
        operations["read_dataset"] = (*original[:2], "download")
        assert GENERATOR["render"]() != ROOT.joinpath("operations.json").read_text()
    finally:
        operations["read_dataset"] = original


@pytest.mark.parametrize(
    "body",
    [
        {"dataset": "release", "visibility": "public", "task": "detect"},
        {"dataset": "release", "visibility": "private", "task": "segment"},
        {"dataset": "release", "visibility": "private", "task": "detect", "imageCount": -1},
        {
            "dataset": "release",
            "visibility": "private",
            "task": "detect",
            "metadata": {"x": float("nan")},
        },
    ],
)
def test_invalid_or_non_private_dataset_request_rejected(body) -> None:
    with pytest.raises((WireError, ValueError)):
        prepare(PlatformRequest(Operation.CREATE_DATASET, "create-1", body=body))
