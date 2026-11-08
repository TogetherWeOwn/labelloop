"""Regenerate the bounded client route table from the pinned, offline OpenAPI snapshot."""

import argparse
import hashlib
import json
from pathlib import Path

CONTRACT = Path(__file__).parent / "src/labelloop_platform/contract"
# Application policy, not vendor-discovered capability. New operations require code review.
OPERATIONS = {
    "create_dataset": ("POST", "/api/datasets", "default"),
    "read_dataset": ("GET", "/api/datasets/{owner}/{dataset}", "default"),
    "signed_upload": ("POST", "/api/upload/signed-url", "upload"),
    "complete_upload": ("POST", "/api/upload/complete", "upload"),
    "ingest": ("POST", "/api/datasets/{owner}/{dataset}/ingest", "upload"),
    "read_image": ("GET", "/api/images/{imageId}", "default"),
    "replace_labels": ("PATCH", "/api/images/{imageId}", "default"),
    "predict_image": ("POST", "/api/images/{imageId}/predict", "predict"),
    "start_batch": ("POST", "/api/datasets/{owner}/{dataset}/predict/batch", "predict"),
    "read_batch": ("GET", "/api/datasets/{owner}/{dataset}/predict/batch", "predict"),
    "save_export": ("POST", "/api/datasets/{owner}/{dataset}/export", "export"),
    "read_export": ("GET", "/api/datasets/{owner}/{dataset}/export", "export"),
    "create_project": ("POST", "/api/projects", "default"),
    "create_model": ("POST", "/api/models", "default"),
    "read_model": ("GET", "/api/models/{owner}/{project}/{model}", "default"),
    "start_training": ("POST", "/api/training/start", "training"),
    "read_training": ("GET", "/api/models/{owner}/{project}/{model}/training", "training"),
    "model_files": ("GET", "/api/models/{owner}/{project}/{model}/files", "download"),
    "cancel_training": ("DELETE", "/api/models/{owner}/{project}/{model}/training", "training"),
    "gpu_availability": ("GET", "/api/training/gpu-availability", "training"),
    "list_images": ("GET", "/api/datasets/{owner}/{dataset}/images", "default"),
    "compare_versions": ("GET", "/api/datasets/{owner}/{dataset}/versions/compare", "default"),
}


def render(contract: Path = CONTRACT) -> str:
    raw = (contract / "openapi.json").read_bytes()
    provenance = json.loads((contract / "provenance.json").read_text())
    if hashlib.sha256(raw).hexdigest() != provenance["sha256"]:
        raise ValueError("Platform snapshot SHA-256 mismatch")
    document = json.loads(raw)
    if document["info"]["version"] != "0.1.107" or document["openapi"] != "3.2.0":
        raise ValueError("Platform contract version mismatch")
    routes = {}
    for name, (method, path, category) in OPERATIONS.items():
        operation = document["paths"][path][method.lower()]
        routes[name] = {
            "method": method,
            "path": path,
            "category": category,
            "operation_id": operation["operationId"],
            "parameters": operation.get("parameters", []),
            "request": operation.get("requestBody"),
            "responses": {k: v for k, v in operation["responses"].items() if k.startswith("2")},
        }
    return json.dumps(routes, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = render()
    output = CONTRACT / "operations.json"
    if args.check:
        if output.read_text() != generated:
            raise SystemExit(
                "Platform client drift: regenerate packages/platform/generate_contract.py"
            )
    else:
        output.write_text(generated)
