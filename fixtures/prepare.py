#!/usr/bin/env python3
"""Reproduce committed fixtures in a separate output directory, without mutation."""

import hashlib
import io
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent


class Text(HTMLParser):
    """Read a creator's plain-text attribution, not arbitrary markup."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain_text(markup: str) -> str:
    parser = Text()
    parser.feed(markup)
    return "".join(parser.parts).replace("\xa0", " ").strip()


def response(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "labelloop-fixture-preparation/1.0 "
            "(https://github.com/TogetherWeOwn/labelloop)"
        },
    )
    with urlopen(request, timeout=30) as reply:
        data: bytes = reply.read()
        return data


def resize(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((160, 160), Image.Resampling.LANCZOS)
        thumbnail = Image.new("RGB", (160, 160), "#f0f0f0")
        thumbnail.paste(image, ((160 - image.width) // 2, (160 - image.height) // 2))
        output = io.BytesIO()
        thumbnail.save(output, format="JPEG", quality=65, optimize=True, progressive=False)
        return output.getvalue()


def commons_metadata(item: dict[str, str]) -> str:
    source_page = urlparse(item["source_page"])
    page_ids = parse_qs(source_page.query).get("curid", [])
    if (
        source_page.scheme != "https"
        or source_page.hostname != "commons.wikimedia.org"
        or len(page_ids) != 1
    ):
        raise ValueError(f"unexpected Commons source page: {item['file']}")
    query = urlencode(
        {
            "action": "query",
            "format": "json",
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": "250",
            "pageids": page_ids[0],
        },
    )
    url = "https://commons.wikimedia.org/w/api.php?" + query
    record = next(iter(json.loads(response(url))["query"]["pages"].values()))
    info = record["imageinfo"][0]
    metadata = info["extmetadata"]
    if metadata["LicenseShortName"]["value"] != item["licence"]:
        raise ValueError(f"source licence changed: {item['file']}")
    if not metadata["LicenseUrl"]["value"].rstrip("/").startswith("https://creativecommons.org/"):
        raise ValueError(f"unexpected licence URL: {item['file']}")
    creator = plain_text(metadata["Artist"]["value"])
    if creator != item["original_creator"]:
        raise ValueError(f"source creator changed: {item['file']}")
    if record["title"].removeprefix("File:") != item["title"]:
        raise ValueError(f"source title changed: {item['file']}")
    item["original_url"] = urlunparse(urlparse(info["url"])._replace(query=""))
    thumbnail_url = info["thumburl"]
    if not isinstance(thumbnail_url, str):
        raise ValueError(f"invalid thumbnail URL: {item['file']}")
    return urlunparse(urlparse(thumbnail_url)._replace(query=""))


def prepare(manifest_path: Path, licence: str) -> None:
    manifest = json.loads(manifest_path.read_text())
    output = Path(sys.argv[1]) / manifest_path.parent.name
    if output.exists():
        raise ValueError(f"output domain already exists: {output}")
    destination = output / "images"
    destination.mkdir(parents=True)
    for item in manifest["images"]:
        if item["licence"] != licence:
            raise ValueError(f"unexpected licence: {item['licence']}")
        source_url = item["original_url"]
        if licence == "CC BY 4.0":
            source_url = commons_metadata(item)
            item["thumbnail_url"] = source_url
        parsed = urlparse(source_url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "upload.wikimedia.org",
            "thumb.wikimedia.org",
        }:
            raise ValueError(f"unexpected source host: {item['file']}")
        data = resize(response(source_url))
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"source reproduction hash changed: {item['file']}")
        (destination / item["file"]).write_bytes(data)
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    (output / "manifest.json").write_text(manifest_bytes)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: fixtures/prepare.py NEW_OUTPUT_DIRECTORY")
    prepare(HERE / "commons-office-keyboards" / "manifest.json", "CC BY 4.0")
    prepare(HERE / "cc0-interface" / "manifest.json", "CC0-1.0")
