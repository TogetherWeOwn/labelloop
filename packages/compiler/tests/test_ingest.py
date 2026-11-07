"""Image normalization and immutable-content tests."""

import hashlib
import io
import os
import struct
import zlib
from pathlib import Path

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


def test_object_store_only_publishes_complete_bytes(tmp_path, monkeypatch):
    data = image_bytes()
    digest = hashlib.sha256(data).hexdigest()
    key = f"sha256/{digest[:2]}/{digest}"
    target = tmp_path / key
    original_link = os.link
    published = []

    def publish(source, destination):
        assert Path(source).read_bytes() == data
        assert Path(destination) == target
        assert not target.exists()
        original_link(source, destination)
        published.append(target.read_bytes())

    monkeypatch.setattr("labelloop_compiler.ingest.os.link", publish)
    LocalObjectStore(tmp_path).put(key, data)
    assert published == [data]
    assert list(target.parent.iterdir()) == [target]


def test_object_store_failed_staging_leaves_no_object_and_can_retry(tmp_path, monkeypatch):
    data = image_bytes()
    digest = hashlib.sha256(data).hexdigest()
    key = f"sha256/{digest[:2]}/{digest}"
    target = tmp_path / key
    store = LocalObjectStore(tmp_path)

    def fail_sync(fd):
        assert not target.exists()
        raise OSError("interrupted staging")

    with monkeypatch.context() as patch:
        patch.setattr("labelloop_compiler.ingest.os.fsync", fail_sync)
        with pytest.raises(OSError, match="interrupted staging"):
            store.put(key, data)
    assert not target.exists()
    assert list(target.parent.iterdir()) == []
    store.put(key, data)
    assert target.read_bytes() == data


@pytest.mark.parametrize("concurrent_bytes", [None, b"different bytes"])
def test_object_store_concurrent_publication_never_overwrites(
    tmp_path, monkeypatch, concurrent_bytes
):
    data = image_bytes()
    digest = hashlib.sha256(data).hexdigest()
    key = f"sha256/{digest[:2]}/{digest}"
    target = tmp_path / key
    winner = data if concurrent_bytes is None else concurrent_bytes
    original_link = os.link
    published_inodes = []

    def competing_publish(source, destination):
        target.write_bytes(winner)
        published_inodes.append(target.stat().st_ino)
        original_link(source, destination)

    monkeypatch.setattr("labelloop_compiler.ingest.os.link", competing_publish)
    if winner == data:
        LocalObjectStore(tmp_path).put(key, data)
    else:
        with pytest.raises(ValueError, match="immutable object"):
            LocalObjectStore(tmp_path).put(key, data)
    assert target.read_bytes() == winner
    assert published_inodes == [target.stat().st_ino]
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("orientation", [0, 9])
def test_undefined_exif_orientation_is_rejected(orientation):
    exif = Image.Exif()
    exif[274] = orientation
    output = io.BytesIO()
    Image.new("RGB", (12, 8), "blue").save(output, format="JPEG", exif=exif)
    with pytest.raises(ValueError, match="invalid EXIF orientation"):
        process_image(output.getvalue(), asset_id="a", project_id="p")


def test_invalid_image_rejected() -> None:
    with pytest.raises(ValueError, match="decoded"):
        process_image(b"not-image", asset_id="a", project_id="p")
    with pytest.raises(ValueError, match="upload"):
        process_image(b"", asset_id="a", project_id="p")


@pytest.mark.parametrize(
    ("width", "height", "message"),
    [(10001, 10000, "megapixel"), (20000, 10000, "pixel safety")],
)
def test_oversized_header_is_rejected_before_pixel_decode(
    monkeypatch: pytest.MonkeyPatch, width, height, message
):
    output = io.BytesIO()
    Image.new("RGB", (10, 10)).save(output, format="PNG")
    data = bytearray(output.getvalue())
    data[16:24] = width.to_bytes(4, "big") + height.to_bytes(4, "big")
    data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))

    def decode_is_forbidden(self: Image.Image):
        raise AssertionError("oversized image was decoded")

    monkeypatch.setattr(Image.Image, "load", decode_is_forbidden)
    with pytest.raises(ValueError, match=message):
        process_image(bytes(data), asset_id="large", project_id="p")


def test_fixture_image_toolchain_record():
    from labelloop_compiler.ingest import imaging_toolchain

    toolchain = imaging_toolchain()
    assert toolchain["decoder"] == "Pillow Image.open; ImageOps.exif_transpose; RGB"
    assert toolchain["pillow"] == "12.2.0"
