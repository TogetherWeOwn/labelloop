"""Schema drift and boundary documentation checks; no provider or database access."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from labelloop_api import annotation_reads, auth
from labelloop_api.export_openapi import render_openapi
from labelloop_api.main import app

SNAPSHOT = Path(__file__).resolve().parents[1] / "openapi.json"


def test_snapshot_matches_production_schema_without_io(monkeypatch):
    unavailable = MagicMock(side_effect=AssertionError("schema export performed I/O"))
    monkeypatch.setattr(auth, "auth_store", unavailable)
    monkeypatch.setattr(annotation_reads, "annotation_engine", unavailable)
    rendered = render_openapi()
    assert rendered == SNAPSHOT.read_text(encoding="utf-8")
    assert render_openapi() == rendered
    unavailable.assert_not_called()


def test_annotation_contract_retains_uuid_bounds_and_redacted_errors():
    schema = json.loads(render_openapi())
    base = "/projects/{project_id}/assets/{asset_id}"
    for suffix, response in (
        ("annotations", "AnnotationSnapshot"),
        ("annotation-history", "AnnotationHistory"),
    ):
        route = schema["paths"][f"{base}/{suffix}"]
        assert set(route) == {"get"}
        operation = route["get"]
        ids = [item for item in operation["parameters"] if item["in"] == "path"]
        assert {item["name"] for item in ids} == {"project_id", "asset_id"}
        assert all(item["schema"]["format"] == "uuid" for item in ids)
        for status in ("401", "403", "404", "422", "503"):
            assert operation["responses"][status]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/BoundaryError"
            }
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{response}"
        }
    history = schema["paths"][f"{base}/annotation-history"]["get"]
    query = {
        item["name"]: item["schema"] for item in history["parameters"] if item["in"] == "query"
    }
    assert query["limit"]["minimum"] == 1 and query["limit"]["maximum"] == 100
    error = schema["components"]["schemas"]["BoundaryError"]
    assert error["properties"] == {"detail": {"title": "Detail", "type": "string"}}
    assert error["additionalProperties"] is False


def test_auth_contract_does_not_document_raw_validation_inputs():
    schema = json.loads(render_openapi())
    for path in ("/auth/login", "/auth/logout", "/auth/me"):
        for operation in schema["paths"][path].values():
            assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/BoundaryError"
            }
    password = schema["components"]["schemas"]["Login"]["properties"]["password"]
    assert password["writeOnly"] is True
    assert password["format"] == "password"


def test_export_does_not_enable_public_schema_or_docs(monkeypatch):
    monkeypatch.setattr(auth, "auth_store", lambda: MagicMock())
    with TestClient(app, base_url="https://testserver") as client:
        for path in ("/openapi.json", "/docs", "/redoc"):
            response = client.get(path)
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
            assert response.json() == {"detail": "authentication_required"}
