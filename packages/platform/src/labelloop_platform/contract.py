"""Pinned route metadata and structural wire checks, not domain acceptance gates."""

import hashlib
import json
import math
import re
from importlib.resources import files
from typing import Any

from .ports import Json, Operation, PlatformRequest

ROOT = files("labelloop_platform").joinpath("contract")
SNAPSHOT_SHA256 = "141c6c80558c733e362286920be8b7a73f93f5c43ce5dd93bf4dea3da8c816ed"
_raw = ROOT.joinpath("openapi.json").read_bytes()
if hashlib.sha256(_raw).hexdigest() != SNAPSHOT_SHA256:
    raise RuntimeError("Platform contract integrity failure")
DOCUMENT: dict[str, Any] = json.loads(_raw)
ROUTES: dict[str, Any] = json.loads(ROOT.joinpath("operations.json").read_text())


class WireError(ValueError):
    def __init__(self) -> None:
        super().__init__("Platform wire contract rejected")


def resolve(schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/components/schemas/"):
            raise WireError()
        schema = DOCUMENT["components"]["schemas"][ref.rsplit("/", 1)[1]]
    return schema


def validate_shape(value: Json, schema: dict[str, Any], depth: int = 0) -> None:
    """Validate the snapshot's structural subset; business invariants remain in consumers."""
    if depth > 64:
        raise WireError()
    schema = resolve(schema)
    if "not" in schema:
        try:
            validate_shape(value, schema["not"], depth + 1)
        except WireError:
            pass
        else:
            raise WireError()
    for branch in schema.get("allOf", []):
        validate_shape(value, branch, depth + 1)
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            matches = 0
            for branch in schema[keyword]:
                try:
                    validate_shape(value, branch, depth + 1)
                    matches += 1
                except WireError:
                    pass
            if matches == 0 or (keyword == "oneOf" and matches != 1):
                raise WireError()
    types = {
        "null": value is None,
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }
    expected = schema.get("type")
    if isinstance(expected, str) and not types[expected]:
        raise WireError()
    if isinstance(expected, list) and not any(types[t] for t in expected):
        raise WireError()
    if "enum" in schema and value not in schema["enum"]:
        raise WireError()
    if "const" in schema and value != schema["const"]:
        raise WireError()
    if isinstance(value, float) and not math.isfinite(value):
        raise WireError()
    if isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", float("inf")):
            raise WireError()
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise WireError()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
            raise WireError()
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", math.inf):
            raise WireError()
        for item in value:
            validate_shape(item, schema.get("items", {}), depth + 1)
    if isinstance(value, dict):
        if not set(schema.get("required", [])).issubset(value):
            raise WireError()
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                validate_shape(item, properties[key], depth + 1)
            elif additional is False:
                raise WireError()
            elif isinstance(additional, dict):
                validate_shape(item, additional, depth + 1)


def prepare(request: PlatformRequest) -> tuple[dict[str, Any], str]:
    if not isinstance(request.operation, Operation):
        raise WireError()
    route = ROUTES[request.operation.value]
    parameters = route["parameters"]
    expected_path = {p["name"] for p in parameters if p["in"] == "path"}
    if set(request.path) != expected_path:
        raise WireError()
    path = route["path"]
    for name, value in request.path.items():
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise WireError()
        path = path.replace("{" + name + "}", value)
    query = {p["name"]: p for p in parameters if p["in"] == "query"}
    if set(request.query) - query.keys():
        raise WireError()
    for name, parameter in query.items():
        if parameter.get("required") and name not in request.query:
            raise WireError()
        if name in request.query:
            validate_shape(request.query[name], parameter["schema"])
    spec = route["request"]
    if spec:
        if request.body is None and spec.get("required"):
            raise WireError()
        if request.body is not None:
            validate_shape(request.body, spec["content"]["application/json"]["schema"])
    elif request.body is not None:
        raise WireError()
    body = request.body or {}
    if request.operation == Operation.INGEST and (
        not body.get("sessionId")
        or "classMapping" not in body
        or {"sourceUrl", "reference", "targetSplit"}.intersection(body)
    ):
        raise WireError()
    if request.operation == Operation.CREATE_DATASET and (
        body.get("visibility") != "private" or body.get("task") != "detect"
    ):
        raise WireError()
    if request.operation == Operation.START_TRAINING and (
        not body.get("gpuType") or body.get("captureDatasetVersion") is not True
    ):
        raise WireError()
    # Also rejects non-JSON values and non-finite additional properties.
    json.dumps([request.body, request.query], allow_nan=False)
    return route, path
