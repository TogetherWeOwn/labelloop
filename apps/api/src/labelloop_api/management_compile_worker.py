"""Trusted compiler/queue composition; no default worker or application mount.

Only immutable content objects are uploaded outside SQL. Canonical publication is
an existing JobStore.complete sink, in its lease-fenced application transaction.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from labelloop_compiler.compiler import compile_dataset, json_bytes, resolve_recipe
from labelloop_core.domain import (
    AnnotationRevision,
    CanonicalDatasetRelease,
    ClassDefinition,
    DatasetObject,
    OntologyRelease,
    ReleasedSample,
    SourceAsset,
    SourceSplitAssignment,
)
from labelloop_core.geometry import Box
from labelloop_core.json_records import canonical_json


class CompileLease(Protocol):
    job_id: str
    project_key: str
    kind: str
    generation: int
    worker_id: str
    input: dict


class CompilationQueue(Protocol):
    def connect(self) -> psycopg.Connection: ...


class CompileContext(Protocol):
    lease: CompileLease


def content_key(sha256: str) -> str:
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise ValueError("invalid content digest")
    return f"sha256/{sha256[:2]}/{sha256}"


class CompilationObjects(Protocol):
    def read(self, key: str) -> bytes: ...

    def put(self, key: str, data: bytes) -> None: ...


def pinned_release(connection: psycopg.Connection, lease: CompileLease, workspace_id: UUID):
    """Rehydrate historical pins, never the latest ontology, labels or assignment."""
    inputs = lease.input
    if lease.kind != "compile_variant" or inputs["workspace_id"] != str(workspace_id):
        raise ValueError("compile workspace or kind mismatch")
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT r.* FROM canonical_dataset_release r JOIN project p ON p.id=r.project_id "
            "WHERE r.release_key=%s AND p.id=%s AND p.workspace_id=%s AND p.project_key=%s",
            (inputs["release_id"], inputs["project_id"], workspace_id, lease.project_key),
        )
        release = cur.fetchone()
        if release is None or not release["frozen"]:
            raise ValueError("missing owned frozen release")
        cur.execute(
            "SELECT payload FROM canonical_event WHERE event_type='management.compile_variant' "
            "AND aggregate_id=%s AND actor_kind='human' AND actor_id=%s "
            "AND payload->>'workspace_id'=%s AND payload->'evidence'->>'job_id'=%s",
            (inputs["release_id"], inputs["requested_by"], str(workspace_id), lease.job_id),
        )
        receipts = cur.fetchall()
        if len(receipts) != 1 or canonical_json(
            receipts[0]["payload"]["evidence"]["inputs"]
        ) != canonical_json(inputs):
            raise ValueError("compile inputs differ from the authorized receipt")
        cur.execute("SELECT input FROM worker_job WHERE job_id=%s", (lease.job_id,))
        job = cur.fetchone()
        if job is None or canonical_json(job["input"]) != canonical_json(inputs):
            raise ValueError("compile inputs differ from the durable job")
    candidate, asset_ids = frozen_release(connection, release, workspace_id)
    if (
        release["hashes"].get("canonical_snapshot_sha256") != inputs["canonical_snapshot_sha256"]
        or inputs["release_revision"] != 1
        or release["assignment_version"] != inputs["assignment_version"]
        or candidate.ontology.ontology_id != inputs["ontology_id"]
    ):
        raise ValueError("compile inputs differ from the frozen release")
    recipe = resolve_recipe(inputs["recipe"]["recipe_id"])
    if canonical_json(asdict(recipe)) != canonical_json(inputs["recipe"]):
        raise ValueError("recipe differs from the code-owned policy")
    return release, candidate, asset_ids


def frozen_release(connection: psycopg.Connection, release: dict, workspace_id: UUID):
    """Validate attributed frozen history within the caller's owned read snapshot."""
    if not release["frozen"]:
        raise ValueError("missing frozen release")
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT payload FROM canonical_event WHERE event_type='management.freeze_release' "
            "AND aggregate_id=%s AND actor_kind='human' AND payload->>'workspace_id'=%s "
            "AND payload->'result'->>'id'=%s",
            (str(release["project_id"]), str(workspace_id), release["release_key"]),
        )
        freezes = cur.fetchall()
        expected_resource = {
            "id": release["release_key"],
            "project_id": str(release["project_id"]),
            "kind": "dataset-release",
            "revision": 1,
            "state": "frozen",
        }
        if (
            len(freezes) != 1
            or freezes[0]["payload"]["result"] != expected_resource
            or freezes[0]["payload"]["evidence"].get("acceptance_policy_id")
            != "canonical-training-eligibility-v1"
            or not release["hashes"].get("canonical_snapshot_sha256")
            or freezes[0]["payload"]["evidence"].get("canonical_snapshot_sha256")
            != release["hashes"]["canonical_snapshot_sha256"]
        ):
            raise ValueError("missing frozen snapshot authority")
        cur.execute(
            "SELECT * FROM ontology_release WHERE id=%s AND project_id=%s",
            (release["ontology_id"], release["project_id"]),
        )
        ontology = cur.fetchone()
        if ontology is None:
            raise ValueError("missing pinned ontology")
        pins, exclusions = release["sample_revisions"], release["excluded_asset_ids"]
        identities = set(pins) | set(exclusions)
        if not pins or len(identities) > 10000 or set(pins) & set(exclusions):
            raise ValueError("invalid bounded release membership")
        cur.execute(
            "SELECT a.*, s.group_id, s.split, s.seed, s.algorithm, s.reason, s.locked, "
            "s.thresholds FROM source_asset a JOIN source_split_assignment s ON s.asset_id=a.id "
            "WHERE a.project_id=%s AND a.asset_key=ANY(%s) "
            "AND s.assignment_version=%s ORDER BY a.id",
            (release["project_id"], sorted(identities), release["assignment_version"]),
        )
        assets = cur.fetchall()
        if len(assets) != len(identities) or any(not row["locked"] for row in assets):
            raise ValueError("missing pinned source assignments")
        if (
            len(
                {
                    (row["seed"], row["algorithm"], json.dumps(row["thresholds"], sort_keys=True))
                    for row in assets
                }
            )
            != 1
        ):
            raise ValueError("inconsistent pinned assignment policy")
        cur.execute(
            "SELECT r.*, a.asset_key FROM annotation_revision r "
            "JOIN source_asset a ON a.id=r.asset_id "
            "WHERE a.project_id=%s AND a.asset_key=ANY(%s) "
            "AND r.revision=(%s::jsonb->>a.asset_key)::bigint",
            (release["project_id"], sorted(pins), Jsonb(pins)),
        )
        revisions = {row["asset_key"]: row for row in cur.fetchall()}
    if set(revisions) != set(pins):
        raise ValueError("missing pinned annotation revisions")
    samples = []
    group_splits = {}
    for asset in assets:
        if group_splits.setdefault(asset["group_id"], asset["split"]) != asset["split"]:
            raise ValueError("pinned group crosses splits")
        if asset["asset_key"] not in pins:
            continue
        row = revisions[asset["asset_key"]]
        samples.append(
            ReleasedSample(
                asset=SourceAsset(
                    asset_id=asset["asset_key"],
                    project_id=str(release["project_id"]),
                    sha256=asset["sha256"],
                    object_key=asset["object_key"],
                    width=asset["width"],
                    height=asset["height"],
                    exif_orientation=asset["exif_orientation"],
                    source_uri=asset["source_uri"],
                    source_group=asset["group_id"],
                ),
                revision=AnnotationRevision(
                    revision=row["revision"],
                    parent_revision=row["parent_revision"],
                    actor_kind=row["actor_kind"],
                    approval_state=row["approval_state"],
                    review_tier=row["review_tier"],
                    scope_completeness=row["scope_completeness"],
                    training_eligible=row["training_eligible"],
                    gold_eligible=row["gold_eligible"],
                    objects=tuple(
                        DatasetObject(
                            object_id=item["object_id"],
                            class_id=item["class_id"],
                            box=Box(*item["box"]),
                            label_state=item.get("label_state", "identified"),
                            attributes=item.get("attributes", {}),
                            occluded=item.get("occluded", False),
                            truncated_at_image_edge=item.get("truncated_at_image_edge", False),
                        )
                        for item in row["objects"]
                    ),
                ),
                group_id=asset["group_id"],
                split=asset["split"],
            )
        )
    candidate = CanonicalDatasetRelease(
        release_id=release["release_key"],
        project_id=str(release["project_id"]),
        ontology=OntologyRelease(
            ontology_id=ontology["ontology_key"],
            project_id=str(release["project_id"]),
            version=ontology["version"],
            classes=tuple(ClassDefinition(**item) for item in ontology["classes"]),
            class_map=ontology["class_map"],
        ),
        assignment=SourceSplitAssignment(
            assignment_version=release["assignment_version"],
            asset_splits={row["asset_key"]: row["split"] for row in assets},
            group_splits=group_splits,
            group_ids={row["asset_key"]: row["group_id"] for row in assets},
            seed=assets[0]["seed"],
            algorithm=assets[0]["algorithm"],
            reasons={row["asset_key"]: row["reason"] for row in assets},
            locked=True,
        ),
        samples=tuple(samples),
        excluded_asset_ids=exclusions,
    )
    snapshot = hashlib.sha256(
        json.dumps(asdict(candidate), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        snapshot != release["hashes"]["canonical_snapshot_sha256"]
        or candidate.ontology_hash() != release["ontology_hash"]
    ):
        raise ValueError("frozen snapshot digest mismatch")
    return candidate, {row["asset_key"]: row["id"] for row in assets}


@dataclass(frozen=True)
class CanonicalCompilationWorker:
    store: CompilationQueue
    objects: CompilationObjects
    workspace_id: UUID

    def compile(self, context: CompileContext) -> dict:
        with self.store.connect() as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            _, release, _ = pinned_release(connection, context.lease, self.workspace_id)
        # The temporary directory is worker-owned, never a request or queue path.
        with TemporaryDirectory(prefix="canonical-compile-") as temporary:
            output = Path(temporary) / "dataset"
            source_keys = {
                sample.asset.asset_id: sample.asset.object_key for sample in release.samples
            }
            manifest = compile_dataset(
                release,
                context.lease.input["recipe"]["recipe_id"],
                output,
                lambda asset_id: self.objects.read(source_keys[asset_id]),
            )
            manifest_bytes = (output / "manifest.json").read_bytes()
            result = {
                "manifest": manifest,
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            }
            if len(canonical_json(result)) > 2_000_000:
                raise ValueError("compilation exceeds the queue's bounded inline lineage")
            for relative, sha256 in manifest["file_hashes"].items():
                data = (output / relative).read_bytes()
                if hashlib.sha256(data).hexdigest() != sha256:
                    raise ValueError("compiler artifact digest mismatch")
                self.objects.put(content_key(sha256), data)
            self.objects.put(content_key(result["manifest_sha256"]), manifest_bytes)
        return result

    def publish(self, connection: psycopg.Connection, lease: CompileLease, artifacts: dict) -> None:
        # This method is only a JobStore.complete transactional sink. It commits
        # nothing and performs no object-store or filesystem operation.
        with connection.cursor() as cur:
            cur.execute(
                "SELECT has_table_privilege(current_user, 'canonical_event', "
                "'UPDATE,DELETE,TRUNCATE')"
            )
            if cur.fetchone()[0]:
                raise ValueError("compiler runtime must not rewrite canonical audit")
        release_row, release, sources = pinned_release(connection, lease, self.workspace_id)
        record = artifacts["compilation"]
        manifest, manifest_sha256 = record["manifest"], record["manifest_sha256"]
        if (
            hashlib.sha256(json_bytes(manifest)).hexdigest() != manifest_sha256
            or manifest["release_id"] != release.release_id
            or manifest["ontology_id"] != release.ontology.ontology_id
            or manifest["ontology_hash"] != release.ontology_hash()
            or manifest["class_map"] != release.ontology.mapping()
            or manifest["split_assignment_version"] != release.assignment.assignment_version
            or canonical_json(manifest["recipe"]) != canonical_json(lease.input["recipe"])
            or set(manifest["release_membership"]) != set(sources)
            or not any(row["split"] == "val" for row in manifest["samples"])
        ):
            raise ValueError("compiled artifact differs from pinned release")
        samples = {sample.asset.asset_id: sample for sample in release.samples}
        seen = set()
        for row in manifest["samples"]:
            sample = samples[row["asset_id"]]
            if (
                row["asset_id"] in seen
                or row["revision"] != sample.revision.revision
                or row["source_sha256"] != sample.asset.sha256
                or row["group_id"] != sample.group_id
                or row["split"] != sample.split
                or manifest["file_hashes"][row["image_path"]] != row["image_sha256"]
                or manifest["file_hashes"][row["label_path"]] != row["label_sha256"]
            ):
                raise ValueError("compiled sample differs from canonical lineage")
            seen.add(row["asset_id"])
        expected_membership = {key: "compiled" if key in seen else "excluded" for key in sources}
        if manifest["release_membership"] != expected_membership:
            raise ValueError("compiled membership differs from sample lineage")
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO preprocessing_recipe(id,recipe_key,policy,immutable) "
                "VALUES (%s,%s,%s,true) "
                "ON CONFLICT (recipe_key) DO NOTHING",
                (uuid4(), manifest["recipe"]["recipe_id"], Jsonb(manifest["recipe"])),
            )
            cur.execute(
                "SELECT id,policy,immutable FROM preprocessing_recipe WHERE recipe_key=%s",
                (manifest["recipe"]["recipe_id"],),
            )
            recipe_id, policy, immutable = cur.fetchone()
            if not immutable or policy != manifest["recipe"]:
                raise ValueError("persisted recipe conflicts with compiler policy")
            cur.execute(
                "INSERT INTO yolo_dataset_variant(id,release_id,recipe_id,class_map,"
                "file_manifest,validated,non_empty_val) "
                "VALUES (%s,%s,%s,%s,%s,true,true)",
                (
                    UUID(lease.input["variant_id"]),
                    release_row["id"],
                    recipe_id,
                    Jsonb(manifest["class_map"]),
                    Jsonb(manifest),
                ),
            )
            for row in manifest["samples"]:
                cur.execute(
                    "INSERT INTO derived_sample(variant_id,source_id,crop,transform,"
                    "image_sha256,image_key,label_sha256) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        UUID(lease.input["variant_id"]),
                        sources[row["asset_id"]],
                        Jsonb(row["transform"]["crop"]),
                        Jsonb(row["transform"]),
                        row["image_sha256"],
                        content_key(row["image_sha256"]),
                        row["label_sha256"],
                    ),
                )
            cur.execute(
                "INSERT INTO canonical_event(event_type,aggregate_id,expected_revision,"
                "payload,actor_kind,actor_id) "
                "VALUES ('management.variant_compiled',%s,1,%s,'system',%s)",
                (
                    lease.input["variant_id"],
                    Jsonb(
                        {
                            "workspace_id": str(self.workspace_id),
                            "job_id": lease.job_id,
                            "generation": lease.generation,
                            "requested_by": lease.input["requested_by"],
                            "release_id": release.release_id,
                            "canonical_snapshot_sha256": lease.input["canonical_snapshot_sha256"],
                            "manifest_sha256": manifest_sha256,
                            "manifest_key": content_key(manifest_sha256),
                        }
                    ),
                    lease.worker_id,
                ),
            )
