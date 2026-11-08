"""Trusted local-training stage and fenced publication; no default worker registration.

The backend factory is deployment-owned (LocalUltralyticsBackend in the ML
process). Model output cannot select it, filesystem paths or object-store keys.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Protocol
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from sqlalchemy import Engine, select, text

from labelloop_compiler.compiler import json_bytes
from labelloop_core.evaluation import EvaluationImage, GroundTruth
from labelloop_core.json_records import canonical_json
from labelloop_core.training import (
    Checkpoint,
    InferencePolicy,
    TrainingBackend,
    TrainingRequest,
    TrainingResult,
)

from .management_compile_worker import (
    CompilationObjects,
    CompileContext,
    CompileLease,
    content_key,
    pinned_release,
)
from .management_training import ApprovedTrainingProfile, verified_variant
from .model_store import decode_report, save_training_transaction
from .schema import (
    canonical_dataset_release,
    canonical_event,
    preprocessing_recipe,
    project,
    yolo_dataset_variant,
)


class LocalBackendFactory(Protocol):
    def __call__(
        self,
        *,
        dataset: Path,
        initialization: Path,
        output: Path,
        validation_images: tuple[EvaluationImage, ...],
    ) -> TrainingBackend: ...


def checked_object(objects: CompilationObjects, digest: str, *, limit: int) -> bytes:
    data = objects.read(content_key(digest))
    if len(data) > limit or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("training object exceeds its bound or digest differs")
    return data


def materialize_dataset(
    objects: CompilationObjects,
    manifest: dict,
    root: Path,
    *,
    selected_files: tuple[str, ...] | None = None,
) -> None:
    hashes = manifest["file_hashes"]
    if not hashes or len(hashes) > 100000:
        raise ValueError("training dataset membership exceeds its bound")
    # Validate the complete allowlist before reading an object or creating a file.
    for name in hashes:
        path = PurePosixPath(name)
        if (
            not isinstance(name, str)
            or path.is_absolute()
            or ".." in path.parts
            or str(path) != name
            or "\\" in name
            or name == "manifest.json"
            or not path.parts
        ):
            raise ValueError("invalid compiled artifact path")
    if selected_files is not None and (
        not selected_files
        or len(set(selected_files)) != len(selected_files)
        or not set(selected_files) <= hashes.keys()
    ):
        raise ValueError("selected compiled files differ from manifest membership")
    root.mkdir()
    total = 0
    for name in hashes if selected_files is None else selected_files:
        digest = hashes[name]
        data = checked_object(objects, digest, limit=64_000_000)
        total += len(data)
        if total > 512_000_000:
            raise ValueError("training dataset bytes exceed their bound")
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (root / "manifest.json").write_bytes(json_bytes(manifest))


def collect_training(
    request: TrainingRequest,
    result: TrainingResult,
    manifest: dict,
    output: Path,
    objects: CompilationObjects,
) -> dict:
    """Check the delivered backend's files before saving immutable stage evidence."""
    if (
        result.request != request
        or len(result.checkpoints) != request.epochs
        or {checkpoint.epoch for checkpoint in result.checkpoints} != set(range(request.epochs))
    ):
        raise ValueError("training result differs from authorized request or epoch coverage")
    # Reconstruct the core contract so a non-validating backend cannot bypass it.
    replace(result)
    reports, uploads = [], []
    total = 0
    for checkpoint in result.checkpoints:
        path = output / "trainer" / "weights" / f"epoch{checkpoint.epoch}.pt"
        if path.is_symlink() or not path.is_file():
            raise ValueError("missing immutable checkpoint file")
        size = path.stat().st_size
        total += size
        if size > 256_000_000 or total > 512_000_000:
            raise ValueError("checkpoint bytes exceed their bound")
        data = path.read_bytes()
        if len(data) != size or hashlib.sha256(data).hexdigest() != checkpoint.sha256:
            raise ValueError("checkpoint bytes differ from fixed-shape selection")
        uploads.append((checkpoint.sha256, data))
        report_record = json.loads(
            (output / f"evaluation-epoch-{checkpoint.epoch}.json").read_text()
        )
        report = decode_report(report_record)
        if (
            report.run_id != checkpoint.evaluation_run_id
            or report.role != "development"
            or report.weights_sha256 != checkpoint.sha256
            or report.dataset_release_id != request.dataset_release_id
            or report.recipe_id != request.recipe_id
            or report.class_map_sha256 != request.class_map_sha256
            or report.policy != request.policy
            or set(report.overall.per_class) != set(manifest["class_map"])
            or report.overall.map50_95 != checkpoint.validation_map
        ):
            raise ValueError("checkpoint report differs from training lineage")
        reports.append(report_record)
    shapes = json.loads((output / "tensor-shapes.json").read_text())
    retention = json.loads((output / "target-retention.json").read_text())
    validate_runtime_evidence(request, manifest, shapes, retention)
    record = {
        "result": asdict(result),
        "reports": reports,
        "tensor_shapes": shapes,
        "target_retention": retention,
        "checkpoint_keys": {item.sha256: content_key(item.sha256) for item in result.checkpoints},
    }
    if len(canonical_json(record)) > 2_000_000:
        raise ValueError("training exceeds the queue's bounded inline evidence")
    # No upload occurs until all output contracts and checkpoint bytes are checked.
    for digest, data in uploads:
        objects.put(content_key(digest), data)
    return record


