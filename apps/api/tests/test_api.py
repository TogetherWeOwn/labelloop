from unittest.mock import patch

import certifi
import pytest
from fastapi.testclient import TestClient

from labelloop_api.main import app


def test_healthz() -> None:
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "api"}


@pytest.fixture
def development_environment() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql://test",
        "S3_ENDPOINT": "minio:9000",
        "S3_ACCESS_KEY": "test-only",
        "S3_SECRET_KEY": "test-only",
        "S3_BUCKET": "test",
    }


def test_readiness(development_environment: dict[str, str]) -> None:
    with (
        patch("labelloop_api.main.psycopg.connect") as connect,
        patch("labelloop_api.main.Minio") as minio,
        patch.dict("os.environ", development_environment),
    ):
        connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (
            "vector",
        )
        minio.return_value.bucket_exists.return_value = True
        response = TestClient(app, raise_server_exceptions=True).get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    minio.assert_called_once_with(
        "minio:9000",
        access_key="test-only",
        secret_key="test-only",
        secure=False,
        http_client=minio.call_args.kwargs["http_client"],
    )
    client = minio.call_args.kwargs["http_client"]
    timeout = client.connection_pool_kw["timeout"]
    assert (timeout.connect_timeout, timeout.read_timeout) == (3, 3)
    assert client.connection_pool_kw["ca_certs"] == certifi.where()
    assert client.connection_pool_kw["retries"].total == 0
    minio.return_value.make_bucket.assert_not_called()


@pytest.mark.parametrize(
    ("dependency", "failure_path"),
    [
        ("database", "labelloop_api.main.psycopg.connect"),
        ("storage", "labelloop_api.main.Minio"),
    ],
)
def test_readiness_dependency_failure(
    development_environment: dict[str, str], dependency: str, failure_path: str
) -> None:
    with (
        patch("labelloop_api.main.psycopg.connect"),
        patch(failure_path, side_effect=OSError("test internal detail")),
        patch.dict("os.environ", development_environment),
    ):
        response = TestClient(app, raise_server_exceptions=False).get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"detail": {"dependency": dependency}}
    assert "test internal detail" not in response.text


def test_missing_bucket_is_not_created(development_environment: dict[str, str]) -> None:
    with (
        patch("labelloop_api.main.psycopg.connect"),
        patch("labelloop_api.main.Minio") as minio,
        patch.dict("os.environ", development_environment),
    ):
        minio.return_value.bucket_exists.return_value = False
        response = TestClient(app).get("/readyz")
    assert response.status_code == 503
    minio.return_value.make_bucket.assert_not_called()
