"""MinIO-oriented content objects and the initial canonical asset transaction."""

from __future__ import annotations

import io
from dataclasses import dataclass

from minio import Minio
from sqlalchemy import Connection, insert, select

from labelloop_compiler.ingest import ObjectStore, ingest_image
from labelloop_core.domain import SourceAsset

from .schema import annotation_revision, source_asset


@dataclass(slots=True)
class MinioStore(ObjectStore):
    client: Minio
    bucket: str

    def put(self, key: str, data: bytes) -> None:
        """The key is the hash-generated object name; no user filename is accepted."""
        self.client.put_object(
            self.bucket, key, io.BytesIO(data), length=len(data), content_type="image/jpeg"
        )

    def read(self, key: str) -> bytes:
        response = self.client.get_object(self.bucket, key)
        try:
            data: bytes = response.read()
            return data
        finally:
            response.close()
            response.release_conn()


def ingest_canonical_asset(
    connection: Connection,
    store: ObjectStore,
    data: bytes,
    *,
    project_id: str,
    asset_id: str,
    source_uri: str,
    actor_id: str = "ingest",
    actor_kind: str = "system",
) -> SourceAsset:
    if actor_kind not in {"human", "system"}:
        raise ValueError("model output cannot register authoritative source assets")
    asset = ingest_image(
        store, data, asset_id=asset_id, project_id=project_id, source_uri=source_uri
    )
    identity = connection.execute(
        select(source_asset.c.id).where(source_asset.c.asset_key == asset_id)
    ).scalar_one_or_none()
    if identity is not None:
        raise ValueError("asset ID is already assigned; reconcile immutable objects instead")
    connection.execute(
        insert(source_asset).values(
            project_id=project_id,
            asset_key=asset.asset_id,
            sha256=asset.sha256,
            object_key=asset.object_key,
            width=asset.width,
            height=asset.height,
            exif_orientation=asset.exif_orientation,
            source_uri=asset.source_uri,
        )
    )
    record_id = connection.execute(
        select(source_asset.c.id).where(source_asset.c.asset_key == asset_id)
    ).scalar_one()
    connection.execute(
        insert(annotation_revision).values(
            asset_id=record_id,
            revision=1,
            actor_kind=actor_kind,
            actor_id=actor_id,
            objects=[],
            approval_state="draft",
            review_tier="unreviewed",
            scope_completeness="unreviewed",
            training_eligible=False,
            gold_eligible=False,
        )
    )
    return asset


def source_uuid(connection: Connection, asset_key: str) -> object:
    return connection.execute(
        select(source_asset.c.id).where(source_asset.c.asset_key == asset_key)
    ).scalar_one()
