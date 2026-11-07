#!/usr/bin/env python3
"""Validate committed fixture provenance, dimensions, hashes, and total size."""

import hashlib
import json
from io import BytesIO
from pathlib import Path

from PIL import Image


def main() -> None:
    fixture_root = Path(__file__).resolve().parents[1] / "fixtures"
    total_bytes = 0
    for domain in ("coco-office", "cc0-interface"):
        root = fixture_root / domain
        manifest = json.loads((root / "manifest.json").read_text())
        images = (root / "images").glob("*.jpg")
        actual = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in images}
        expected = {item["file"]: item["sha256"] for item in manifest["images"]}
        assert len(actual) >= 16, f"{domain}: need at least 16 images"
        assert actual == expected, f"{domain}: image names or hashes differ from manifest"
        assert manifest["licence"], f"{domain}: missing licence"
        assert manifest["source"], f"{domain}: missing source"
        for item in manifest["images"]:
            source_url = item.get("source_url", item.get("original_url"))
            assert source_url.startswith(("http://images.cocodataset.org/", "https://"))
            assert item["licence"]
            assert item["licence"]
            with Image.open(BytesIO((root / "images" / item["file"]).read_bytes())) as image:
                assert image.size == (160, 160), f"{domain}/{item['file']}: wrong size"
                assert image.mode == "RGB"
            total_bytes += (root / "images" / item["file"]).stat().st_size
    assert total_bytes <= 5_000_000, f"fixtures are too large: {total_bytes} bytes"
    print(f"PASS: 2 domains, 32 licensed images, {total_bytes:,} bytes, all SHA-256 checks passed")


if __name__ == "__main__":
    main()
