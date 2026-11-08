"""M1/M2 fixture snapshot ports and fail-closed isolated restore. No live restore CLI."""

import datetime
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import sqlalchemy as sa
from minio import Minio
from sqlalchemy.sql.util import sort_tables

from labelloop_api import model_schema, schema
from labelloop_api.backup_manifest import (
    MAX_OBJECT_BYTES,
    MAX_ROWS,
    MAX_SNAPSHOT_BYTES,
    SCHEMA_REVISION,
    BackupManifest,
    EncryptionMetadata,
    ObjectInventory,
    TableInventory,
    audit_seal,
    digest,
    encode,
    object_key,
    parse_manifest,
    reject_transport_secrets,
)
from labelloop_api.repository import MinioStore
from labelloop_compiler.compiler import _toolchain

# Explicit registration is deliberate: a future table/column must add inventory and drill coverage.
COLUMNS = {
    "project": "id project_key workspace_id task scope domain access_policy created_at",
    "ontology_release": "id ontology_key project_id version classes class_map created_at",
    "source_asset": (
        "id asset_key project_id sha256 object_key width height "
        "exif_orientation source_uri created_at"
    ),
    "reference_example": (
        "id asset_id class_id crop evidence_provenance approved "
        "validity_scope embedding_model created_at"
    ),
    "annotation_revision": (
        "id asset_id revision parent_revision actor_kind actor_id objects "
        "label_state approval_state review_tier scope_completeness "
        "training_eligible gold_eligible created_at"
    ),
    "review_event": (
        "id asset_id actor_kind actor_id decision expected_revision correction reason created_at"
    ),
    "completeness_assessment": (
        "id asset_id revision ontology_hash scope_completeness actor_kind "
        "actor_id output_truncated unresolved_regions created_at"
    ),
    "similarity_relation": (
        "id left_asset_id right_asset_id reason evidence "
        "embedding_model algorithm_version created_at"
    ),
    "borderline_review": (
        "id left_asset_id right_asset_id metric value review_state algorithm_version created_at"
    ),
    "source_split_assignment": (
        "id assignment_version asset_id group_id split seed algorithm "
        "thresholds reason locked created_at"
    ),
    "canonical_dataset_release": (
        "id release_key project_id ontology_id assignment_version sample_revisions ontology_hash "
        "excluded_asset_ids hashes frozen created_at"
    ),
    "preprocessing_recipe": "id recipe_key policy immutable created_at",
    "derived_sample": (
        "id variant_id source_id crop transform image_sha256 image_key label_sha256 created_at"
    ),
    "yolo_dataset_variant": (
        "id release_id recipe_id class_map file_manifest validated non_empty_val platform_mapping "
        "created_at"
    ),
    "canonical_event": (
        "id event_type aggregate_id expected_revision payload actor_kind actor_id created_at"
    ),
    "training_run": "id run_key variant_id input_manifest record created_at",
    "evaluation_run": "id run_key training_run_id variant_id record created_at",
    "model_release": (
        "id release_key training_run_id project_id recipe_id record export_evidence created_at"
    ),
    "model_evaluation": "model_id evaluation_id",
    "registry_head": "project_id recipe_id revision champion_id rollback_id",
    "promotion_event": "id project_id recipe_id revision actor_id action record created_at",
}
TABLES = {**schema.metadata.tables, **model_schema.metadata.tables}
AUDIT_TABLES = ("canonical_event", "review_event", "promotion_event", "annotation_revision")


class ReadObjects(Protocol):
    def read(self, key: str) -> bytes: ...

    def read_bounded(self, key: str, *, max_bytes: int) -> bytes: ...


def bounded_read(response, *, max_bytes: int) -> bytes:
    data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("snapshot object byte bound exceeded")
    return data


class RecoveryObjects(MinioStore):
    def read_bounded(self, key: str, *, max_bytes: int) -> bytes:
        response = self.client.get_object(self.bucket, key)
        try:
            return bounded_read(response, max_bytes=max_bytes)
        finally:
            response.close()
            response.release_conn()

    def read(self, key: str) -> bytes:
        return self.read_bounded(key, max_bytes=MAX_OBJECT_BYTES)


