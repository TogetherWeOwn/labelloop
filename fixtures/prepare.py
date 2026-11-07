#!/usr/bin/env python3
"""Provision fixture bytes from the explicitly selected provenance manifest.

This utility is optional; normal tests use the committed images and no network.
The full COCO annotation archive URL and hash are pinned in the source manifest.
"""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent


def response(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "labelloop-fixture-preparation/1.0 "
            "(https://github.com/TogetherWeOwn/labelloop)"
        },
    )
    with urlopen(request, timeout=30) as reply:
        return reply.read()


def resize(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((160, 160), Image.Resampling.LANCZOS)
        thumbnail = Image.new("RGB", (160, 160), "#f0f0f0")
        thumbnail.paste(image, ((160 - image.width) // 2, (160 - image.height) // 2))
        output = io.BytesIO()
        thumbnail.save(output, format="JPEG", quality=65, optimize=True, progressive=False)
        return output.getvalue()


def coco() -> None:
    manifest_path = HERE / "coco-office" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    cache = HERE / "coco-office" / ".annotations.zip"
    if not cache.exists():
        cache.write_bytes(response(manifest["annotation_source"]))
    digest = hashlib.sha256(cache.read_bytes()).hexdigest()
    assert digest == manifest["annotation_archive_sha256"], "unexpected COCO archive"
    with zipfile.ZipFile(cache) as archive:
        annotations = json.loads(archive.read("annotations/instances_val2017.json"))
        categories = {item["id"]: item["name"] for item in annotations["categories"]}
        metadata = {item["id"]: item for item in annotations["images"]}
    destination = manifest_path.parent / "images"
    destination.mkdir(exist_ok=True)
    for item in manifest["images"]:
        record = metadata[item["coco_id"]]
        item["source_url"] = record["coco_url"]
        item["original_source_page"] = record["flickr_url"].replace("http://", "https://")
        item["licence_id"] = record["license"]
        item["original_creator"] = "See the Flickr source page for the image creator"
        item["objects"] = sorted(
            {
                categories[entry["category_id"]]
                for entry in annotations["annotations"]
                if entry["image_id"] == item["coco_id"]
            }
        )
        data = resize(response(item["source_url"]))
        item["sha256"] = hashlib.sha256(data).hexdigest()
        (destination / item["file"]).write_bytes(data)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def cc0() -> None:
    manifest_path = HERE / "cc0-interface" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    destination = manifest_path.parent / "images"
    destination.mkdir(exist_ok=True)
    for item in manifest["images"]:
        assert item["licence"] == "CC0-1.0"
        data = resize(response(item["original_url"]))
        item["sha256"] = hashlib.sha256(data).hexdigest()
        (destination / item["file"]).write_bytes(data)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    coco()
    cc0()
