"""Validate frozen experiment inputs before a caller dispatches controlled training."""

import hashlib
import json
from pathlib import Path

from ultralytics.utils import YAML

from labelloop_core.experiments import Cell, ExperimentManifest
from labelloop_worker_ml.training import LocalUltralyticsBackend, file_digest, verify_dataset_files


def validate_inputs(
    manifest: ExperimentManifest, cell: Cell, backend: LocalUltralyticsBackend
) -> dict:
    """Check source membership and roles before any training can select a checkpoint."""
    expected = manifest.request(cell, "input-validation")
    dataset = json.loads((backend.dataset / "manifest.json").read_text())
    if file_digest(backend.dataset / "manifest.json") != expected.dataset_manifest_sha256:
        raise ValueError("dataset manifest digest mismatch")
    verify_dataset_files(backend.dataset, dataset)
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
    loader = YAML.load(backend.dataset / "data.yaml")
    for split in ("train", "val"):
        paths = {s["image_path"] for s in dataset["samples"] if s["split"] == split}
        loaded = {
            name for name in dataset["file_hashes"] if Path(name).parts[:2] == ("images", split)
        }
        if (
            loader.get(split) != f"images/{split}"
            or paths != loaded
            or any(Path(path).parent != Path("images") / split for path in paths)
        ):
            raise ValueError("compiled loader membership differs from experiment")
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
