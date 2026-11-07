"""Create the development bucket once, outside the API readiness path."""

import os

from minio import Minio
from urllib3 import PoolManager, Retry
from urllib3.util import Timeout

storage = Minio(
    os.environ["S3_ENDPOINT"],
    access_key=os.environ["S3_ACCESS_KEY"],
    secret_key=os.environ["S3_SECRET_KEY"],
    # This service connects only to the Compose-internal plain-HTTP MinIO.
    secure=False,
    http_client=PoolManager(
        timeout=Timeout(connect=3, read=3),
        maxsize=10,
        retries=Retry(total=0),
    ),
)
bucket = os.environ["S3_BUCKET"]
if not storage.bucket_exists(bucket):
    storage.make_bucket(bucket)
print("Development object storage initialized.")