def validate_runtime_evidence(request: TrainingRequest, manifest: dict, shapes, retention) -> None:
    if set(shapes) != {"training", "validation", "inference"} or not all(shapes.values()):
        raise ValueError("missing actual tensor-shape evidence")
    for records in shapes.values():
        for shape in records:
            request.policy.assert_shape(tuple(shape), stride=32)
    train_names = {
        PurePosixPath(sample["image_path"]).name
        for sample in manifest["samples"]
        if sample["split"] == "train"
    }
    if (
        not retention
        or {row["sample"] for row in retention} != train_names
        or any(
            type(row[key]) is not int or row[key] < 0
            for row in retention
            for key in ("before", "after", "before_at_or_below_6px", "after_at_or_below_6px")
        )
        or any(
            row["before"] != row["after"]
            or row["before_at_or_below_6px"] > row["before"]
            or row["after_at_or_below_6px"] > row["after"]
            for row in retention
        )
    ):
        raise ValueError("incomplete or lossy actual dataloader retention evidence")


def decode_training(record: dict) -> TrainingResult:
    request = record["request"]
    return TrainingResult(
        **(
            record
            | {
                "request": TrainingRequest(
                    **(request | {"policy": InferencePolicy(**request["policy"])})
                ),
                "checkpoints": tuple(Checkpoint(**item) for item in record["checkpoints"]),
                "selected": Checkpoint(**record["selected"]),
            }
        )
    )


def frozen_validation_images(
    release, manifest: dict, *, include_excluded: bool = False
) -> tuple[EvaluationImage, ...]:
    return tuple(
        EvaluationImage(
            sample.asset.asset_id,
            sample.asset.width,
            sample.asset.height,
            tuple(
                GroundTruth(
                    obj.object_id,
                    obj.class_id,
                    obj.box,
                    obj.occluded,
                    obj.truncated_at_image_edge,
                )
                for obj in sample.revision.objects
            ),
            revision=sample.revision.revision,
            source_sha256=sample.asset.sha256,
        )
        for sample in release.samples
        if sample.split == "val"
        and (
            include_excluded or manifest["release_membership"][sample.asset.asset_id] == "compiled"
        )
    )


