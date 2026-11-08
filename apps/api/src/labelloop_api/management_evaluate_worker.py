"""Trusted development evaluation and fenced publication; no final-test/default runner.

The ML process injects the delivered predict_samples adapter. Identity, truth,
recipe transforms and metrics stay code-owned; predictor output is only detections.
"""

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Protocol
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from sqlalchemy import Engine, select, text

from labelloop_compiler.compiler import json_bytes, transform_document
from labelloop_core.evaluation import DerivedPredictions, EvaluationImage, EvaluationRunner
from labelloop_core.json_records import canonical_json
from labelloop_core.registry import ModelRelease
from labelloop_core.training import InferencePolicy

from .management_compile_worker import (
    CompilationObjects,
    CompileContext,
    CompileLease,
    content_key,
    pinned_release,
)
from .management_evaluation import ApprovedDevelopmentProtocol, pinned_model, verified_model
from .management_train_worker import checked_object, frozen_validation_images, materialize_dataset
from .management_training import pinned_variant, verified_variant
from .model_schema import model_release
from .model_store import decode_report, record_digest, save_evaluation_transaction
from .schema import canonical_dataset_release, canonical_event, preprocessing_recipe, project
from .schema import yolo_dataset_variant as variant_table


class LocalPredictor(Protocol):
    def __call__(
        self,
        weights: Path,
        root: Path,
        samples: list[dict],
        class_ids: tuple[str, ...],
        policy: InferencePolicy,
        shapes: list,
    ) -> tuple[DerivedPredictions, ...]: ...


def evaluate_model(
    run_id: str,
    model: ModelRelease,
    manifest: dict,
    images: tuple[EvaluationImage, ...],
    objects: CompilationObjects,
    predictor: LocalPredictor,
) -> dict:
    """Score every pinned validation source, including crop/exclusion coverage loss."""
    samples = [sample for sample in manifest["samples"] if sample["split"] == "val"]
    names = tuple(sample["image_path"] for sample in samples)
    truths = {image.asset_id: image.truth_digest() for image in images}
    if (
        not names
        or len(set(names)) != len(names)
        or len(truths) != len(images)
        or manifest["file_hashes"]["class-map.json"] != model.class_map_sha256
        or record_digest(manifest["recipe"]) != model.preprocessing_sha256
        or tuple(sorted(manifest["class_map"], key=manifest["class_map"].__getitem__))
        != model.class_ids
        or manifest["recipe"]["recipe_id"] != model.recipe_id
        or any(
            PurePosixPath(sample["image_path"]).parts[:2] != ("images", "val")
            or sample["asset_id"] not in truths
            or sample["source_truth_sha256"] != truths[sample["asset_id"]]
            for sample in samples
        )
    ):
        raise ValueError("evaluation differs from frozen validation truth or model recipe")
    digest = hashlib.sha256(json_bytes(manifest)).hexdigest()
    if checked_object(objects, digest, limit=2_000_000) != json_bytes(manifest):
        raise ValueError("stored compiler manifest differs from publication")
    data = checked_object(objects, model.weights_sha256, limit=256_000_000)
    expected = Counter(
        canonical_json({"asset_id": sample["asset_id"], "transform": sample["transform"]})
        for sample in samples
    )
    with TemporaryDirectory(prefix="canonical-evaluate-") as temporary:
        root = Path(temporary)
        dataset, weights = root / "dataset", root / "selected.pt"
        # Neither train nor final-test pixels are materialized for this stage.
        materialize_dataset(objects, manifest, dataset, selected_files=names)
        weights.write_bytes(data)
        shapes: list = []
        derived = predictor(weights, dataset, samples, model.class_ids, model.policy, shapes)
        if (
            not isinstance(derived, tuple)
            or not all(isinstance(item, DerivedPredictions) for item in derived)
            or Counter(
                canonical_json(
                    {"asset_id": item.asset_id, "transform": transform_document(item.transform)}
                )
                for item in derived
            )
            != expected
            or len(shapes) < len(samples)
            or weights.is_symlink()
            or not weights.is_file()
            or weights.stat().st_size != len(data)
            or weights.read_bytes() != data
        ):
            raise ValueError("evaluation changed checkpoint, transforms or sample coverage")
        for shape in shapes:
            model.policy.assert_shape(tuple(shape), stride=1)
            if shape[0] != 1:
                raise ValueError("evaluation requires observed single-image tensors")
        report = EvaluationRunner(model.class_ids, model.policy).evaluate(
            run_id=run_id,
            dataset_release_id=manifest["release_id"],
            recipe_id=model.recipe_id,
            weights_sha256=model.weights_sha256,
            class_map_sha256=model.class_map_sha256,
            role="development",
            images=images,
            derived=derived,
        )
    record = {"report": asdict(report), "tensor_shapes": shapes, "sample_count": len(samples)}
    encoded = canonical_json(record)
    if len(encoded) > 2_000_000:
        raise ValueError("evaluation exceeds the queue's bounded inline evidence")
    # Fresh and resumed stages share the queue's JSON array/object representation.
    return json.loads(encoded)


