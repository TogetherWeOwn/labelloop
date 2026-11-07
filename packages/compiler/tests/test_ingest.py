"""Image normalization and immutable-content tests."""

import io

import pytest
from PIL import Image

from labelloop_compiler.ingest import LocalObjectStore, ingest_image, process_image


def image_bytes(width=200, height=150, color=(12, 62, 120)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="JPEG", quality=95)
    return output.getvalue()


def test_exif_ingest_dimensions_and_content_key(tmp_path):
    image = Image.new("RGB", (12, 8), "blue")
    exif = Image.Exif()
    exif[274] = 6
    source = io.BytesIO()
    image.save(source, format="JPEG", exif=exif)
    asset, encoded = process_image(source.getvalue(), asset_id="a", project_id="p")
    assert (asset.width, asset.height) == (8, 12)
    assert asset.exif_orientation == 6
    store = LocalObjectStore(tmp_path)
    result = ingest_image(store, source.getvalue(), asset_id="a", project_id="p")
    assert result == asset
    assert (tmp_path / asset.object_key).read_bytes() == encoded
    assert store.put(asset.object_key, encoded) is None


def test_repeated_identical_ingest_and_different_content_key(tmp_path):
    store = LocalObjectStore(tmp_path)
    first = ingest_image(store, image_bytes(), asset_id="a", project_id="p")
    second = ingest_image(store, image_bytes(), asset_id="b", project_id="p")
    assert first.sha256 == second.sha256 and first.object_key == second.object_key
    with pytest.raises(ValueError, match="content key"):
        store.put(first.object_key, b"different")


def test_invalid_image_rejected() -> None:
    with pytest.raises(ValueError, match="decoded"):
        process_image(b"not-image", asset_id="a", project_id="p")
    with pytest.raises(ValueError, match="upload"):
        process_image(b"", asset_id="a", project_id="p")


def test_fixture_image_toolchain_record():
    from labelloop_compiler.ingest import imaging_toolchain

    toolchain = imaging_toolchain()
    assert toolchain["decoder"] == "Pillow Image.open; ImageOps.exif_transpose; RGB"
    assert toolchain["pillow"] == "12.2.0"