def capture_objects(roles, manifests, store: ReadObjects, *, byte_budget: int) -> dict[str, bytes]:
    objects = {}
    for key in roles:
        limit = min(MAX_OBJECT_BYTES, byte_budget)
        if limit < 1:
            raise ValueError("snapshot total byte bound exceeded")
        data = manifests[key] if key in manifests else store.read_bounded(key, max_bytes=limit)
        if len(data) > limit:
            raise ValueError("snapshot object byte bound exceeded")
        objects[key] = data
        byte_budget -= len(data)
    return objects


@dataclass(frozen=True)
class Snapshot:
    manifest: bytes
    objects: dict[str, bytes]

    @property
    def sha256(self) -> str:
        return digest(self.manifest)


def json_value(value):
    if isinstance(value, (uuid.UUID, datetime.datetime)):
        return str(value)
    return value


def check_schema(connection: sa.Connection) -> None:
    inspector = sa.inspect(connection)
    if set(inspector.get_table_names()) != set(COLUMNS) | {"alembic_version"}:
        raise ValueError("unregistered database table inventory")
    if connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalars().all() != [
        SCHEMA_REVISION
    ]:
        raise ValueError("unsupported database schema revision")
    for name, columns in COLUMNS.items():
        expected = columns.split()
        if (
            set(c["name"] for c in inspector.get_columns(name)) != set(expected)
            or list(TABLES[name].c.keys()) != expected
        ):
            raise ValueError("unregistered database column inventory")


def ordered_rows(name: str, rows: list[dict]) -> list[dict]:
    if name == "annotation_revision":
        return sorted(rows, key=lambda row: (row["asset_id"], row["revision"]))
    if name == "promotion_event":
        return sorted(rows, key=lambda row: (row["project_id"], row["recipe_id"], row["revision"]))
    return sorted(rows, key=lambda row: encode([row[c.name] for c in TABLES[name].primary_key]))


def object_roles(tables: dict[str, TableInventory]) -> dict[str, set[str]]:
    roles: dict[str, set[str]] = {}

    def add(sha256: str, role: str) -> None:
        key = object_key(sha256)
        roles.setdefault(key, set()).add(role)

    for row in tables["source_asset"].rows:
        add(row["sha256"], "original")
        if row["object_key"] != object_key(row["sha256"]):
            raise ValueError("source object identity mismatch")
    # Reference pixels are source_asset FKs; their crops and provenance remain canonical rows.
    assets = {row["id"]: row for row in tables["source_asset"].rows}
    for row in tables["reference_example"].rows:
        add(assets[row["asset_id"]]["sha256"], "reference")
    for row in tables["training_run"].rows:
        result = row["record"]
        add(result["request"]["initialization_sha256"], "initialization-weights")
        for checkpoint in result["checkpoints"]:
            add(checkpoint["sha256"], "checkpoint-weights")
    for row in tables["model_release"].rows:
        add(row["record"]["weights_sha256"], "model-weights")
        add(row["record"]["export_sha256"], "model-export")
    for row in tables["evaluation_run"].rows:
        add(row["record"]["weights_sha256"], "evaluation-weights")
    # Compiled pixels/labels are reproducible; only their manifest, not a mutable tree, is stored.
    for row in tables["yolo_dataset_variant"].rows:
        add(digest(encode(row["file_manifest"])), "release-manifest")
    return roles


def annotation_replay(tables: dict[str, TableInventory]) -> Iterator[tuple[str, dict]]:
    reviews: dict[tuple, list[dict]] = {}
    for row in tables["review_event"].rows:
        reviews.setdefault((row["asset_id"], row["expected_revision"]), []).append(row)
    for row in tables["annotation_revision"].rows:
        yield "annotation_revision", row
        for review in reviews.pop((row["asset_id"], row["revision"]), []):
            yield "review_event", review
    if reviews:
        raise ValueError("review event references a missing annotation revision")


def registry_continuity(previous: dict, current: dict) -> bool:
    previous, current = dict(previous), dict(current)
    for field in ("champion", "rollback"):
        left, right = previous.get(field), current.get(field)
        if left is not None and right is not None:
            left, right = dict(left), dict(right)
            if not set(left.pop("evaluation_run_ids")).issubset(right.pop("evaluation_run_ids")):
                return False
            previous[field], current[field] = left, right
    return previous == current


