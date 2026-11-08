"""Local export collection and atomic candidate publication, never qualification.

The ML process supplies export_and_compare evidence. No default worker, export
stage or app mount is installed here; deployment owns that trusted composition.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from labelloop_core.json_records import canonical_json
from labelloop_core.registry import ModelRelease
from labelloop_core.training import TrainingResult

from .management_compile_worker import CompilationObjects, CompileLease, content_key
from .management_train_worker import CanonicalLocalTrainingWorker, decode_training
from .model_store import record_digest, save_model_transaction, validate_export


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
