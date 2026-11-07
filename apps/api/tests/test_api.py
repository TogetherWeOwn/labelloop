from unittest.mock import patch

from fastapi.testclient import TestClient
from labelloop_api.main import app


def test_healthz() -> None:
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "api"}


def test_readiness() -> None:
    with (
        patch("labelloop_api.main.psycopg.connect") as connect,
        patch("labelloop_api.main.Minio") as minio,
    ):
        connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (
            "vector",
        )
        minio.return_value.bucket_exists.return_value = True
        response = TestClient(app, raise_server_exceptions=True).get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