@dataclass(frozen=True)
class CanonicalLocalEvaluationWorker:
    engine: Engine
    objects: CompilationObjects
    workspace_id: UUID
    predictor: LocalPredictor

    def resolve(self, context: CompileContext):
        """Recheck owned intent and fenced model/compiler before any object access."""
        lease, inputs = context.lease, context.lease.input
        if lease.kind != "evaluate_local" or inputs["workspace_id"] != str(self.workspace_id):
            raise ValueError("evaluation workspace or kind mismatch")
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
                            canonical_event.c.event_type == "management.start_evaluation",
                            canonical_event.c.aggregate_id == inputs["model_id"],
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
                model = connection.execute(
                    select(model_release)
                    .join(project, project.c.id == model_release.c.project_id)
                    .where(
                        model_release.c.release_key == inputs["model_id"],
                        project.c.id == UUID(inputs["project_id"]),
                        project.c.workspace_id == self.workspace_id,
                        project.c.project_key == lease.project_key,
                    )
                ).one_or_none()
                variant = connection.execute(
                    select(
                        variant_table,
                        canonical_dataset_release.c.release_key,
                        preprocessing_recipe.c.recipe_key,
                        preprocessing_recipe.c.policy,
                        preprocessing_recipe.c.immutable,
                    )
                    .join(
                        canonical_dataset_release,
                        variant_table.c.release_id == canonical_dataset_release.c.id,
                    )
                    .join(
                        preprocessing_recipe, variant_table.c.recipe_id == preprocessing_recipe.c.id
                    )
                    .where(
                        variant_table.c.id == UUID(inputs["variant_id"]),
                        canonical_dataset_release.c.project_id == UUID(inputs["project_id"]),
                        canonical_dataset_release.c.release_key == inputs["dataset_release_id"],
                    )
                ).one_or_none()
                if (
                    job is None
                    or job.kind != lease.kind
                    or job.project_key != lease.project_key
                    or canonical_json(job.input) != canonical_json(inputs)
                    or len(receipts) != 1
                    or canonical_json(receipts[0]["evidence"]["inputs"]) != canonical_json(inputs)
                    or receipts[0]["result"]["id"] != inputs["run_id"]
                    or model is None
                    or variant is None
                ):
                    raise ValueError("evaluation differs from owned authorized intent")
                protocol = inputs["protocol"]
                ApprovedDevelopmentProtocol(
                    model.project_id, protocol["id"], protocol["revision"], variant.id
                )
                release, model_job_id = verified_model(connection, model, self.workspace_id)
                manifest, digest, compile_job_id = verified_variant(
                    connection, variant, self.workspace_id
                )
                if (
                    protocol["role"] != "development"
                    or protocol["split"] != "val"
                    or inputs["model_sha256"] != record_digest(model.record)
                    or inputs["model_job_id"] != model_job_id
                    or inputs["compile_job_id"] != compile_job_id
                    or inputs["manifest_sha256"] != digest
                    or inputs["manifest_key"] != content_key(digest)
                    or inputs["weights_key"] != content_key(release.weights_sha256)
                    or inputs["policy"] != asdict(release.policy)
                ):
                    raise ValueError("evaluation differs from pinned protocol or publication")
                compile_job = connection.execute(
                    text("SELECT input FROM worker_job WHERE job_id=:id"),
                    {"id": compile_job_id},
                ).scalar_one()
                _, frozen, _ = pinned_release(
                    connection.connection.driver_connection,
                    SimpleNamespace(
                        job_id=compile_job_id,
                        project_key=lease.project_key,
                        kind="compile_variant",
                        input=compile_job,
                    ),
                    self.workspace_id,
                )
        return release, manifest, frozen_validation_images(frozen, manifest, include_excluded=True)

    def evaluate(self, context: CompileContext) -> dict:
        model, manifest, images = self.resolve(context)
        return evaluate_model(
            context.lease.input["run_id"], model, manifest, images, self.objects, self.predictor
        )


