"""Real CPU integration cases run only in the explicitly enabled Actions ML job."""

import json
import os
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LABELLOOP_ML_SMOKE") != "1", reason="requires the Actions CPU ML environment"
)


def test_ml_runtime_uses_paired_cpu_dependency_pins() -> None:
    import onnx
    import onnxruntime
    import torch
    import torchvision
    import ultralytics

    suffix = "+cpu" if sys.platform == "linux" else ""
    assert str(torch.__version__) == f"2.13.0{suffix}"
    assert torchvision.__version__ == f"0.28.0{suffix}"
    assert onnx.__version__ == "1.22.0"
    assert onnxruntime.__version__ == "1.24.4"
    assert ultralytics.__version__ == "8.4.204"
    if sys.platform == "linux":
        assert torch.version.cuda is None
        assert torch.version.hip is None


@pytest.mark.parametrize("column,delta", [(0, 0.02), (4, 0.001), (5, 1)])
def test_export_comparison_rejects_geometry_score_and_class_drift(
    column: int, delta: float
) -> None:
    import numpy as np

    from labelloop_worker_ml.export import compare_detections

    native = np.array([[10, 20, 30, 40, 0.8, 0], [11, 21, 31, 41, 0.7, 0]])
    compare_detections(native, native[::-1])
    changed = native.copy()
    changed[0, column] += delta
    with pytest.raises(ValueError, match="exceed declared tolerance"):
        compare_detections(native, changed)
    with pytest.raises(ValueError, match="count or layout mismatch"):
        compare_detections(native, native[:1])
    with pytest.raises(ValueError, match="exceed declared tolerance"):
        compare_detections(native, np.repeat(native[:1], 2, axis=0))
    changed[0, 0] = float("nan")
    with pytest.raises(ValueError, match="nonfinite"):
        compare_detections(native, changed)


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
@pytest.mark.parametrize(
    "width,edge", [(2, False), (2.1, False), (4, True), (6, False), (0, False)]
)
def test_real_loader_target_retention_boundaries(
    tmp_path: Path, side: int, width: float, edge: bool
) -> None:
    from PIL import Image
    from ultralytics.cfg import get_cfg
    from ultralytics.data.build import build_yolo_dataset

    from labelloop_core.training import InferencePolicy, TrainingRequest
    from labelloop_worker_ml.training import AuditedTransforms

    images = tmp_path / "images" / "train"
    labels = tmp_path / "labels" / "train"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    Image.new("RGB", (side, side), (114, 114, 114)).save(images / "boundary.png")
    center = width / (2 * side) if edge else 0.5
    row = f"0 {center} 0.5 {width / side} {max(width, 4) / side}\n" if width else ""
    (labels / "boundary.txt").write_text(row)
    request = TrainingRequest(
        "boundary",
        "p",
        "r",
        "d",
        "a" * 64,
        "b" * 64,
        "v1",
        "c" * 64,
        InferencePolicy(side),
    )
    dataset = build_yolo_dataset(
        get_cfg(overrides=request.arguments()),
        str(images),
        1,
        {"names": {0: "object"}, "nc": 1, "channels": 3},
        mode="train",
        rect=False,
        stride=32,
    )
    records = []
    dataset.transforms = AuditedTransforms(dataset.transforms, records)
    if width == 2:
        with pytest.raises(ValueError, match="dataloader dropped targets"):
            dataset[0]
        assert records[0].before == 1 and records[0].after == 0
    else:
        sample = dataset[0]
        assert tuple(sample["img"].shape) == (3, side, side)
        assert records[0].before == records[0].after == int(width > 0)
    assert records[0].before_at_or_below_6px == int(width > 0)


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
    from labelloop_worker_ml.export import export_and_compare
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
            revision=sample.revision.revision,
            source_sha256=sample.asset.sha256,
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
        # The seeded coarse head scores near .012 at both sizes; the next head is near .003.
        # A fixed threshold keeps the complete coarse cohort below the cap at either size.
        policy=InferencePolicy(side, conf=0.005, max_det=1000),
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
    truth = next(image for image in validation_images if image.objects)
    assert truth.revision is not None
    obj = truth.objects[0]
    stale_objects = (
        replace(obj, object_id=obj.object_id + "-stale"),
        replace(obj, class_id="stale-class"),
        replace(obj, box=replace(obj.box, x_max=obj.box.x_max - 1)),
        replace(obj, occluded=not obj.occluded),
        replace(obj, truncated=not obj.truncated),
    )
    stale_images = (
        replace(truth, revision=truth.revision + 1),
        replace(truth, source_sha256="f" * 64),
        replace(truth, width=truth.width + 1),
        replace(truth, objects=()),
        *(replace(truth, objects=(stale,) + truth.objects[1:]) for stale in stale_objects),
    )
    for stale in stale_images:
        invalid = tuple(
            stale if image.asset_id == truth.asset_id else image for image in validation_images
        )
        with pytest.raises(ValueError, match="differs from frozen canonical truth"):
            LocalUltralyticsBackend(
                dataset=dataset,
                initialization=initialization,
                output=output,
                validation_images=invalid,
            ).train(request)
        assert not output.exists()
    for invalid in (
        validation_images + (validation_images[0],),
        validation_images + (replace(truth, asset_id="unlisted-source"),),
        validation_images[1:],
    ):
        with pytest.raises(ValueError, match="must match every validation source"):
            LocalUltralyticsBackend(
                dataset=dataset,
                initialization=initialization,
                output=output,
                validation_images=invalid,
            ).train(request)
        assert not output.exists()
    rogue_image = dataset / "images/train/unlisted.png"
    rogue_label = dataset / "labels/train/unlisted.txt"
    rogue_image.write_bytes((dataset / manifest["samples"][0]["image_path"]).read_bytes())
    rogue_label.write_text("")
    with pytest.raises(ValueError, match="membership differs"):
        backend.train(request)
    assert not output.exists()
    rogue_image.unlink()
    rogue_label.unlink()
    rogue_image.symlink_to(dataset / manifest["samples"][0]["image_path"])
    with pytest.raises(ValueError, match="membership differs"):
        backend.train(request)
    assert not output.exists()
    rogue_image.unlink()
    result = backend.train(request)
    # Exercise management's artifact collector on the actual CPU backend output,
    # without claiming this standalone fixture is a DB-backed management job.
    from types import SimpleNamespace

    from labelloop_api.management_train_worker import collect_training

    saved_checkpoints = {}
    stage_record = collect_training(
        request,
        result,
        manifest,
        output,
        SimpleNamespace(put=lambda key, data: saved_checkpoints.__setitem__(key, data)),
    )
    assert stage_record["result"]["selected"]["sha256"] == result.selected.sha256
    assert stage_record["reports"][0]["weights_sha256"] == result.selected.sha256
    assert len(saved_checkpoints) == 1
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
    from labelloop_api.management_compile_worker import content_key
    from labelloop_api.management_model_worker import export_training

    # Exercise the real orchestration helper with CPU export_and_compare, not a
    # synthetic exporter. This standalone fixture still does not prove DB wiring.
    stored = saved_checkpoints | {
        content_key(digest): (dataset / name).read_bytes()
        for name, digest in manifest["file_hashes"].items()
    }
    stored[content_key(request.dataset_manifest_sha256)] = (dataset / "manifest.json").read_bytes()
    saved_exports, results = {}, []

    def exporter(**kwargs):
        exported = export_and_compare(**kwargs)
        (output / "export-equivalence.json").write_bytes(kwargs["evidence"].read_bytes())
        results.append(exported)
        return asdict(exported)

    export_stage = export_training(
        request,
        manifest,
        stage_record,
        SimpleNamespace(
            read=stored.__getitem__,
            put=lambda key, data: saved_exports.__setitem__(key, data),
        ),
        exporter,
    )
    exported = results[0]
    assert list(saved_exports) == [export_stage["export_key"]]
    assert export_stage["evidence"] == asdict(exported)
    epoch_file.with_suffix(".onnx").write_bytes(saved_exports[export_stage["export_key"]])
    assert exported.static_input_signature == (1, 3, side, side)
    assert exported.export_sha256 == file_digest(epoch_file.with_suffix(".onnx"))
    assert exported.sample_count == len(validation_images)
    assert all(0 < count < request.policy.max_det for count in exported.detection_counts)
    from labelloop_api.management_evaluate_worker import evaluate_model
    from labelloop_api.management_model_worker import candidate_release
    from labelloop_worker_ml.training import predict_samples

    # Run standalone development inference through the same delivered predictor and
    # original-frame evaluator. This is not queue/DB publication or qualification.
    model = candidate_release(result, manifest, request.project_id, export_stage["evidence"])
    evaluated = evaluate_model(
        f"standalone-{side}",
        model,
        manifest,
        validation_images,
        SimpleNamespace(read=stored.__getitem__),
        predict_samples,
    )
    (output / "management-development-evaluation.json").write_text(json.dumps(evaluated))
    assert evaluated["report"]["role"] == "development"
    assert evaluated["report"]["weights_sha256"] == result.selected.sha256
    assert evaluated["report"]["overall"] == report["overall"]
    assert evaluated["report"]["slices"] == report["slices"]
    assert evaluated["sample_count"] == len(validation_images)
    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(
        str(epoch_file.with_suffix(".onnx")), providers=["CPUExecutionProvider"]
    )
    with pytest.raises(ort.capi.onnxruntime_pybind11_state.InvalidArgument):
        session.run(
            None,
            {
                session.get_inputs()[0].name: np.zeros(
                    (1, 3, side + 32, side + 32), dtype=np.float32
                )
            },
        )
    from registry_smoke import exercise_registry

    exercise_registry(result, exported, manifest, output)
    if side == 320:
        empty_weights = output / "empty-export.pt"
        empty_weights.write_bytes(epoch_file.read_bytes())
        empty_evidence = output / "empty-export.json"
        with pytest.raises(ValueError, match="requires at least one detection"):
            export_and_compare(
                weights=empty_weights,
                expected_weights_sha256=result.selected.sha256,
                images=tuple(
                    dataset / sample["image_path"]
                    for sample in manifest["samples"]
                    if sample["split"] == "val"
                ),
                policy=replace(request.policy, conf=1.0),
                evidence=empty_evidence,
            )
        assert not empty_evidence.exists()
    # Fail closed before creating any run directory when a frozen input changes.
    initialization.write_bytes(b"modified checkpoint")
    with pytest.raises(ValueError, match="checkpoint digest mismatch"):
        backend.train(request)
