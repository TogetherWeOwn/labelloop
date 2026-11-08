"""Versioned, strict JSON contracts; decoding never confers authority on evidence.

Only caller-selected, closed root types are allowed. Nested constructors are
selected from static field annotations, never from type names in input data.
"""

import json
import math
import types
from dataclasses import fields, is_dataclass
from typing import Any, Literal, Union, cast, get_args, get_origin, get_type_hints

from .audit import AuditSample
from .drift import AcceptanceState
from .evaluation_lifecycle import CandidateSelection, FinalTestState
from .qualification import QualificationContext, QualificationEvidence, QualificationPolicy
from .qualification_data import canonical_record

SCHEMA = "labelloop.qualification.v1"
MAX_RECORD_BYTES = 2_000_000
_ROOTS = (
    AuditSample,
    AcceptanceState,
    CandidateSelection,
    FinalTestState,
    QualificationContext,
    QualificationEvidence,
    QualificationPolicy,
)
type QualificationRecord = (
    AuditSample
    | AcceptanceState
    | CandidateSelection
    | FinalTestState
    | QualificationContext
    | QualificationEvidence
    | QualificationPolicy
)


def dumps(record: QualificationRecord) -> str:
    if type(record) not in _ROOTS:
        raise ValueError("unknown qualification root record")
    encoded = canonical_record(
        {
            "schema": SCHEMA,
            "record_type": type(record).__name__,
            "payload": record,
        }
    )
    if len(encoded.encode()) > MAX_RECORD_BYTES:
        raise ValueError("qualification record exceeds the durable record bound")
    # Enforce the same strict shapes on both sides of the storage boundary.
    _decode(json.loads(encoded)["payload"], type(record))
    return encoded


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate qualification JSON key")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError(f"non-finite qualification JSON constant: {value}")


def _decode(value: object, annotation: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, types.UnionType):
        for choice in args:
            try:
                return _decode(value, choice)
            except (ValueError, TypeError):
                pass
        raise ValueError("qualification field does not match its declared union")
    if origin is Literal:
        if not any(type(value) is type(choice) and value == choice for choice in args):
            raise ValueError("unknown qualification literal")
        return value
    if annotation is type(None):
        if value is not None:
            raise ValueError("expected null qualification field")
        return None
    if annotation in (str, int, bool):
        if type(value) is not annotation:
            raise ValueError("invalid qualification scalar type")
        return value
    if annotation is float:
        if type(value) not in (int, float) or not math.isfinite(cast(float, value)):
            raise ValueError("expected a finite qualification number")
        # Preserve JSON's integer/float spelling so roundtrips do not change content hashes.
        return value
    if origin in (tuple, frozenset):
        if not isinstance(value, list):
            raise ValueError("expected a qualification array")
        if origin is tuple and len(args) > 1 and args[1] is not Ellipsis:
            if len(value) != len(args):
                raise ValueError("qualification tuple has the wrong arity")
            return tuple(_decode(item, kind) for item, kind in zip(value, args, strict=True))
        converted = tuple(_decode(item, args[0]) for item in value)
        if origin is frozenset:
            if len(set(converted)) != len(converted):
                raise ValueError("duplicate qualification set member")
            return frozenset(converted)
        return converted
    if origin is dict:
        if not isinstance(value, dict):
            raise ValueError("expected a qualification mapping")
        return {_decode(key, args[0]): _decode(item, args[1]) for key, item in value.items()}
    if isinstance(annotation, type) and is_dataclass(annotation):
        names = {item.name for item in fields(annotation)}
        if not isinstance(value, dict) or set(value) != names:
            raise ValueError("qualification fields must match the exact versioned contract")
        hints = get_type_hints(annotation)
        return annotation(**{name: _decode(value[name], hints[name]) for name in names})
    raise ValueError("unsupported qualification field type")


def loads[
    Record: (
        AuditSample,
        AcceptanceState,
        CandidateSelection,
        FinalTestState,
        QualificationContext,
        QualificationEvidence,
        QualificationPolicy,
    )
](payload: str, record_type: type[Record]) -> Record:
    if record_type not in _ROOTS or len(payload.encode()) > MAX_RECORD_BYTES:
        raise ValueError("unknown or oversized qualification record")
    try:
        data = json.loads(payload, object_pairs_hook=_pairs, parse_constant=_constant)
        if (
            not isinstance(data, dict)
            or set(data) != {"schema", "record_type", "payload"}
            or data["schema"] != SCHEMA
            or data["record_type"] != record_type.__name__
        ):
            raise ValueError("unknown qualification schema or record type")
        return cast(Record, _decode(data["payload"], record_type))
    except (TypeError, RecursionError, OverflowError) as error:
        raise ValueError("malformed qualification record") from error