def check_chains(tables: dict[str, TableInventory]) -> None:
    # Every review is replayable while its expected revision is current; guards stay enabled.
    for _ in annotation_replay(tables):
        pass
    head_scopes = {(r["project_id"], r["recipe_id"]) for r in tables["registry_head"].rows}
    event_scopes = {(r["project_id"], r["recipe_id"]) for r in tables["promotion_event"].rows}
    if not event_scopes.issubset(head_scopes):
        raise ValueError("promotion audit events are missing their registry head")
    revisions: dict[str, int] = {}
    for row in tables["annotation_revision"].rows:
        previous = revisions.get(row["asset_id"], 0)
        if row["revision"] != previous + 1 or row["parent_revision"] != (previous or None):
            raise ValueError("incomplete annotation audit chain")
        revisions[row["asset_id"]] = row["revision"]
    events: dict[tuple, list[dict]] = {}
    for row in tables["promotion_event"].rows:
        key = row["project_id"], row["recipe_id"]
        chain = events.setdefault(key, [])
        if row["revision"] != len(chain) + 1:
            raise ValueError("incomplete registry audit chain")
        if chain and not registry_continuity(chain[-1]["record"]["after"], row["record"]["before"]):
            raise ValueError("broken registry before/after audit chain")
        chain.append(row)
    models = {r["id"]: r for r in tables["model_release"].rows}
    for head in tables["registry_head"].rows:
        chain = events.get((head["project_id"], head["recipe_id"]), [])
        if head["revision"] != len(chain):
            raise ValueError("registry head is missing audit events")
        if chain:
            state = chain[-1]["record"]["after"]
            for field, payload in (("champion_id", "champion"), ("rollback_id", "rollback")):
                model = models.get(head[field])
                if (state[payload] is None) != (model is None) or (
                    model and state[payload]["release_id"] != model["release_key"]
                ):
                    raise ValueError("registry rollback payload mismatch")


def capture_fixture(connection: sa.Connection, store: ReadObjects) -> Snapshot:
    """Caller uses one REPEATABLE READ, read-only transaction; immutable objects are hashed."""
    if (
        connection.get_isolation_level() != "REPEATABLE READ"
        or not connection.execute(sa.text("SHOW transaction_read_only")).scalar_one() == "on"
    ):
        raise ValueError("snapshot requires a repeatable-read read-only transaction")
    check_schema(connection)
    tables = {}
    for name, table in TABLES.items():
        rows = [
            {key: json_value(value) for key, value in row.items()}
            for row in connection.execute(sa.select(table).limit(MAX_ROWS + 1)).mappings()
        ]
        if len(rows) > MAX_ROWS:
            raise ValueError("snapshot row bound exceeded")
        rows = ordered_rows(name, rows)
        tables[name] = TableInventory(
            columns=COLUMNS[name].split(), rows=rows, sha256=digest(encode(rows))
        )
    objects = {}
    roles = object_roles(tables)
    manifests = {
        object_key(digest(encode(row["file_manifest"]))): encode(row["file_manifest"])
        for row in tables["yolo_dataset_variant"].rows
    }
    table_bytes = len(encode({name: table.model_dump() for name, table in tables.items()}))
    objects = capture_objects(roles, manifests, store, byte_budget=MAX_SNAPSHOT_BYTES - table_bytes)
    manifest = BackupManifest(
        toolchain=_toolchain(),
        encryption=EncryptionMetadata(),
        tables=tables,
        objects={
            key: ObjectInventory(sha256=digest(data), size=len(data), roles=sorted(roles[key]))
            for key, data in objects.items()
        },
        audit={name: audit_seal(tables[name].rows) for name in AUDIT_TABLES},
    )
    snapshot = Snapshot(encode(manifest.model_dump()), objects)
    validate_snapshot(snapshot, expected_sha256=snapshot.sha256)
    return snapshot


