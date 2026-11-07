"""Real CPU integration cases run only in the explicitly enabled Actions ML job."""

import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LABELLOOP_ML_SMOKE") != "1", reason="requires the Actions CPU ML environment"
)


def test_transform_audit_rejects_a_dropped_tiny_target() -> None:
    import numpy as np
    import torch
    from ultralytics.utils.instance import Instances

    from labelloop_worker_ml.training import AuditedTransforms

    def drop_target(labels: dict) -> dict:
        return {
            "bboxes": torch.empty((0, 4)),
            "cls": torch.empty((0, 1)),
            "img": torch.zeros((3, 320, 320)),
        }

    records = []
    transform = AuditedTransforms(drop_target, records)
    with pytest.raises(ValueError, match="dataloader dropped targets"):
        transform(
            {
                "im_file": "tiny.png",
                "img": np.zeros((320, 320, 3)),
                "cls": np.array([[0]]),
                "instances": Instances(
                    np.array([[0.5, 0.5, 2 / 320, 2 / 320]]), bbox_format="xywh", normalized=True
                ),
            }
        )
    assert records[0].before_at_or_below_6px == 1
    assert (records[0].before, records[0].after) == (1, 0)


@pytest.mark.parametrize("side", [320, 640])
def test_cpu_training_audits_real_batches_and_selects_source_frame_checkpoint(
    tmp_path: Path, side: int
) -> None:
    import torch
    from ultralytics import YOLO
    from ultralytics.utils import SETTINGS

    from labelloop_compiler.compiler import compile_dataset
    from labelloop_core.evaluation import EvaluationImage, GroundTruth
    from labelloop_core.training import InferencePolicy, TrainingRequest
    from labelloop_worker_ml.training import LocalUltralyticsBackend, file_digest

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fixtures"))
    from load_canonical import FixtureDomain

    torch.set_num_threads(2)
    SETTINGS.update({"sync": False})
    torch.manual_seed(0)
    # No downloaded weights: a locally seeded yolo26n architecture is licence-clean CI input.
    initialization = tmp_path / "initialization.pt"
    YOLO("yolo26n.yaml").save(str(initialization))
    fixture = FixtureDomain("cc0-interface")
    dataset = tmp_path / "compiled"
    recipe_id = f"full-letterbox-{side}-v1"
    manifest = compile_dataset(fixture.release, recipe_id, dataset, fixture.images)
    assert len(manifest["samples"]) >= 16
    original_files = {
        path.relative_to(dataset): file_digest(path)
        for path in dataset.rglob("*")
        if path.is_file()
    }
    validation_images = tuple(
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
        )
        for sample in fixture.release.samples
        if sample.split == "val"
    )
    request = TrainingRequest(
        run_id=f"smoke-{side}",
        project_id=fixture.release.project_id,
        recipe_id=recipe_id,
        dataset_release_id=fixture.release.release_id,
        dataset_manifest_sha256=file_digest(dataset / "manifest.json"),
        class_map_sha256=file_digest(dataset / "class-map.json"),
        split_assignment_version=fixture.release.assignment.assignment_version,
        initialization_sha256=file_digest(initialization),
        policy=InferencePolicy(side),
        epochs=1,
        batch=2,
    )
    output = tmp_path / "run"
    backend = LocalUltralyticsBackend(
        dataset=dataset,
        initialization=initialization,
        output=output,
        validation_images=validation_images,
    )
    result = backend.train(request)
    assert len(result.checkpoints) == 1
    assert result.selected == result.checkpoints[0]
    epoch_file = output / "trainer" / "weights" / "epoch0.pt"
    assert result.selected.sha256 == file_digest(epoch_file)
    shapes = json.loads((output / "tensor-shapes.json").read_text())
    for stage in ("training", "validation", "inference"):
        assert shapes[stage], stage
        assert all(shape[1:] == [3, side, side] for shape in shapes[stage])
    retention = json.loads((output / "target-retention.json").read_text())
    assert retention and all(item["before"] == item["after"] for item in retention)
    train_names = {
        Path(sample["image_path"]).name
        for sample in manifest["samples"]
        if sample["split"] == "train"
    }
    assert {item["sample"] for item in retention} == train_names
    report = json.loads((output / "evaluation-epoch-0.json").read_text())
    assert report["role"] == "development"
    assert report["weights_sha256"] == result.selected.sha256
    assert report["overall"]["support"] == sum(len(image.objects) for image in validation_images)
    assert all(item["covered_fraction"] == 1 for item in report["coverage"])
    assert original_files == {
        path.relative_to(dataset): file_digest(path)
        for path in dataset.rglob("*")
        if path.is_file()
    }
    # Fail closed before creating any run directory when a frozen input changes.
    initialization.write_bytes(b"modified checkpoint")
    with pytest.raises(ValueError, match="checkpoint digest mismatch"):
        backend.train(request)
