"""Minimal API service used by the development stack."""

import html
import logging
import os
import socket
from collections.abc import Callable

import certifi
import psycopg
import urllib3
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from minio import Minio

from labelloop_api.annotation_reads import router as annotation_router
from labelloop_api.auth import (
    authentication_boundary,
    current_principal,
    router,
    validation_error,
)
from labelloop_api.management import management_router
from labelloop_api.management_ports import ManagementOperations

logger = logging.getLogger(__name__)


def log_readiness_failure(dependency: str, error: Exception) -> None:
    """Keep driver messages out of logs because they can contain credentials."""
    log_context: dict[str, str | int] = {
        "dependency": dependency,
        "error_type": type(error).__name__,
    }
    if isinstance(error, OSError):
        log_context["errno"] = error.errno or 0
        log_context["socket_error"] = isinstance(error, socket.gaierror)
    logger.warning(
        "Readiness check failed: %s",
        html.escape(str(log_context), quote=True),
    )


def storage_client() -> Minio:
    """Use the MinIO 7.2 supported HTTP-client timeout configuration."""
    return Minio(
        os.environ["S3_ENDPOINT"],
        access_key=os.environ["S3_ACCESS_KEY"],
        secret_key=os.environ["S3_SECRET_KEY"],
        # MinIO is a plain-HTTP loopback-only development dependency. This does
        # not disable CA validation or cookies; TLS belongs at any other boundary.
        secure=False,
        http_client=urllib3.PoolManager(
            timeout=urllib3.util.Timeout(connect=3, read=3),
            maxsize=10,
            cert_reqs="CERT_REQUIRED",
            ca_certs=os.environ.get("SSL_CERT_FILE") or certifi.where(),
            retries=urllib3.Retry(total=0),
        ),
    )


def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "api"}


def readyz() -> dict[str, str]:
    """Check dependencies and the migrated schema without logging connection details."""
    try:
        with psycopg.connect(
            host=os.environ["POSTGRES_HOST"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            dbname=os.environ["POSTGRES_DB"],
            connect_timeout=3,
        ) as connection:
            result = connection.execute(
                "SELECT extname FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
            if result is None:
                raise RuntimeError("pgvector is not enabled")
            migrated = connection.execute(
                "SELECT to_regclass('public.canonical_dataset_release') IS NOT NULL"
            ).fetchone()
            if not migrated or not migrated[0]:
                raise RuntimeError("canonical schema is not migrated")
    except Exception as error:
        log_readiness_failure("database", error)
        raise HTTPException(status_code=503, detail={"dependency": "database"}) from error
    try:
        storage = storage_client()
        bucket = os.environ["S3_BUCKET"]
        if not storage.bucket_exists(bucket):
            raise HTTPException(status_code=503, detail={"dependency": "storage"})
        next(iter(storage.list_objects(bucket)), None)
    except HTTPException:
        raise
    except Exception as error:
        log_readiness_failure("storage", error)
        raise HTTPException(status_code=503, detail={"dependency": "storage"}) from error
    return {"status": "ready", "storage": "connected", "database": "connected"}


def create_app(
    management_operations: Callable[[], ManagementOperations] | None = None,
) -> FastAPI:
    """Compose optional trusted adapters behind the single session/CSRF boundary.

    No request or environment toggle supplies operations or actor claims. The
    default app remains unmounted until real producer and admission gates pass.
    """
    application = FastAPI(
        title="Labelloop", version="0.0.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    application.middleware("http")(authentication_boundary)
    application.add_exception_handler(RequestValidationError, validation_error)
    application.include_router(router)
    application.include_router(annotation_router)
    application.add_api_route("/healthz", healthz, methods=["GET"])
    application.add_api_route("/readyz", readyz, methods=["GET"])
    if management_operations is not None:
        application.include_router(management_router(current_principal, management_operations))
    return application


app = create_app()


def run() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run()