@dataclass(frozen=True)
class CanonicalLocalTrainingWorker:
    engine: Engine
    objects: CompilationObjects
    workspace_id: UUID
    backend: LocalBackendFactory

    def resolve(
        self, context: CompileContext
    ) -> tuple[TrainingRequest, dict, tuple[EvaluationImage, ...]]:
        """Recheck immutable intent/compiler authority before training or export I/O."""
        lease, inputs = context.lease, context.lease.input
        if lease.kind != "train_local" or inputs["workspace_id"] != str(self.workspace_id):
            raise ValueError("training workspace or kind mismatch")
        request = TrainingRequest(
            **(inputs["request"] | {"policy": InferencePolicy(**inputs["request"]["policy"])})
        )
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                job = connection.execute(
                    text("SELECT input,project_key,kind FROM worker_job WHERE job_id=:id"),
                    {"id": lease.job_id},
                ).one_or_none()
                receipts = (
                    connection.execute(
                        select(canonical_event.c.payload).where(
                            canonical_event.c.event_type == "management.start_training",
                            canonical_event.c.aggregate_id == inputs["variant_id"],
                            canonical_event.c.actor_kind == "human",
                            canonical_event.c.actor_id == inputs["requested_by"],
                            canonical_event.c.payload["workspace_id"].as_string()
                            == str(self.workspace_id),
                            canonical_event.c.payload["evidence"]["job_id"].as_string()
                            == lease.job_id,
                        )
                    )
                    .scalars()
                    .all()
                )
                variant = connection.execute(
                    select(
                        yolo_dataset_variant,
                        canonical_dataset_release.c.project_id,
                        canonical_dataset_release.c.release_key,
                        preprocessing_recipe.c.recipe_key,
                        preprocessing_recipe.c.policy,
                        preprocessing_recipe.c.immutable,
                    )
                    .select_from(yolo_dataset_variant)
                    .join(
                        canonical_dataset_release,
                        yolo_dataset_variant.c.release_id == canonical_dataset_release.c.id,
                    )
                    .join(project, project.c.id == canonical_dataset_release.c.project_id)
                    .join(
                        preprocessing_recipe,
                        preprocessing_recipe.c.id == yolo_dataset_variant.c.recipe_id,
                    )
                    .where(
                        yolo_dataset_variant.c.id == UUID(inputs["variant_id"]),
                        project.c.workspace_id == self.workspace_id,
                        project.c.project_key == lease.project_key,
                        project.c.id == UUID(request.project_id),
                    )
                ).one_or_none()
                if (
                    job is None
                    or job.kind != lease.kind
                    or job.project_key != lease.project_key
                    or canonical_json(job.input) != canonical_json(inputs)
                    or len(receipts) != 1
                    or canonical_json(receipts[0]["evidence"]["inputs"]) != canonical_json(inputs)
                    or receipts[0]["result"]["id"] != request.run_id
                    or variant is None
                    or inputs["variant_revision"] != 1
                ):
                    raise ValueError("training input differs from owned authorized intent")
                manifest, digest, compile_job_id = verified_variant(
                    connection, variant, self.workspace_id
                )
                profile = inputs["profile"]
                expected = ApprovedTrainingProfile(
                    UUID(request.project_id),
                    profile["id"],
                    profile["revision"],
                    profile["recipe_id"],
                    profile["initialization_sha256"],
                    profile["epochs"],
                    request.batch,
                    request.seed,
                    request.policy,
                )
                if (
                    expected.request(request.run_id, variant.release_key, manifest, digest)
                    != request
                    or expected.read().model_dump(mode="json") != profile
                    or compile_job_id != inputs["compile_job_id"]
                    or inputs["manifest_key"] != content_key(digest)
                    or inputs["initialization_key"] != content_key(request.initialization_sha256)
                ):
                    raise ValueError("training request differs from pinned profile or variant")
                compile_job = connection.execute(
                    text("SELECT input FROM worker_job WHERE job_id=:id"),
                    {"id": compile_job_id},
                ).scalar_one()
                _, release, _ = pinned_release(
                    connection.connection.driver_connection,
                    SimpleNamespace(
                        job_id=compile_job_id,
                        project_key=lease.project_key,
                        kind="compile_variant",
                        input=compile_job,
                    ),
                    self.workspace_id,
                )
        return request, manifest, frozen_validation_images(release, manifest)

    def train(self, context: CompileContext) -> dict:
        request, manifest, validation_images = self.resolve(context)
        manifest_data = checked_object(
            self.objects, request.dataset_manifest_sha256, limit=2_000_000
        )
        if manifest_data != json_bytes(manifest):
            raise ValueError("stored compiler manifest differs from publication")
        initialization = checked_object(
            self.objects, request.initialization_sha256, limit=256_000_000
        )
        with TemporaryDirectory(prefix="canonical-train-") as temporary:
            root = Path(temporary)
            dataset, output = root / "dataset", root / "run"
            materialize_dataset(self.objects, manifest, dataset)
            checkpoint = root / "initialization.pt"
            checkpoint.write_bytes(initialization)
            backend = self.backend(
                dataset=dataset,
                initialization=checkpoint,
                output=output,
                validation_images=validation_images,
            )
            result = backend.train(request)
            return collect_training(request, result, manifest, output, self.objects)

    def publish(self, connection: psycopg.Connection, lease: CompileLease, artifacts: dict) -> None:
        """JobStore.complete sink: no second connection, backend, filesystem or object I/O."""
        inputs, record = lease.input, artifacts["training"]
        if lease.kind != "train_local" or inputs["workspace_id"] != str(self.workspace_id):
            raise ValueError("training workspace or kind mismatch")
        result = decode_training(record["result"])
        request = result.request
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT has_table_privilege(current_user,'canonical_event',"
                "'UPDATE,DELETE,TRUNCATE') AS broad"
            )
            if cursor.fetchone()["broad"]:
                raise ValueError("training runtime must not rewrite canonical audit")
            cursor.execute(
                "SELECT input,kind,project_key FROM worker_job WHERE job_id=%s",
                (lease.job_id,),
            )
            job = cursor.fetchone()
            cursor.execute(
                "SELECT payload FROM canonical_event WHERE event_type='management.start_training' "
                "AND aggregate_id=%s AND actor_kind='human' AND actor_id=%s "
                "AND payload->>'workspace_id'=%s AND payload->'evidence'->>'job_id'=%s",
                (
                    inputs["variant_id"],
                    inputs["requested_by"],
                    str(self.workspace_id),
                    lease.job_id,
                ),
            )
            receipts = cursor.fetchall()
            cursor.execute(
                "SELECT v.file_manifest,p.id AS project_id,p.project_key,r.release_key "
                "FROM yolo_dataset_variant v JOIN canonical_dataset_release r ON r.id=v.release_id "
                "JOIN project p ON p.id=r.project_id "
                "WHERE v.id=%s AND p.workspace_id=%s AND p.id=%s AND p.project_key=%s",
                (inputs["variant_id"], self.workspace_id, request.project_id, lease.project_key),
            )
            variant = cursor.fetchone()
            if (
                job is None
                or job["kind"] != lease.kind
                or job["project_key"] != lease.project_key
                or canonical_json(job["input"]) != canonical_json(inputs)
                or len(receipts) != 1
                or canonical_json(receipts[0]["payload"]["evidence"]["inputs"])
                != canonical_json(inputs)
                or receipts[0]["payload"]["result"]["id"] != request.run_id
                or variant is None
                or inputs["variant_revision"] != 1
            ):
                raise ValueError("training input differs from owned authorized intent")
            manifest = variant["file_manifest"]
            digest = hashlib.sha256(json_bytes(manifest)).hexdigest()
            profile = inputs["profile"]
            expected = ApprovedTrainingProfile(
                variant["project_id"],
                profile["id"],
                profile["revision"],
                profile["recipe_id"],
                profile["initialization_sha256"],
                profile["epochs"],
                request.batch,
                request.seed,
                request.policy,
            )
            if (
                expected.request(request.run_id, variant["release_key"], manifest, digest)
                != request
                or expected.read().model_dump(mode="json") != profile
                or canonical_json(asdict(request)) != canonical_json(inputs["request"])
                or inputs["manifest_key"] != content_key(digest)
                or inputs["initialization_key"] != content_key(request.initialization_sha256)
            ):
                raise ValueError("training result differs from pinned request")
            cursor.execute(
                "SELECT j.input,j.kind,j.status,j.project_key,j.generation,a.generation AS applied,"
                "s.payload FROM worker_job j JOIN worker_application a ON a.job_id=j.job_id "
                "JOIN worker_stage_artifact s ON s.job_id=j.job_id AND s.stage='compilation' "
                "WHERE j.job_id=%s",
                (inputs["compile_job_id"],),
            )
            compiled = cursor.fetchone()
            cursor.execute(
                "SELECT payload FROM canonical_event "
                "WHERE event_type='management.variant_compiled' "
                "AND aggregate_id=%s AND actor_kind='system' AND payload->>'workspace_id'=%s",
                (inputs["variant_id"], str(self.workspace_id)),
            )
            publications = cursor.fetchall()
            if (
                compiled is None
                or compiled["kind"] != "compile_variant"
                or compiled["status"] != "succeeded"
                or compiled["project_key"] != lease.project_key
                or compiled["generation"] != compiled["applied"]
                or compiled["input"]["variant_id"] != inputs["variant_id"]
                or compiled["payload"] != {"manifest": manifest, "manifest_sha256": digest}
                or len(publications) != 1
            ):
                raise ValueError("training requires fenced compiler publication")
            publication = publications[0]["payload"]
            if (
                publication["job_id"] != inputs["compile_job_id"]
                or publication["generation"] != compiled["generation"]
                or publication["manifest_sha256"] != digest
                or publication["manifest_key"] != content_key(digest)
                or publication["requested_by"] != compiled["input"]["requested_by"]
                or publication["release_id"] != variant["release_key"]
                or publication["canonical_snapshot_sha256"]
                != compiled["input"]["canonical_snapshot_sha256"]
            ):
                raise ValueError("training compiler receipt differs from frozen lineage")
        _, release, _ = pinned_release(
            connection,
            SimpleNamespace(
                job_id=inputs["compile_job_id"],
                project_key=lease.project_key,
                kind="compile_variant",
                input=compiled["input"],
            ),
            self.workspace_id,
        )
        if (
            manifest["ontology_id"] != release.ontology.ontology_id
            or manifest["ontology_hash"] != release.ontology_hash()
            or manifest["class_map"] != release.ontology.mapping()
            or manifest["split_assignment_version"] != release.assignment.assignment_version
            or canonical_json(manifest["recipe"]) != canonical_json(compiled["input"]["recipe"])
            or len(result.checkpoints) != request.epochs
            or {item.epoch for item in result.checkpoints} != set(range(request.epochs))
            or record["checkpoint_keys"]
            != {item.sha256: content_key(item.sha256) for item in result.checkpoints}
        ):
            raise ValueError("training artifacts differ from authorized lineage")
        validate_runtime_evidence(
            request, manifest, record["tensor_shapes"], record["target_retention"]
        )
        # Management authority is a UUID; the delivered model store's core contract
        # uses the canonical public project key. Translate only this verified field.
        stored = replace(result, request=replace(request, project_id=variant["project_key"]))
        save_training_transaction(
            connection,
            variant_id=UUID(inputs["variant_id"]),
            result=stored,
            reports=tuple(decode_report(item) for item in record["reports"]),
        )
        connection.execute(
            "INSERT INTO canonical_event(event_type,aggregate_id,expected_revision,"
            "payload,actor_kind,actor_id) "
            "VALUES ('management.training_completed',%s,1,%s,'system',%s)",
            (
                request.run_id,
                Jsonb(
                    {
                        "workspace_id": str(self.workspace_id),
                        "project_id": str(variant["project_id"]),
                        "project_key": variant["project_key"],
                        "variant_id": inputs["variant_id"],
                        "job_id": lease.job_id,
                        "generation": lease.generation,
                        "requested_by": inputs["requested_by"],
                        "stage_sha256": hashlib.sha256(canonical_json(record)).hexdigest(),
                        "checkpoint_keys": record["checkpoint_keys"],
                    }
                ),
                lease.worker_id,
            ),
        )
