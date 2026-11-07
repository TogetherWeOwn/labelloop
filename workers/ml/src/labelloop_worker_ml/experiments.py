"""Execute one caller-selected frozen cell through the existing controlled backend."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from labelloop_core.evaluation import EvaluationRunner
from labelloop_core.experiments import (
    ArmEvidence,
    Cell,
    ExperimentManifest,
    RetentionObservation,
    validate_arm,
)
from labelloop_core.training import TrainingResult
from labelloop_worker_ml.export import ExportResult, export_and_compare
from labelloop_worker_ml.training import (
    LocalUltralyticsBackend,
    file_digest,
    predict_samples,
    write_record,
)


def validate_inputs(
    manifest: ExperimentManifest, cell: Cell, backend: LocalUltralyticsBackend
) -> dict:
    """Check source membership and roles before any training can select a checkpoint."""
    expected = manifest.request(cell, "input-validation")
    dataset = json.loads((backend.dataset / "manifest.json").read_text())
    if file_digest(backend.dataset / "manifest.json") != expected.dataset_manifest_sha256:
        raise ValueError("dataset manifest digest mismatch")
    recipe = next(r for r in manifest.recipes if r.recipe_id == cell.recipe_id)
    digest = hashlib.sha256(
        json.dumps(dataset["recipe"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if digest != recipe.recipe_sha256:
        raise ValueError("recipe configuration drift")
    train_ids = set(
        next(
            d.training_asset_ids
            for d in manifest.datasets
            if (d.recipe_id, d.cohort) == (cell.recipe_id, cell.cohort)
        )
    )
    dev_ids = {s.asset_id for s in manifest.sources if s.role == "development"}
    pins = {s.asset_id: s for s in manifest.sources}
    if {s["asset_id"] for s in dataset["samples"] if s["split"] == "train"} != train_ids or {
        s["asset_id"] for s in dataset["samples"] if s["split"] == "val"
    } != dev_ids:
        raise ValueError("compiled membership differs from experiment cohort")
    for sample in dataset["samples"]:
        pin = pins.get(sample["asset_id"])
        if pin is None or (
            sample["source_sha256"],
            sample["source_truth_sha256"],
            sample["group_id"],
        ) != (pin.source_sha256, pin.truth_sha256, pin.group_id):
            raise ValueError("compiled source lineage differs from experiment")
        if sample["split"] == "test" and pin.role != "locked_final_test":
            raise ValueError("compiled final-test role mismatch")
    if {i.asset_id for i in backend.validation_images} != dev_ids or any(
        i.truth_digest() != pins[i.asset_id].truth_sha256 for i in backend.validation_images
    ):
        raise ValueError("evaluation truth differs from experiment")
    if file_digest(backend.dataset / "class-map.json") != manifest.class_map_sha256 or set(
        dataset["class_map"]
    ) != set(dict(manifest.class_mirror_safety)):
        raise ValueError("experiment class map mismatch")
    return dataset


def collect_arm(
    manifest: ExperimentManifest,
    cell: Cell,
    backend: LocalUltralyticsBackend,
    result: TrainingResult,
    *,
    exported: ExportResult | None = None,
) -> ArmEvidence:
    """Also accepts already-delivered records; never retrains a completed cell."""
    expected = manifest.request(cell, result.request.run_id)
    if result.request != expected:
        raise ValueError("training request differs from frozen experiment")
    dataset = validate_inputs(manifest, cell, backend)
    weights = backend.output / "trainer" / "weights" / f"epoch{result.selected.epoch}.pt"
    if file_digest(weights) != result.selected.sha256:
        raise ValueError("selected checkpoint digest mismatch")
    samples = [s for s in dataset["samples"] if s["split"] == "val"]
    shapes = json.loads((backend.output / "tensor-shapes.json").read_text())
    observed: list[tuple[int, ...]] = []
    classes = tuple(sorted(dataset["class_map"], key=dataset["class_map"].__getitem__))
    derived = predict_samples(weights, backend.dataset, samples, classes, expected.policy, observed)
    runner = EvaluationRunner(classes, expected.policy)
    arguments = {
        "dataset_release_id": expected.dataset_release_id,
        "recipe_id": expected.recipe_id,
        "weights_sha256": result.selected.sha256,
        "class_map_sha256": expected.class_map_sha256,
        "role": "development",
    }
    report = runner.evaluate(
        images=backend.validation_images,
        derived=derived,
        run_id=result.selected.evaluation_run_id,
        **arguments,
    )
    if report.overall.map50_95 != result.selected.validation_map:
        raise ValueError("selected checkpoint evaluation drift")
    group_reports = []
    for group in manifest.development_groups():
        ids = {
            s.asset_id for s in manifest.sources if s.role == "development" and s.group_id == group
        }
        group_reports.append(
            (
                group,
                runner.evaluate(
                    images=tuple(i for i in backend.validation_images if i.asset_id in ids),
                    derived=tuple(d for d in derived if d.asset_id in ids),
                    run_id=f"{result.selected.evaluation_run_id}-group-{group}",
                    **arguments,
                ),
            )
        )
    if exported is None:
        exported = export_and_compare(
            weights=weights,
            expected_weights_sha256=result.selected.sha256,
            images=tuple(backend.dataset / s["image_path"] for s in samples),
            policy=expected.policy,
            evidence=backend.output / "experiment-export.json",
        )
    if file_digest(weights.with_suffix(".onnx")) != exported.export_sha256:
        raise ValueError("export artifact digest mismatch")
    sample_ids = {
        Path(s["image_path"]).name: s["asset_id"]
        for s in dataset["samples"]
        if s["split"] == "train"
    }
    retention = tuple(
        RetentionObservation(
            sample_ids[r["sample"]],
            r["before"],
            r["after"],
            r["before_at_or_below_6px"],
            r["after_at_or_below_6px"],
        )
        for r in json.loads((backend.output / "target-retention.json").read_text())
    )
    arm = ArmEvidence(
        manifest.digest(),
        cell,
        result,
        report,
        tuple(group_reports),
        retention,
        tuple((stage, tuple(tuple(shape) for shape in values)) for stage, values in shapes.items()),
        exported.export_sha256,
        exported.weights_sha256,
        exported.static_input_signature,
        exported.policy,
        exported.sample_count,
        sum(exported.detection_counts),
    )
    validate_arm(manifest, arm)
    write_record(backend.output / "experiment-arm.json", asdict(arm))
    return arm


def execute_cell(
    manifest: ExperimentManifest, cell: Cell, backend: LocalUltralyticsBackend, *, run_id: str
) -> ArmEvidence:
    """No scheduling, paid dispatch, checkpoint download or controller state changes."""
    request = manifest.request(cell, run_id)
    validate_inputs(manifest, cell, backend)
    result = backend.train(request)
    write_record(
        backend.output / "experiment-manifest.json",
        {"sha256": manifest.digest(), "manifest": asdict(manifest)},
    )
    return collect_arm(manifest, cell, backend, result)
