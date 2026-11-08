"""Versioned, bounded recovery evidence. A trusted manifest digest is kept out of band."""

import hashlib
import json
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

FORMAT_VERSION = 1
SCHEMA_REVISION = "0002_local_models"
MAX_OBJECT_BYTES = 256 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024
MAX_ROWS = 100_000
DIGEST = re.compile(r"[0-9a-f]{64}")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def object_key(sha256: str) -> str:
    if not DIGEST.fullmatch(sha256):
        raise ValueError("invalid content digest")
    return f"sha256/{sha256[:2]}/{sha256}"


def reject_transport_secrets(value: Any) -> None:
    """Defense in depth, not a credential exporter or a substitute for an inventory."""
    if isinstance(value, dict):
        for key, item in value.items():
            if re.search(
                r"(^|_)(password|token|secret|private_key|api_key|authorization)($|_)",
                key,
                re.IGNORECASE,
            ):
                raise ValueError("credential-bearing fields cannot enter a backup manifest")
            reject_transport_secrets(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            reject_transport_secrets(item)
    elif isinstance(value, str):
        if "PRIVATE KEY-----" in value or value.startswith(("ul_", "sk-", "Bearer ")):
            raise ValueError("credential material cannot enter a backup manifest")
        if "://" in value:
            url = urlsplit(value)
            if url.query or url.fragment or url.username or url.password:
                raise ValueError("signed URLs and credential-bearing URIs are not durable identity")


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TableInventory(Record):
    columns: list[str]
    rows: list[dict[str, Any]] = Field(max_length=MAX_ROWS)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ObjectInventory(Record):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=1, le=MAX_OBJECT_BYTES)
    roles: list[str] = Field(min_length=1)


class AuditSeal(Record):
    count: int = Field(ge=0, le=MAX_ROWS)
    head: str = Field(pattern=r"^[0-9a-f]{64}$")


class EncryptionMetadata(Record):
    # v1 drills contain licence-clean fixture data only. No operational crypto is invented.
    mode: Literal["fixture-plaintext"] = "fixture-plaintext"
    fixture_only: Literal[True] = True
    key_custody: Literal["external-prerequisite-no-key-export"] = (
        "external-prerequisite-no-key-export"
    )
    provider_and_private_keys_included: Literal[False] = False


class BackupManifest(Record):
    format_version: Literal[1] = FORMAT_VERSION
    schema_revision: Literal["0002_local_models"] = SCHEMA_REVISION
    inventory_version: Literal["m1-m2-v1"] = "m1-m2-v1"
    toolchain: dict[str, str | int | float | bool]
    encryption: EncryptionMetadata
    tables: dict[str, TableInventory]
    objects: dict[str, ObjectInventory]
    audit: dict[str, AuditSeal]


def audit_seal(rows: list[dict]) -> AuditSeal:
    head = "0" * 64
    for row in rows:
        head = digest(bytes.fromhex(head) + encode(row))
    return AuditSeal(count=len(rows), head=head)


def parse_manifest(data: bytes, *, expected_sha256: str) -> BackupManifest:
    if len(data) > MAX_SNAPSHOT_BYTES or digest(data) != expected_sha256:
        raise ValueError("backup manifest digest or size mismatch")
    manifest = BackupManifest.model_validate_json(data)
    reject_transport_secrets(manifest.model_dump())
    return manifest
