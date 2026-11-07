"""Minimal API service used by the development stack."""

import os

import certifi
import psycopg
import urllib3
import uvicorn
from fastapi import FastAPI, HTTPException
from minio import Minio

app = FastAPI(title="Labelloop", version="0.0.0")


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


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "api"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    """Check local dependencies; the SQL file enables the extension on a new volume."""
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as connection:
            result = connection.execute(
                "SELECT extname FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
            if result is None:
                raise RuntimeError("pgvector is not enabled")
    except Exception as error:
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
        raise HTTPException(status_code=503, detail={"dependency": "storage"}) from error
    return {"status": "ready", "storage": "connected", "database": "connected"}


def run() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run()
