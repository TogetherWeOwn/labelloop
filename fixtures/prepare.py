#!/usr/bin/env python3
"""Reproduce committed fixtures in a separate output directory, without mutation."""

import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
from html import unescape
from html.parser import HTMLParser
from http.client import HTTPMessage
from pathlib import Path
from typing import BinaryIO
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image, ImageOps, features
from PIL import __version__ as pillow_version

HERE = Path(__file__).resolve().parent
ALLOWED_SOURCE_HOSTS = {"commons.wikimedia.org", "thumb.wikimedia.org", "upload.wikimedia.org"}
PROCESSING = {
    "pillow": pillow_version,
    "decoder": "Pillow Image.open; ImageOps.exif_transpose; convert to RGB",
    "encoder": "Pillow JPEG (libjpeg-turbo)",
    "libjpeg_turbo": features.version("libjpeg_turbo"),
    "jpeg_compatibility": features.version_codec("jpg"),
    "python": "3.12",
    "interpolation": "Image.Resampling.LANCZOS",
    "letterbox": {
        "width": 160,
        "height": 160,
        "mode": "RGB",
        "background": "#f0f0f0",
        "align": "center",
    },
    "jpeg": {"quality": 65, "optimize": True, "progressive": False},
}


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
    return unescape("".join(parser.parts)).replace("\xa0", " ").strip()


class AllowlistedRedirects(HTTPRedirectHandler):
    """Reject non-HTTPS or off-host redirect targets before connecting."""

    def redirect_request(  # type: ignore[override]
        self,
        req: Request,
        fp: BinaryIO,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        resolved_url = urljoin(req.full_url, newurl)
        parsed = urlparse(resolved_url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_SOURCE_HOSTS:
            raise ValueError("redirect URL must use HTTPS and an allowlisted host")
        return super().redirect_request(req, fp, code, msg, headers, resolved_url)


def response(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_SOURCE_HOSTS:
        raise ValueError("source URL must use HTTPS and an allowlisted host")
    request = Request(
        url,
        headers={
            "User-Agent": "labelloop-fixture-preparation/1.0 "
            "(https://github.com/TogetherWeOwn/labelloop)"
        },
    )
    with build_opener(AllowlistedRedirects).open(request, timeout=30) as reply:
        data: bytes = reply.read()
        return data


def resize(data: bytes) -> bytes:
    size = (160, 160)
    with Image.open(io.BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail(size, Image.Resampling.LANCZOS)
        thumbnail = Image.new("RGB", size, "#f0f0f0")
        thumbnail.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
        output = io.BytesIO()
        thumbnail.save(output, format="JPEG", quality=65, optimize=True, progressive=False)
        return output.getvalue()


def commons_metadata(item: dict[str, object]) -> str:
    for key in ("source_page", "file", "licence", "original_creator", "title"):
        if not isinstance(item.get(key), str):
            raise ValueError("invalid Commons record")
    source_page = urlparse(str(item["source_page"]))
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
    creator = plain_text(unescape(metadata["Artist"]["value"]))
    if creator != item["original_creator"]:
        if plain_text(str(item["original_creator"])) != creator:
            raise ValueError(f"source creator changed: {item['file']}")
        item["original_creator"] = creator
    if record["title"].removeprefix("File:") != item["title"]:
        raise ValueError(f"source title changed: {item['file']}")
    original_url = urlunparse(urlparse(info["url"])._replace(query=""))
    item["original_url"] = original_url
    thumbnail_url = info["thumburl"]
    if not isinstance(thumbnail_url, str):
        raise ValueError(f"invalid thumbnail URL: {item['file']}")
    thumbnail = urlunparse(urlparse(thumbnail_url)._replace(query=""))
    if item.get("thumbnail_url") != thumbnail:
        raise ValueError(f"source thumbnail changed: {item['file']}")
    return thumbnail


def prepare(manifest_path: Path, licence: str) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location(
        "check_fixtures", HERE.parent / "scripts" / "check-fixtures.py"
    )
    assert spec is not None and spec.loader is not None
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    validator.check_toolchain(manifest, manifest_path.parent.name)
    assert manifest["processing"] == PROCESSING, "actual imaging toolchain differs from manifest"
    output = Path(sys.argv[1]) / manifest_path.parent.name
    if output.exists():
        raise ValueError(f"output domain already exists: {output}")
    temporary = output.with_name(f".{output.name}.partial.{os.getpid()}")
    temporary.mkdir(parents=True, exist_ok=False)
    destination = temporary / "images"
    destination.mkdir()
    try:
        for item in manifest["images"]:
            if item["licence"] != licence:
                raise ValueError(f"unexpected licence: {item['licence']}")
            source_url = item["original_url"]
            if licence == "CC BY 4.0":
                source_url = commons_metadata(item)
            data = resize(response(source_url))
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError(f"source reproduction hash changed: {item['file']}")
            (destination / item["file"]).write_bytes(data)
        manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        (temporary / "manifest.json").write_text(manifest_bytes, encoding="utf-8")
        temporary.rename(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: fixtures/prepare.py NEW_OUTPUT_DIRECTORY")
    prepare(HERE / "commons-office-keyboards" / "manifest.json", "CC BY 4.0")
    prepare(HERE / "cc0-interface" / "manifest.json", "CC0-1.0")
