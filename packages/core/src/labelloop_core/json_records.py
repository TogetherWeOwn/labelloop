"""Lossless JSON normalization for immutable worker inputs and evidence."""

import json
from typing import Any


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("colliding JSON object keys")
        result[key] = value
    return result


def canonical_json(value: object) -> bytes:
    # Check every object after JSON key coercion, before duplicate keys can be lost.
    normalized = json.loads(json.dumps(value, allow_nan=False), object_pairs_hook=_unique_object)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
