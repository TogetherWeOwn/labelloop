"""Tiny factorial wiring experiment. Seeded fixtures cannot establish quality improvement."""

import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LABELLOOP_ML_SMOKE") != "1", reason="requires the Actions CPU ML environment"
)


def test_paired_cpu_experiment_preserves_m2_contracts(tmp_path: Path) -> None:
    import torch
    from ultralytics import YOLO
    from ultralytics.utils import SETTINGS

    from labelloop_compiler.compiler import compile_dataset
    from labelloop_core.evaluation import EvaluationImage, GroundTruth
    from labelloop_core.experiments import (
        BASELINE,
        DatasetPin,
        ExperimentManifest,
        RecipePin,
        SourcePin,
        compare_experiment,
    )
    from labelloop_core.training import AugmentationProfile, InferencePolicy, TrainingRequest
    from labelloop_worker_ml.experiments import collect_arm, execute_cell, validate_inputs
    from labelloop_worker_ml.training import LocalUltralyticsBackend, file_digest, write_record

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fixtures"))
    from load_canonical import FixtureDomain

    torch.set_num_threads(2)
    SETTINGS.update({"sync": False})
    torch.manual_seed(0)
    initialization = tmp_path / "initialization.pt"
    YOLO("yolo26n.yaml").save(str(initialization))
    fixture = FixtureDomain("cc0-interface")
    # No mirror-safety approval exists for this fixture; color-only alternatives are valid.
    classes = tuple((c.stable_id, False) for c in fixture.release.ontology.classes)
    images = tuple(
        EvaluationImage(
            sample.asset.asset_id,
            sample.asset.width,
            sample.asset.height,
            tuple(
                GroundTruth(
                    obj.object_id, obj.class_id, obj.box, obj.occluded, obj.truncated_at_image_edge
                )
                for obj in sample.revision.objects
            ),
            revision=sample.revision.revision,
            source_sha256=sample.asset.sha256,
        )
        for sample in fixture.release.samples
        if sample.split == "val"
    )
    datasets = {}
    backends = {}
    base_results = {}
    for side in (320, 640):
        recipe_id = f"full-letterbox-{side}-v1"
        dataset = tmp_path / f"compiled-{side}"
        compiled = compile_dataset(fixture.release, recipe_id, dataset, fixture.images)
        request = TrainingRequest(
            f"base-{side}",
            fixture.release.project_id,
            recipe_id,
            fixture.release.release_id,
            file_digest(dataset / "manifest.json"),
            file_digest(dataset / "class-map.json"),
            fixture.release.assignment.assignment_version,
            file_digest(initialization),
            InferencePolicy(side, conf=0.005, max_det=1000),
            class_mirror_safety=classes,
        )
        backend = LocalUltralyticsBackend(
            dataset=dataset,
            initialization=initialization,
            output=tmp_path / f"base-{side}",
            validation_images=images,
        )
        datasets[recipe_id] = compiled
        backends[recipe_id] = backend
        base_results[recipe_id] = backend.train(request)
    first = next(iter(datasets.values()))
    sources = tuple(
        SourcePin(
            sample["asset_id"],
            sample["group_id"],
            sample["source_sha256"],
            sample["source_truth_sha256"],
            {"train": "curated", "val": "development", "test": "locked_final_test"}[
                sample["split"]
            ],
        )
        for sample in first["samples"]
    )
    manifest = ExperimentManifest(
        "cpu-fixture-factorial-v1",
        fixture.release.project_id,
        next(iter(base_results.values())).request.class_map_sha256,
        classes,
        fixture.release.assignment.assignment_version,
        file_digest(initialization),
        sources,
        tuple(
            RecipePin(
                recipe_id,
                hashlib.sha256(
                    json.dumps(compiled["recipe"], sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                base_results[recipe_id].request.policy,
                base_results[recipe_id].selected.sha256,
                file_digest(initialization),
                tuple(sorted({s.group_id for s in sources if s.role == "curated"})),
            )
            for recipe_id, compiled in datasets.items()
        ),
        tuple(
            DatasetPin(
                recipe_id,
                "curated-only",
                fixture.release.release_id,
                base_results[recipe_id].request.dataset_manifest_sha256,
                tuple(s.asset_id for s in sources if s.role == "curated"),
            )
            for recipe_id in datasets
        ),
        (BASELINE, AugmentationProfile("color-value-02-v1", hsv_v=0.2)),
        (0,),
        fixture_only=True,
    )
    write_record(
        tmp_path / "experiment-manifest.json",
        {"sha256": manifest.digest(), "manifest": asdict(manifest)},
    )
    arms = []
    for index, cell in enumerate(manifest.cells()):
        original = backends[cell.recipe_id]
        if cell.initialization == "base" and cell.augmentation_id == BASELINE.profile_id:
            image_path = next(
                s["image_path"] for s in datasets[cell.recipe_id]["samples"] if s["split"] == "val"
            )
            image = original.dataset / image_path
            saved = image.read_bytes()
            # A trailing newline leaves decoding/predictions unchanged, but changes frozen bytes.
            image.write_bytes(saved + b"\n")
            with pytest.raises(ValueError, match="compiled dataset file digest mismatch"):
                collect_arm(manifest, cell, original, base_results[cell.recipe_id])
            assert not (original.output / "experiment-inputs").exists()
            image.write_bytes(saved)
            arms.append(collect_arm(manifest, cell, original, base_results[cell.recipe_id]))
            continue
        warm = base_results[cell.recipe_id]
        backend = LocalUltralyticsBackend(
            dataset=original.dataset,
            initialization=initialization
            if cell.initialization == "base"
            else original.output / "trainer" / "weights" / f"epoch{warm.selected.epoch}.pt",
            output=tmp_path / f"cell-{index}",
            validation_images=images,
        )
        # Verify a changed recipe is refused before dispatching another training run.
        from dataclasses import replace

        wrong = replace(
            manifest, recipes=tuple(replace(r, recipe_sha256="f" * 64) for r in manifest.recipes)
        )
        with pytest.raises(ValueError, match="recipe configuration drift"):
            validate_inputs(wrong, cell, backend)
        assert not backend.output.exists()
        arms.append(execute_cell(manifest, cell, backend, run_id=f"cell-{index}"))
    comparisons = compare_experiment(manifest, tuple(arms))
    write_record(tmp_path / "experiment-comparison.json", [asdict(r) for r in comparisons])
    assert len(arms) == 8
    assert {r.recipe_id for r in comparisons} == set(datasets)
    assert {r.axis for r in comparisons} == {"initialization", "augmentation"}
    assert all(r.fixture_only and not r.m6_exit_established for r in comparisons)
    assert all(r.outcome != "observed-gain-unqualified" for r in comparisons)
