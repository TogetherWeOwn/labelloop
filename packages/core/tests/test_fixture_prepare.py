import importlib.util
import json
from http.client import HTTPMessage
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

import pytest

ROOT = Path(__file__).parents[3]


def load_module(name: str, path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load_module("fixture_prepare", ROOT / "fixtures" / "prepare.py")
check_fixtures = load_module("check_fixtures", ROOT / "scripts" / "check-fixtures.py")


def test_redirect_target_is_rejected_before_download() -> None:
    handler = next(
        handler
        for handler in prepare.build_opener(prepare.AllowlistedRedirects).handlers
        if isinstance(handler, prepare.AllowlistedRedirects)
    )
    with pytest.raises(ValueError, match="allowlisted host"):
        handler.redirect_request(
            Request("https://upload.wikimedia.org/image.jpg"),
            None,
            302,
            "Found",
            HTTPMessage(),
            "http://example.org/image.jpg",
        )


def test_relative_allowed_redirect() -> None:
    request = prepare.AllowlistedRedirects().redirect_request(
        Request("https://upload.wikimedia.org/image.jpg"),
        None,
        302,
        "Found",
        HTTPMessage(),
        "/next/image.jpg",
    )
    assert request is not None
    assert request.full_url == "https://upload.wikimedia.org/next/image.jpg"


def test_bad_initial_source_is_rejected() -> None:
    with pytest.raises(ValueError, match="allowlisted host"):
        prepare.response("http://upload.wikimedia.org/image.jpg")


def test_atomic_output_cleanup(tmp_path: Path) -> None:
    manifest_path = Path(prepare.HERE) / "cc0-interface" / "manifest.json"
    output_root = tmp_path / "new" / "output"
    with (
        patch.object(prepare.sys, "argv", ["prepare.py", str(output_root)]),
        patch.object(prepare, "response", side_effect=RuntimeError("simulated drift")),
        pytest.raises(RuntimeError, match="simulated drift"),
    ):
        prepare.prepare(manifest_path, "CC0-1.0")
    assert not (output_root / "cc0-interface").exists()
    assert not list(output_root.glob(".cc0-interface.partial*"))
    assert manifest_path.exists()


def test_commons_metadata_checks_recorded_thumbnail() -> None:
    item = {
        "file": "keyboard-01.jpg",
        "source_page": "https://commons.wikimedia.org/w/index.php?curid=1",
        "licence": "CC BY 4.0",
        "original_creator": "Author <b>name</b>",
        "title": "Keyboard.jpg",
        "original_url": "https://upload.wikimedia.org/original.jpg",
        "thumbnail_url": "https://upload.wikimedia.org/thumb.jpg",
    }
    response = {
        "query": {
            "pages": {
                "1": {
                    "title": "File:Keyboard.jpg",
                    "imageinfo": [
                        {
                            "url": "https://upload.wikimedia.org/original.jpg",
                            "thumburl": "https://upload.wikimedia.org/thumb.jpg",
                            "extmetadata": {
                                "LicenseShortName": {"value": "CC BY 4.0"},
                                "LicenseUrl": {
                                    "value": "https://creativecommons.org/licenses/by/4.0/"
                                },
                                "Artist": {"value": "Author &lt;b&gt;name&lt;/b&gt;"},
                            },
                        }
                    ],
                }
            }
        }
    }
    with patch.object(prepare, "response", return_value=json.dumps(response).encode()):
        assert prepare.commons_metadata(item) == item["thumbnail_url"]
        assert item["original_creator"] == "Author name"
    item["thumbnail_url"] = "https://upload.wikimedia.org/changed.jpg"
    with (
        patch.object(prepare, "response", return_value=json.dumps(response).encode()),
        pytest.raises(ValueError, match="thumbnail changed"),
    ):
        prepare.commons_metadata(item)


def test_licence_allowlist_rejects_changed_record(tmp_path: Path) -> None:
    root = tmp_path / "fixtures"
    for domain in ("cc0-interface", "commons-office-keyboards"):
        source = ROOT / "fixtures" / domain
        (root / domain).mkdir(parents=True)
        (root / domain / "images").symlink_to(source / "images")
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        manifest["images"][0]["licence"] = "All rights reserved"
        (root / domain / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with patch("sys.argv", ["check-fixtures.py", str(root)]):
        with pytest.raises(AssertionError, match="Unknown fixture licence"):
            check_fixtures.main()
