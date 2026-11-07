import hashlib
import json
import runpy
import shutil
from importlib.metadata import version
from pathlib import Path

import pytest

from labelloop_platform import Operation, PlatformRequest
from labelloop_platform.contract import DOCUMENT, ROOT, ROUTES, SNAPSHOT_SHA256, WireError, prepare

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
