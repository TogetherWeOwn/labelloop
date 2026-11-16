import hashlib
import json
import runpy
import shutil
import tomllib
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
        path={"imageId": "507f1f77bcf86cd799439011"},
        body={"labels": [{"classId": 0, "bbox": [0.5, 0.5, 0.2, 0.2]}]},
    )
    assert prepare(request)[1] == "/api/images/507f1f77bcf86cd799439011"
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


def test_scan_classification_has_no_commit_or_path_only_exemption() -> None:
    repo = PACKAGE.parents[1]
    fingerprints = [
        line
        for line in (repo / ".gitleaksignore").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert fingerprints == []
    config = tomllib.loads((repo / ".gitleaks.toml").read_text())
    assert config["extend"] == {"useDefault": True}
    assert "allowlist" not in config and "allowlists" not in config
    assert [rule["id"] for rule in config["rules"]] == ["private-key", "generic-api-key"]
    for rule in config["rules"]:
        assert len(rule["allowlists"]) == 1
        allowlist = rule["allowlists"][0]
        assert allowlist["condition"] == "AND"
        assert allowlist["regexTarget"] == "secret"
        assert len(allowlist["paths"]) == len(allowlist["regexes"]) == 1
    generic = config["rules"][1]["allowlists"][0]
    assert "test_management_canonical_db" in generic["paths"][0]
    assert generic["regexes"] == ["^compile-evaluation-640$"]
    lines = ROOT.joinpath("openapi.json").read_text().splitlines()
    for line_number in (22480, 22704):
        example = json.loads("{" + lines[line_number - 1].strip() + "}")["example"]
        assert example.splitlines()[1] == "YOUR_GCP_PRIVATE_KEY"
        assert len(example.splitlines()) == 3


@pytest.mark.parametrize(
    "operation", [Operation.READ_IMAGE, Operation.REPLACE_LABELS, Operation.PREDICT_IMAGE]
)
@pytest.mark.parametrize("image_id", ["fixture-image", "a" * 23, "a" * 25, "g" * 24])
def test_path_image_id_schema_rejected(operation, image_id) -> None:
    with pytest.raises(WireError):
        prepare(PlatformRequest(operation, path={"imageId": image_id}))


@pytest.mark.parametrize("image_id", ["0" * 24, "F" * 24, "507f1f77bcf86cd799439011"])
def test_path_image_id_schema_valid_boundaries(image_id) -> None:
    assert prepare(PlatformRequest(Operation.READ_IMAGE, path={"imageId": image_id}))[1].endswith(
        image_id
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner", "abc"),
        ("owner", "a" * 33),
        ("owner", "Uppercase"),
        ("owner", "under_score"),
        ("owner", "-owner"),
        ("owner", "owner-"),
        ("owner", "owner--slug"),
        ("dataset", ""),
        ("dataset", "x" * 129),
        ("dataset", "data_set"),
        ("project", "project--slug"),
        ("project", "x" * 129),
        ("model", "Model"),
        ("model", "x" * 129),
    ],
)
def test_path_slug_schema_rejected(field, value) -> None:
    path = (
        {"owner": "fixture", "dataset": "release"}
        if field in ("owner", "dataset")
        else {"owner": "fixture", "project": "project", "model": "model"}
    )
    operation = Operation.READ_DATASET if "dataset" in path else Operation.READ_MODEL
    with pytest.raises(WireError):
        prepare(PlatformRequest(operation, path={**path, field: value}))


@pytest.mark.parametrize("owner", ["abcd", "a" * 32])
@pytest.mark.parametrize("slug", ["x", "x" * 128, "valid-slug-2"])
def test_path_slug_schema_valid_boundaries(owner, slug) -> None:
    prepare(PlatformRequest(Operation.READ_DATASET, path={"owner": owner, "dataset": slug}))
    prepare(
        PlatformRequest(Operation.READ_MODEL, path={"owner": owner, "project": slug, "model": slug})
    )


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
