"""Opt-in bounded MinIO transport for existing content-addressed worker ports."""

import hashlib
import io
import re
from dataclasses import dataclass

from minio import Minio

_CONTENT_KEY = re.compile(r"sha256/([0-9a-f]{2})/([0-9a-f]{64})")
_CHUNK_BYTES = 1024 * 1024
_MAX_OBJECT_BYTES = 256 * 1024 * 1024


def _digest(key: str) -> str:
    match = _CONTENT_KEY.fullmatch(key) if type(key) is str else None
    if match is None or match[1] != match[2][:2]:
        raise ValueError("invalid content-addressed object key")
    return match[2]


@dataclass(frozen=True, slots=True)
class BoundedMinioObjects:
    client: Minio
    bucket: str
    max_object_bytes: int

    def __post_init__(self) -> None:
        if type(self.bucket) is not str or not self.bucket:
            raise ValueError("object transport requires a trusted bucket")
        if type(self.max_object_bytes) is not int or not (
            1 <= self.max_object_bytes <= _MAX_OBJECT_BYTES
        ):
            raise ValueError("object transport requires a bounded byte limit")

    def read(self, key: str) -> bytes:
        expected = _digest(key)
        response = self.client.get_object(self.bucket, key)
        try:
            data = io.BytesIO()
            size = 0
            digest = hashlib.sha256()
            while True:
                amount = min(_CHUNK_BYTES, self.max_object_bytes - size + 1)
                chunk = response.read(amount)
                if type(chunk) is not bytes or len(chunk) > amount:
                    raise ValueError("invalid object transport response")
                if not chunk:
                    break
                size += len(chunk)
                if size > self.max_object_bytes:
                    raise ValueError("object exceeds transport byte limit")
                digest.update(chunk)
                data.write(chunk)
            if digest.hexdigest() != expected:
                raise ValueError("object content digest mismatch")
            return data.getvalue()
        finally:
            try:
                response.close()
            finally:
                response.release_conn()

    def put(self, key: str, data: bytes) -> None:
        expected = _digest(key)
        if type(data) is not bytes or len(data) > self.max_object_bytes:
            raise ValueError("object exceeds transport byte limit or is not bytes")
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("object content digest mismatch")
        self.client.put_object(
            self.bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type="application/octet-stream",
        )
