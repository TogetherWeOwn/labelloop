"""Trusted development-evaluation stage; no final-test, publication or default runner.

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

from sqlalchemy import Engine, select, text

from labelloop_compiler.compiler import json_bytes, transform_document
from labelloop_core.evaluation import DerivedPredictions, EvaluationImage, EvaluationRunner
from labelloop_core.json_records import canonical_json
from labelloop_core.registry import ModelRelease
from labelloop_core.training import InferencePolicy

from .management_compile_worker import (
    CompilationObjects,
    CompileContext,
    content_key,
    pinned_release,
)
from .management_evaluation import ApprovedDevelopmentProtocol, verified_model
from .management_train_worker import checked_object, frozen_validation_images, materialize_dataset
from .management_training import verified_variant
from .model_schema import model_release
from .model_store import record_digest
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
