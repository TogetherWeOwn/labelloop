"""Minimal API service used by the development stack."""

import os

import psycopg
import uvicorn
from fastapi import FastAPI
from minio import Minio

app = FastAPI(title="Labelloop", version="0.0.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "api"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    """Check local dependencies; the SQL file enables the extension on a new volume."""
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3) as connection:
        result = connection.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'").fetchone()
        if result is None:
            raise RuntimeError("pgvector is not enabled")
    storage = Minio(
        os.environ["S3_ENDPOINT"],
        access_key=os.environ["S3_ACCESS_KEY"],
        secret_key=os.environ["S3_SECRET_KEY"],
        secure=False,
    )
    bucket = os.environ["S3_BUCKET"]
    if not storage.bucket_exists(bucket):
        storage.make_bucket(bucket)
    storage.list_objects(bucket, max_keys=1)
    return {"status": "ready", "storage": "connected", "database": "connected"}


def run() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run()
