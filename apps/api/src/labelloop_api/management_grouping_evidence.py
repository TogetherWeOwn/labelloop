"""Byte-derived input for the delivered grouping engine; no assignment authority.

A future canonical publisher must resolve ownership, source/revision pins, declared
source groups and persisted edges in its own transaction. These observations alone
never approve a source, lock a split or establish that a collection is current.
"""

import hashlib
import io

from PIL import Image, UnidentifiedImageError

from labelloop_compiler.ingest import MAX_PIXELS, MAX_UPLOAD_BYTES
from labelloop_core.domain import SourceAsset
from labelloop_core.grouping import METADATA_KEYS, AssetEvidence, phash_grayscale


def collect_source_grouping_evidence(
    asset: SourceAsset,
    data: bytes,
    *,
    metadata: dict[str, str],
    class_counts: dict[str, int],
) -> AssetEvidence:
    """Hash the canonical RGB raster, never a derivative or reoriented upload.

    Metadata and class counts are trusted caller-resolved canonical observations,
    not model/HTTP fields. Empty counts mean no observed class counts, not verified
    background. Embeddings remain absent until a versioned producer supplies them.
    The byte cap is conservative: large normalized originals need another bounded
    processing path, not a decoder-limit override.
    """
    if (
        not asset.asset_id
        or not asset.project_id
        or type(asset.width) is not int
        or type(asset.height) is not int
        or asset.width <= 0
        or asset.height <= 0
        or asset.width * asset.height > MAX_PIXELS
        or asset.object_key != f"sha256/{asset.sha256[:2]}/{asset.sha256}"
    ):
        raise ValueError("invalid canonical grouping source")
    if (
        not isinstance(metadata, dict)
        or not metadata.keys() <= METADATA_KEYS
        or any(type(value) is not str or not 0 < len(value) <= 4096 for value in metadata.values())
        or not isinstance(class_counts, dict)
        or any(
            type(key) is not str or not key or type(value) is not int or value < 0
            for key, value in class_counts.items()
        )
    ):
        raise ValueError("invalid canonical grouping observations")
    if type(data) is not bytes or not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("grouping source exceeds the bounded byte input")
    if hashlib.sha256(data).hexdigest() != asset.sha256:
        raise ValueError("grouping source bytes differ from the canonical hash")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format != "JPEG"
                or image.mode != "RGB"
                or image.size != (asset.width, asset.height)
                or image.getexif().get(274, 1) != 1
                or getattr(image, "n_frames", 1) != 1
            ):
                raise ValueError("grouping requires the normalized canonical RGB JPEG")
            image.load()
            phash = phash_grayscale(image)
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ValueError("grouping source cannot be decoded completely") from error
    return AssetEvidence(
        asset_id=asset.asset_id,
        sha256=asset.sha256,
        phash=phash,
        embedding=(),
        metadata=dict(metadata),
        class_counts=dict(class_counts),
    )
