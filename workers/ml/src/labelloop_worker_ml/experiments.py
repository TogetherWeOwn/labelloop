"""Validate frozen experiment inputs before a caller dispatches controlled training."""

import hashlib
import json
from collections import Counter
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
    train_ids = next(
        d.training_asset_ids
        for d in manifest.datasets
        if (d.recipe_id, d.cohort) == (cell.recipe_id, cell.cohort)
    )
    dev_ids = {s.asset_id for s in manifest.sources if s.role == "development"}
    pins = {s.asset_id: s for s in manifest.sources}
    loader = YAML.load(backend.dataset / "data.yaml")
    names, count = loader.get("names"), len(manifest.class_mirror_safety)
    if (
        not isinstance(names, (list, dict))
        or len(names) != count
        or (isinstance(names, dict) and {str(k) for k in names} != {str(n) for n in range(count)})
        or ("nc" in loader and (type(loader["nc"]) is not int or loader["nc"] != count))
    ):
        raise ValueError("loader class domain differs from experiment")
    for split, ids in (("train", train_ids), ("val", dev_ids)):
        samples = [s for s in dataset["samples"] if s["split"] == split]
        if Counter(s["asset_id"] for s in samples) != Counter(ids):
            raise ValueError("compiled membership differs from experiment cohort")
        paths = [s["image_path"] for s in samples]
        labels = [f"labels/{split}/{Path(path).stem}.txt" for path in paths]
        loaded = {
            name for name in dataset["file_hashes"] if Path(name).parts[:2] == ("images", split)
        }
        loaded_labels = {
            name for name in dataset["file_hashes"] if Path(name).parts[:2] == ("labels", split)
        }
        if (
            loader.get(split) != f"images/{split}"
            or len(set(paths)) != len(samples)
            or len(set(labels)) != len(samples)
            or set(paths) != loaded
            or set(labels) != loaded_labels
            or [s.get("label_path") for s in samples] != labels
            or any(Path(path).parent != Path("images") / split for path in paths)
        ):
            raise ValueError("compiled loader membership differs from experiment")
        for sample in samples:
            asset_hash = hashlib.sha256(sample["asset_id"].encode("utf-8")).hexdigest()
            basename = f"{pins[sample['asset_id']].source_sha256}-{asset_hash}"
            for kind, folder, suffix in (("image", "images", ".jpg"), ("label", "labels", ".txt")):
                path = f"{folder}/{split}/{basename}{suffix}"
                if sample[f"{kind}_path"] != path or sample.get(f"{kind}_sha256") != dataset[
                    "file_hashes"
                ].get(path):
                    raise ValueError("compiled loader file identity differs from experiment")
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
