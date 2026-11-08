"""Local export collection and atomic candidate publication, never qualification.

The ML process supplies the export_and_compare adapter. The stage rechecks pinned
intent and bytes, never exporter-selected identity or paths. No default worker or
app mount is installed here; deployment owns that trusted composition.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from labelloop_compiler.compiler import json_bytes
from labelloop_core.json_records import canonical_json
from labelloop_core.registry import ModelRelease
from labelloop_core.training import InferencePolicy, TrainingRequest, TrainingResult

from .management_compile_worker import CompilationObjects, CompileContext, CompileLease, content_key
from .management_train_worker import (
    CanonicalLocalTrainingWorker,
    checked_object,
    decode_training,
    materialize_dataset,
    validate_runtime_evidence,
)
from .model_store import (
    decode_report,
    record_digest,
    save_model_transaction,
    validate_checkpoint_reports,
    validate_export,
)


class LocalExporter(Protocol):
    def __call__(
        self,
        *,
        weights: Path,
        expected_weights_sha256: str,
        images: tuple[Path, ...],
        policy: InferencePolicy,
        evidence: Path,
    ) -> dict: ...


class ModelContext(CompileContext, Protocol):
    artifacts: dict


def export_training(
    request: TrainingRequest,
    manifest: dict,
    record: dict,
    objects: CompilationObjects,
    exporter: LocalExporter,
) -> dict:
    """Run the controlled exporter on hash-verified saved training and recipe pixels.

    The caller supplies an already-authorized request/manifest, not model output.
    No original test images or initialization checkpoint are needed on resume.
    """
    result = decode_training(record["result"])
    if (
        result.request != request
        or len(result.checkpoints) != request.epochs
        or {item.epoch for item in result.checkpoints} != set(range(request.epochs))
        or record["checkpoint_keys"]
        != {item.sha256: content_key(item.sha256) for item in result.checkpoints}
    ):
        raise ValueError("export training differs from authorized request")
    validate_runtime_evidence(
        request, manifest, record["tensor_shapes"], record["target_retention"]
    )
    validate_checkpoint_reports(
        result, tuple(decode_report(item) for item in record["reports"]), manifest["class_map"]
    )
    # Use at most 16 deterministic validation derivatives, never train/test samples.
    names = sorted(
        sample["image_path"] for sample in manifest["samples"] if sample["split"] == "val"
    )
    if (
        not names
        or len(set(names)) != len(names)
        or any(
            name not in manifest["file_hashes"]
            or PurePosixPath(name).parts[:2] != ("images", "val")
            for name in names
        )
    ):
        raise ValueError("export needs distinct compiled validation samples")
    if checked_object(objects, request.dataset_manifest_sha256, limit=2_000_000) != json_bytes(
        manifest
    ):
        raise ValueError("stored compiler manifest differs from publication")
    data = checked_object(objects, result.selected.sha256, limit=256_000_000)
    with TemporaryDirectory(prefix="canonical-export-") as temporary:
        root = Path(temporary)
        dataset = root / "dataset"
        materialize_dataset(objects, manifest, dataset)
        weights = root / "selected.pt"
        weights.write_bytes(data)
        images = tuple(dataset / name for name in names[:16])
        evidence = exporter(
            weights=weights,
            expected_weights_sha256=result.selected.sha256,
            images=images,
            policy=request.policy,
            evidence=root / "export-equivalence.json",
        )
        if (
            evidence.get("sample_count") != len(images)
            or weights.is_symlink()
            or not weights.is_file()
            or weights.stat().st_size != len(data)
            or weights.read_bytes() != data
        ):
            raise ValueError("export changed its checkpoint or sample coverage")
        return collect_export(result, manifest, weights.with_suffix(".onnx"), evidence, objects)


@dataclass(frozen=True)
class CanonicalLocalExportWorker:
    training: CanonicalLocalTrainingWorker
    exporter: LocalExporter

    def export(self, context: ModelContext) -> dict:
        request, manifest, _ = self.training.resolve(context)
        return export_training(
            request, manifest, context.artifacts["training"], self.training.objects, self.exporter
        )


def candidate_release(
    result: TrainingResult, manifest: dict, project_key: str, evidence: dict
) -> ModelRelease:
    """Derive identity and preprocessing from verified training, not exporter fields."""
    request = result.request
    release = ModelRelease(
        release_id="model-" + request.run_id,
        project_id=project_key,
        recipe_id=request.recipe_id,
        weights_sha256=result.selected.sha256,
        preprocessing_sha256=record_digest(manifest["recipe"]),
        class_map_sha256=request.class_map_sha256,
        class_ids=tuple(sorted(manifest["class_map"], key=manifest["class_map"].__getitem__)),
        policy=request.policy,
        ultralytics_version=result.ultralytics_version,
        torch_version=result.torch_version,
        export_sha256=evidence["export_sha256"],
        export_format="onnx",
        static_input_signature=tuple(evidence["static_input_signature"]),
        evaluation_run_ids=(result.selected.evaluation_run_id,),
    )
    validate_export(release, evidence)
    return release


def collect_export(
    result: TrainingResult,
    manifest: dict,
    artifact: Path,
    evidence: dict,
    objects: CompilationObjects,
) -> dict:
    """Hash the actual verified ONNX file before content-addressed upload.

    This consumes the controlled exporter's evidence, not a client assertion, and
    does not itself run ONNX or establish native/export equivalence.
    """
    candidate_release(result, manifest, result.request.project_id, evidence)
    if artifact.is_symlink() or not artifact.is_file():
        raise ValueError("missing immutable export file")
    size = artifact.stat().st_size
    if not 0 < size <= 256_000_000:
        raise ValueError("export bytes exceed their bound")
    data = artifact.read_bytes()
    if len(data) != size or hashlib.sha256(data).hexdigest() != evidence["export_sha256"]:
        raise ValueError("export bytes differ from verified export")
    record = {
        "evidence": evidence,
        "export_key": content_key(evidence["export_sha256"]),
        "weights_key": content_key(result.selected.sha256),
    }
    if len(canonical_json(record)) > 2_000_000:
        raise ValueError("export exceeds the queue's bounded inline evidence")
    objects.put(record["export_key"], data)
    return record


@dataclass(frozen=True)
class CanonicalLocalModelPublisher:
    training: CanonicalLocalTrainingWorker

    def publish(self, connection: psycopg.Connection, lease: CompileLease, artifacts: dict) -> None:
        """JobStore.complete sink: training, reports, candidate and audit commit once."""
        self.training.publish(connection, lease, artifacts)
        result = decode_training(artifacts["training"]["result"])
        record = artifacts["export"]
        # The training sink has already checked the owned intent/compiler lineage
        # and translated its UUID identity. Read only that just-persisted run.
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT input_manifest FROM training_run WHERE run_key=%s",
                (result.request.run_id,),
            )
            manifest = cursor.fetchone()["input_manifest"]
        release = candidate_release(result, manifest, lease.project_key, record["evidence"])
        if record["weights_key"] != content_key(release.weights_sha256) or record[
            "export_key"
        ] != content_key(release.export_sha256):
            raise ValueError("model artifact keys differ from verified content")
        save_model_transaction(
            connection,
            training_run_key=result.request.run_id,
            release=release,
            export_evidence=record["evidence"],
        )
        connection.execute(
            "INSERT INTO canonical_event(event_type,aggregate_id,expected_revision,"
            "payload,actor_kind,actor_id) "
            "VALUES ('management.model_exported',%s,1,%s,'system',%s)",
            (
                release.release_id,
                Jsonb(
                    {
                        "workspace_id": str(self.training.workspace_id),
                        "project_id": result.request.project_id,
                        "project_key": lease.project_key,
                        "variant_id": lease.input["variant_id"],
                        "training_run_id": result.request.run_id,
                        "job_id": lease.job_id,
                        "generation": lease.generation,
                        "requested_by": lease.input["requested_by"],
                        "stage_sha256": hashlib.sha256(canonical_json(record)).hexdigest(),
                        "weights_key": record["weights_key"],
                        "export_key": record["export_key"],
                    }
                ),
                lease.worker_id,
            ),
        )
