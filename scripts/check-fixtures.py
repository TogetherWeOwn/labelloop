#!/usr/bin/env python3
"""Validate committed fixture provenance, dimensions, hashes, and total size."""

import hashlib
import json
import sys
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image

LICENCES = {
    "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/deed.en",
}


def main() -> None:
    if len(sys.argv) == 2:
        fixture_root = Path(sys.argv[1])
    else:
        fixture_root = Path(__file__).resolve().parents[1] / "fixtures"
    total_bytes = 0
    image_count = 0
    for domain in ("commons-office-keyboards", "cc0-interface"):
        root = fixture_root / domain
        manifest = json.loads((root / "manifest.json").read_text())
        images = (root / "images").glob("*.jpg")
        actual = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in images}
        expected = {item["file"]: item["sha256"] for item in manifest["images"]}
        assert len(actual) >= 16, f"{domain}: need at least 16 images"
        assert actual == expected, f"{domain}: image names or hashes differ from manifest"
        image_count += len(actual)
        assert manifest["licence"], f"{domain}: missing licence"
        assert manifest["source"], f"{domain}: missing source"
        allowed_urls = {"upload.wikimedia.org"}
        for item in manifest["images"]:
            if domain == "commons-office-keyboards":
                assert manifest["licence_url"] == LICENCES[item["licence"]]
            source_url = item["original_url"]
            assert urlparse(source_url).scheme == "https"
            assert urlparse(source_url).hostname in allowed_urls, (
                f"Unhandled image host: {source_url}"
            )
            source_page = urlparse(item["source_page"])
            assert source_page.scheme == "https"
            assert source_page.hostname == "commons.wikimedia.org"
            assert parse_qs(source_page.query).get("curid")
            assert item["title"], f"Missing source title: {item['file']}"
        for item in manifest["images"]:
            source_url = item.get("source_url", item.get("original_url"))
            assert urlparse(source_url).hostname in allowed_urls, (
                f"Unhandled image host: {source_url}"
            )
            assert item["licence"] in LICENCES, f"Unknown fixture licence: {item['licence']}"
            creator = item.get("original_creator", item.get("creator"))
            assert creator, f"Missing recorded source creator: {item['file']}"
            with Image.open(BytesIO((root / "images" / item["file"]).read_bytes())) as image:
                assert image.size == (160, 160), f"{domain}/{item['file']}: wrong size"
                assert image.mode == "RGB"
            total_bytes += (root / "images" / item["file"]).stat().st_size
    assert total_bytes <= 5_000_000, f"fixtures are too large: {total_bytes} bytes"
    print(
        f"PASS: 2 domains, {image_count} provenance-recorded images, "
        f"{total_bytes:,} bytes, all hashes valid"
    )


if __name__ == "__main__":
    main()
