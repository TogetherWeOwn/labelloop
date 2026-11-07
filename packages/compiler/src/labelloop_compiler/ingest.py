"""Image-only ingest and byte-derived content-addressed storage."""

import hashlib
import io
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError, features

from labelloop_core.domain import SourceAsset

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_PIXELS = 100_000_000
INGEST_ENCODER = "Pillow JPEG quality=95 optimize=False progressive=False"


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...


class LocalObjectStore:
    """A filesystem implementation of the S3 content-key contract for offline jobs."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, key: str, data: bytes) -> None:
        parts = key.split("/")
        if (
            len(parts) != 3
            or parts[0] != "sha256"
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("invalid content-addressed key")
        target = self.root.joinpath(*parts)
        if (
            len(parts[1]) != 2
            or len(parts[2]) != 64
            or any(
                len(part) == 0 or any(char not in "0123456789abcdef" for char in part)
                for part in parts[1:]
            )
        ):
            raise ValueError("invalid content-addressed hash")
        if hashlib.sha256(data).hexdigest() != parts[2]:
            raise ValueError("content key does not match bytes")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError("immutable object already has different bytes")
            return
        target.write_bytes(data)


def process_image(
    data: bytes,
    *,
    asset_id: str,
    project_id: str,
    source_uri: str = "",
) -> tuple[SourceAsset, bytes]:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("image upload is empty or exceeds the 40 MiB boundary")
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if opened.width <= 0 or opened.height <= 0:
                raise ValueError("image dimensions must be positive")
            if opened.width * opened.height > MAX_PIXELS:
                raise ValueError("image exceeds the 100 megapixel boundary")
            opened.load()

            orientation = opened.getexif().get(274, 1)
            if orientation not in range(1, 9):
                raise ValueError("invalid EXIF orientation")
            normalized = ImageOps.exif_transpose(opened).convert("RGB")
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ValueError("image cannot be decoded completely") from error
    output = io.BytesIO()
    normalized.save(output, format="JPEG", quality=95, optimize=False, progressive=False)
    encoded = output.getvalue()
    digest = hashlib.sha256(encoded).hexdigest()
    return (
        SourceAsset(
            asset_id=asset_id,
            project_id=project_id,
            sha256=digest,
            object_key=f"sha256/{digest[:2]}/{digest}",
            width=normalized.width,
            height=normalized.height,
            exif_orientation=int(orientation),
            source_uri=source_uri,
        ),
        encoded,
    )


def ingest_image(store: ObjectStore, data: bytes, **identity: str) -> SourceAsset:
    asset, encoded = process_image(data, **identity)
    store.put(asset.object_key, encoded)
    return asset


def imaging_toolchain() -> dict[str, str | int | float | bool]:
    return {
        "decoder": "Pillow Image.open; ImageOps.exif_transpose; RGB",
        "encoder": INGEST_ENCODER,
        "pillow": Image.__version__,
        "libjpeg_turbo": features.version("libjpeg_turbo") or "unknown",
        "jpeg_compatibility": features.version_codec("jpg") or "unknown",
    }