@dataclass(frozen=True)
class CanonicalLocalEvaluationPublisher:
    workspace_id: UUID

    def publish(self, connection: psycopg.Connection, lease: CompileLease, artifacts: dict) -> None:
        """JobStore.complete sink: report, model link and audit share its application fence."""
        inputs, record = lease.input, artifacts["evaluation"]
        if (
            connection.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS
            or lease.kind != "evaluate_local"
            or inputs["workspace_id"] != str(self.workspace_id)
            or len(canonical_json(record)) > 2_000_000
        ):
            raise ValueError("evaluation requires a bounded owned caller transaction")
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT has_table_privilege(current_user,'canonical_event',"
                "'UPDATE,DELETE,TRUNCATE') AS broad"
            )
            if cursor.fetchone()["broad"]:
                raise ValueError("evaluation runtime must not rewrite canonical audit")
            cursor.execute(
                "SELECT input,project_key,kind FROM worker_job WHERE job_id=%s", (lease.job_id,)
            )
            job = cursor.fetchone()
            cursor.execute(
                "SELECT payload FROM canonical_event "
                "WHERE event_type='management.start_evaluation' "
                "AND aggregate_id=%s AND actor_kind='human' AND actor_id=%s "
                "AND payload->>'workspace_id'=%s AND payload->'evidence'->>'job_id'=%s",
                (inputs["model_id"], inputs["requested_by"], str(self.workspace_id), lease.job_id),
            )
            receipts = cursor.fetchall()
            cursor.execute(
                "SELECT payload,sha256 FROM worker_stage_artifact "
                "WHERE job_id=%s AND stage='evaluation'",
                (lease.job_id,),
            )
            stage = cursor.fetchone()
            cursor.execute(
                "SELECT m.* FROM model_release m JOIN project p ON p.id=m.project_id "
                "WHERE m.release_key=%s AND p.id=%s AND p.workspace_id=%s AND p.project_key=%s",
                (inputs["model_id"], inputs["project_id"], self.workspace_id, lease.project_key),
            )
            model = cursor.fetchone()
            cursor.execute(
                "SELECT v.*,r.release_key,p.recipe_key,p.policy,p.immutable "
                "FROM yolo_dataset_variant v JOIN canonical_dataset_release r ON r.id=v.release_id "
                "JOIN preprocessing_recipe p ON p.id=v.recipe_id "
                "WHERE v.id=%s AND r.project_id=%s AND r.release_key=%s",
                (inputs["variant_id"], inputs["project_id"], inputs["dataset_release_id"]),
            )
            variant = cursor.fetchone()
            digest = hashlib.sha256(canonical_json(record)).hexdigest()
            if (
                job is None
                or job["kind"] != lease.kind
                or job["project_key"] != lease.project_key
                or canonical_json(job["input"]) != canonical_json(inputs)
                or len(receipts) != 1
                or canonical_json(receipts[0]["payload"]["evidence"]["inputs"])
                != canonical_json(inputs)
                or receipts[0]["payload"]["result"]["id"] != inputs["run_id"]
                or stage is None
                or stage["sha256"] != digest
                or canonical_json(stage["payload"]) != canonical_json(record)
                or model is None
                or variant is None
            ):
                raise ValueError("evaluation differs from saved stage or owned authorized intent")
            protocol = inputs["protocol"]
            ApprovedDevelopmentProtocol(
                model["project_id"], protocol["id"], protocol["revision"], variant["id"]
            )
            release, model_job_id = pinned_model(
                connection, SimpleNamespace(**model), self.workspace_id
            )
            manifest, manifest_digest, compile_job_id = pinned_variant(
                connection, SimpleNamespace(**variant), self.workspace_id
            )
            if (
                protocol["role"] != "development"
                or protocol["split"] != "val"
                or inputs["model_sha256"] != record_digest(model["record"])
                or inputs["model_job_id"] != model_job_id
                or inputs["compile_job_id"] != compile_job_id
                or inputs["manifest_sha256"] != manifest_digest
                or inputs["manifest_key"] != content_key(manifest_digest)
                or inputs["weights_key"] != content_key(release.weights_sha256)
                or inputs["policy"] != asdict(release.policy)
            ):
                raise ValueError("evaluation differs from pinned protocol or publication")
            cursor.execute("SELECT input FROM worker_job WHERE job_id=%s", (compile_job_id,))
            compiled = cursor.fetchone()["input"]
        _, frozen, _ = pinned_release(
            connection,
            SimpleNamespace(
                job_id=compile_job_id,
                project_key=lease.project_key,
                kind="compile_variant",
                input=compiled,
            ),
            self.workspace_id,
        )
        images = frozen_validation_images(frozen, manifest, include_excluded=True)
        report = decode_report(record["report"])
        sample_count = sum(sample["split"] == "val" for sample in manifest["samples"])
        support = Counter(obj.class_id for image in images for obj in image.objects)
        if (
            report.run_id != inputs["run_id"]
            or report.role != "development"
            or report.overall.images != len(images)
            or report.overall.support != sum(support.values())
            or any(
                metrics.support != support[class_id]
                for class_id, metrics in report.overall.per_class.items()
            )
            or Counter(item.asset_id for item in report.coverage)
            != Counter(image.asset_id for image in images)
            or type(record["sample_count"]) is not int
            or record["sample_count"] != sample_count
            or len(record["tensor_shapes"]) < sample_count
        ):
            raise ValueError("evaluation report differs from pinned run or frozen source support")
        for shape in record["tensor_shapes"]:
            release.policy.assert_shape(tuple(shape), stride=1)
            if shape[0] != 1:
                raise ValueError("evaluation requires observed single-image tensors")
        save_evaluation_transaction(
            connection,
            model_key=inputs["model_id"],
            variant_id=UUID(inputs["variant_id"]),
            report=report,
        )
        connection.execute(
            "INSERT INTO canonical_event(event_type,aggregate_id,expected_revision,"
            "payload,actor_kind,actor_id) "
            "VALUES ('management.evaluation_completed',%s,1,%s,'system',%s)",
            (
                report.run_id,
                Jsonb(
                    {
                        "workspace_id": str(self.workspace_id),
                        "project_id": inputs["project_id"],
                        "project_key": lease.project_key,
                        "model_id": inputs["model_id"],
                        "variant_id": inputs["variant_id"],
                        "protocol": protocol,
                        "job_id": lease.job_id,
                        "generation": lease.generation,
                        "requested_by": inputs["requested_by"],
                        "stage_sha256": digest,
                    }
                ),
                lease.worker_id,
            ),
        )