def validate_snapshot(snapshot: Snapshot, *, expected_sha256: str) -> BackupManifest:
    manifest = parse_manifest(snapshot.manifest, expected_sha256=expected_sha256)
    if manifest.toolchain != _toolchain():
        raise ValueError("backup toolchain version mismatch")
    if set(manifest.tables) != set(COLUMNS) or set(manifest.audit) != set(AUDIT_TABLES):
        raise ValueError("incomplete registered inventory or audit seals")
    for name, table in manifest.tables.items():
        if table.columns != COLUMNS[name].split() or any(
            set(row) != set(table.columns) for row in table.rows
        ):
            raise ValueError("backup column inventory mismatch")
        if (
            table.rows != ordered_rows(name, table.rows)
            or digest(encode(table.rows)) != table.sha256
        ):
            raise ValueError("backup row order or hash mismatch")
    for name, seal in manifest.audit.items():
        if seal != audit_seal(manifest.tables[name].rows):
            raise ValueError("incomplete audit chain seal")
    check_chains(manifest.tables)
    roles = object_roles(manifest.tables)
    if set(roles) != set(manifest.objects) or set(roles) != set(snapshot.objects):
        raise ValueError("missing or unexpected backup object")
    if (
        sum(len(data) for data in snapshot.objects.values()) + len(snapshot.manifest)
        > MAX_SNAPSHOT_BYTES
    ):
        raise ValueError("snapshot total byte bound exceeded")
    for key, entry in manifest.objects.items():
        data = snapshot.objects[key]
        if (
            key != object_key(entry.sha256)
            or digest(data) != entry.sha256
            or len(data) != entry.size
            or sorted(roles[key]) != entry.roles
        ):
            raise ValueError("backup object hash, size or role mismatch")
    reject_transport_secrets(manifest.model_dump())
    return manifest


@contextmanager
def fresh_fixture_bucket(client: Minio, *, endpoint: str) -> Iterator[MinioStore]:
    """Create and clean only this invocation's random bucket; no existing bucket is opened."""
    if urlsplit("http://" + endpoint).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("restore requires a loopback disposable object store")
    # Verify the supplied client, not just the caller's descriptive endpoint.
    if client._base_url.host != endpoint:
        raise ValueError("object store endpoint identity mismatch")
    bucket = "recovery-" + uuid.uuid4().hex
    client.make_bucket(bucket)
    try:
        yield RecoveryObjects(client, bucket)
    finally:
        for obj in client.list_objects(bucket, recursive=True):
            client.remove_object(bucket, obj.object_name)
        client.remove_bucket(bucket)


def restore_fixture(
    connection: sa.Connection,
    target: MinioStore,
    snapshot: Snapshot,
    *,
    expected_sha256: str,
) -> None:
    """Restore a fresh loopback drill target; the caller owns commit/rollback."""
    manifest = validate_snapshot(snapshot, expected_sha256=expected_sha256)
    url = connection.engine.url
    database = connection.execute(sa.text("SELECT current_database()")).scalar_one()
    if (
        url.host not in {"localhost", "127.0.0.1", "::1"}
        or not re.fullmatch(r"recovery_[0-9a-f]{32}", database)
        or not re.fullmatch(r"recovery-[0-9a-f]{32}", target.bucket)
        or urlsplit("http://" + target.client._base_url.host).hostname
        not in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("restore is restricted to new disposable loopback targets")
    check_schema(connection)
    connection.execute(
        sa.text("LOCK TABLE " + ", ".join(sorted(TABLES)) + " IN ACCESS EXCLUSIVE MODE")
    )
    if any(
        connection.execute(sa.select(sa.func.count()).select_from(t)).scalar_one()
        for t in TABLES.values()
    ):
        raise ValueError("restore refuses a non-empty database")
    if next(target.client.list_objects(target.bucket, recursive=True), None) is not None:
        raise ValueError("restore refuses a non-empty object bucket")

    def insert_rows(table, rows):
        decoded = []
        for row in rows:
            row = dict(row)
            for column in table.c:
                if row[column.name] is not None:
                    if isinstance(column.type, sa.DateTime):
                        row[column.name] = datetime.datetime.fromisoformat(row[column.name])
                    elif isinstance(column.type, sa.UUID):
                        row[column.name] = uuid.UUID(row[column.name])
            decoded.append(row)
        if decoded:
            connection.execute(sa.insert(table), decoded)

    with connection.begin_nested():
        for table in sort_tables(TABLES.values()):
            if table.name not in {"annotation_revision", "review_event"}:
                insert_rows(table, manifest.tables[table.name].rows)
        for name, row in annotation_replay(manifest.tables):
            insert_rows(TABLES[name], [row])
        for key, data in snapshot.objects.items():
            target.put(key, data)
            if digest(target.read(key)) != manifest.objects[key].sha256:
                raise ValueError("restored object readback mismatch")


def restored_reader(connection: sa.Connection, store: ReadObjects) -> Callable[[str], bytes]:
    assets = dict(
        connection.execute(
            sa.select(schema.source_asset.c.asset_key, schema.source_asset.c.object_key)
        ).all()
    )
    return lambda asset_id: store.read(assets[asset_id])
