"""Real CPU integration cases run only in the explicitly enabled Actions ML job."""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LABELLOOP_ML_SMOKE") != "1", reason="requires the Actions CPU ML environment"
)


@pytest.mark.parametrize("side", (320, 640))
@pytest.mark.parametrize(
    "change",
    (
        "none",
        "reuse-export",
        "request",
        "inputs",
        "weights",
        "copy",
        "selection",
        "retention",
        "shape",
        "observed-shape",
        "original-inference-shape",
        "original-inference-empty",
        "original-inference-missing",
        "observed-empty",
        "export-bytes",
        "export-policy",
        "existing-snapshot",
        "execute",
        "execute-inputs",
    ),
)
def test_experiment_collection_binds_delivered_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, side: int, change: str
) -> None:
    from unittest.mock import Mock

    from labelloop_core.evaluation import EvaluationImage
    from labelloop_worker_ml import experiments
    from labelloop_worker_ml.export import ExportResult
    from labelloop_worker_ml.training import LocalUltralyticsBackend, file_digest, write_record

    # Reuse validated records; prediction/export are mocked, never model-quality evidence.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "packages/core/tests"))
    from test_experiment_training_evidence import arm_case

    frozen, reference = arm_case(side)
    cell = reference.cell
    backend = LocalUltralyticsBackend(
        dataset=tmp_path / "compiled",
        initialization=tmp_path / "base.pt",
        output=tmp_path / "output",
        validation_images=(EvaluationImage("dev", 32, 32, ()),),
    )
    image = backend.dataset / "images/val/dev.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"pinned image bytes")
    samples = [
        {"asset_id": "dev", "split": "val", "image_path": "images/val/dev.jpg"},
        {"asset_id": "train", "split": "train", "image_path": "images/train/train.jpg"},
    ]
    dataset = {
        "samples": samples,
        "class_map": {"object": 0},
        "file_hashes": {"images/val/dev.jpg": file_digest(image)},
    }
    weights = backend.output / "trainer/weights/epoch0.pt"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"selected weights")
    weights.with_suffix(".onnx").write_bytes(b"static export")
    checkpoint = replace(reference.training.selected, sha256=file_digest(weights))
    training = replace(reference.training, checkpoints=(checkpoint,), selected=checkpoint)
    report = replace(reference.evaluation, weights_sha256=checkpoint.sha256)
    exported = ExportResult(
        checkpoint.sha256,
        file_digest(weights.with_suffix(".onnx")),
        (1, 3, side, side),
        training.request.policy,
        1,
        (1,),
        0.01,
        1e-5,
        "fixture",
        "fixture",
    )
    shapes = {stage: [[1, 3, side, side]] for stage in ("training", "validation", "inference")}
    retention = [
        {
            "sample": "train.jpg",
            "before": 1,
            "after": 1,
            "before_at_or_below_6px": 1,
            "after_at_or_below_6px": 1,
        }
    ]
    inputs = Mock(return_value=dataset)
    predict = Mock(
        side_effect=lambda *args: (
            args[-1].append(
                (1, 3, 640 if side == 320 else 320, side)
                if change == "observed-shape"
                else (1, 3, side, side)
            )
            or ()
        )
    )
    if change == "observed-empty":
        predict.side_effect = None
        predict.return_value = ()
    evaluate = Mock(return_value=report)
    export = Mock(return_value=exported)
    train = Mock(return_value=training)
    monkeypatch.setattr(experiments, "validate_inputs", inputs)
    monkeypatch.setattr(experiments, "predict_samples", predict)
    monkeypatch.setattr(experiments, "EvaluationRunner", Mock(return_value=Mock(evaluate=evaluate)))
    monkeypatch.setattr(experiments, "export_and_compare", export)
    monkeypatch.setattr(backend, "train", train)
    error = None
    if change == "request":
        request = replace(training.request, epochs=2)
        training = replace(training, request=request, resolved_arguments=request.arguments())
        error = "training request"
    elif change in ("inputs", "execute-inputs"):
        inputs.side_effect = ValueError("frozen inputs rejected")
        error = "frozen inputs"
    elif change == "weights":
        weights.write_bytes(b"changed weights")
        error = "checkpoint digest"
    elif change == "copy":
        image.write_bytes(b"changed image bytes")
        error = "changed while copying"
    elif change == "selection":
        evaluate.return_value = replace(report, overall=replace(report.overall, map50_95=0.25))
        error = "evaluation drift"
    elif change == "retention":
        retention[0]["after"] = 0
        error = "lost targets"
    elif change == "shape":
        shapes["training"] = [[1, 3, 640 if side == 320 else 320, side]]
        error = "shape"
    elif change == "observed-shape":
        error = "shape"
    elif change == "original-inference-shape":
        shapes["inference"] = [[1, 3, 640 if side == 320 else 320, side]]
        error = "shape"
    elif change in ("original-inference-empty", "original-inference-missing", "observed-empty"):
        if change == "original-inference-empty":
            shapes["inference"] = []
        elif change == "original-inference-missing":
            del shapes["inference"]
        error = "inference tensor evidence"
    elif change == "reuse-export":
        error = "unbound export evidence"
    elif change == "export-bytes":
        weights.with_suffix(".onnx").write_bytes(b"changed export bytes")
        error = "export artifact digest"
    elif change == "export-policy":
        exported = replace(exported, policy=replace(exported.policy, conf=0.2))
        export.return_value = exported
        error = "export"
    elif change == "existing-snapshot":
        (backend.output / "experiment-inputs").mkdir()
        error = "exists"
    write_record(backend.output / "tensor-shapes.json", shapes)
    write_record(backend.output / "target-retention.json", retention)

    def collect():
        if change.startswith("execute"):
            return experiments.execute_cell(frozen, cell, backend, run_id=training.request.run_id)
        return experiments.collect_arm(
            frozen,
            cell,
            backend,
            training,
            exported=exported if change == "reuse-export" else None,
        )

    if error:
        with pytest.raises((ValueError, FileExistsError), match=error):
            collect()
        assert not (backend.output / "experiment-arm.json").exists()
        train.assert_not_called()
        if change in ("request", "inputs", "execute-inputs", "weights", "reuse-export"):
            assert not (backend.output / "experiment-inputs").exists()
            predict.assert_not_called()
        return
    arm = collect()
    assert arm.evaluation == report and arm.group_reports == (("dev", report),)
    assert arm.retention == reference.retention
    assert dict(arm.tensor_shapes)["inference"] == ((1, 3, side, side),) * 2
    assert arm.export_sha256 == exported.export_sha256
    assert (
        json.loads((backend.output / "experiment-arm.json").read_text())["manifest_sha256"]
        == frozen.digest()
    )
    snapshot = backend.output / "experiment-inputs"
    assert (snapshot / "images/val/dev.jpg").read_bytes() == image.read_bytes()
    assert predict.call_args.args[1] == snapshot
    assert evaluate.call_count == 2
    if change == "execute":
        train.assert_called_once_with(training.request)
        assert inputs.call_count == 2
        assert (
            json.loads((backend.output / "experiment-manifest.json").read_text())["sha256"]
            == frozen.digest()
        )
    else:
        train.assert_not_called()
    if change == "reuse-export":
        export.assert_not_called()
    else:
        assert export.call_args.kwargs["images"] == (snapshot / "images/val/dev.jpg",)


@pytest.mark.parametrize(
    "change", ("none", "bytes", "missing", "extra", "file-link", "directory-link", "dangling-link")
)
def test_compiled_file_verifier_preserves_frozen_membership(tmp_path: Path, change: str) -> None:
    from labelloop_worker_ml.training import file_digest, verify_dataset_files

    dataset = tmp_path / "compiled"
    images = dataset / "images"
    images.mkdir(parents=True)
    sample = images / "sample.png"
    sample.write_bytes(b"frozen sample bytes")
    manifest = {"file_hashes": {"images/sample.png": file_digest(sample)}}
    (dataset / "manifest.json").write_text(json.dumps(manifest))
    before = {
        path.relative_to(dataset): path.read_bytes()
        for path in dataset.rglob("*")
        if path.is_file()
    }
    verify_dataset_files(dataset, manifest)
    assert before == {
        path.relative_to(dataset): path.read_bytes()
        for path in dataset.rglob("*")
        if path.is_file()
    }
    if change == "none":
        return
    if change == "bytes":
        sample.write_bytes(b"changed sample bytes")
    elif change == "missing":
        sample.unlink()
    elif change == "extra":
        (images / "unlisted.png").write_bytes(sample.read_bytes())
    elif change == "file-link":
        target = tmp_path / "sample.png"
        target.write_bytes(sample.read_bytes())
        sample.unlink()
        sample.symlink_to(target)
    elif change == "directory-link":
        target = tmp_path / "linked-images"
        images.rename(target)
        images.symlink_to(target, target_is_directory=True)
    else:
        (images / "dangling.png").symlink_to(tmp_path / "absent.png")
    error = "file digest mismatch" if change == "bytes" else "membership differs"
    with pytest.raises(ValueError, match=error):
        verify_dataset_files(dataset, manifest)


@pytest.mark.parametrize("root_form", ("relative", "parent-components"))
def test_compiled_file_verifier_normalizes_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root_form: str
) -> None:
    from labelloop_worker_ml.training import file_digest, verify_dataset_files

    dataset = tmp_path / "compiled"
    dataset.mkdir()
    sample = dataset / "sample.txt"
    sample.write_text("frozen bytes")
    manifest = {"file_hashes": {"sample.txt": file_digest(sample)}}
    (dataset / "manifest.json").write_text(json.dumps(manifest))
    if root_form == "relative":
        monkeypatch.chdir(tmp_path)
        root = Path("compiled")
    else:
        root = dataset / ".." / "compiled"
    verify_dataset_files(root, manifest)
    sample.write_text("changed bytes")
    with pytest.raises(ValueError, match="file digest mismatch"):
        verify_dataset_files(root, manifest)


@pytest.mark.parametrize("side", (320, 640))
@pytest.mark.parametrize(
    "change",
    (
        *("none", "manifest", "bytes", "recipe", "train", "val"),
        *("source", "truth", "group", "role", "evaluation", "classes"),
        "loader-train",
        "loader-val",
        "loader-extra",
        "loader-location",
        "loader-extra-class",
        "loader-sparse-classes",
        "loader-nc",
        "loader-nc-bool",
        "loader-list-names",
        "negative",
        *(
            f"{kind}-{split}"
            for kind in (
                "label-missing",
                "label-mismatch",
                "label-extra",
                "duplicate-source",
                "duplicate-record",
                "shared-image",
                "pair-swap",
                "image-extension",
                "image-digest",
                "label-digest",
            )
            for split in ("train", "val")
        ),
    ),
)
def test_experiment_inputs_bind_compiler_and_canonical_truth(
    tmp_path: Path, side: int, change: str
) -> None:
    import hashlib

    from labelloop_compiler.compiler import compile_dataset
    from labelloop_core.evaluation import EvaluationImage, GroundTruth
    from labelloop_core.experiments import (
        BASELINE,
        DatasetPin,
        ExperimentManifest,
        RecipePin,
        SourcePin,
    )
    from labelloop_core.training import AugmentationProfile, InferencePolicy
    from labelloop_worker_ml.experiments import validate_inputs
    from labelloop_worker_ml.training import LocalUltralyticsBackend, file_digest

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fixtures"))
    from load_canonical import FixtureDomain

    fixture = FixtureDomain("cc0-interface")
    if change == "negative":
        fixture.release = replace(
            fixture.release,
            samples=tuple(
                replace(s, revision=replace(s.revision, objects=())) if s.split == "train" else s
                for s in fixture.release.samples
            ),
        )
    variants = {
        n: compile_dataset(
            fixture.release, f"full-letterbox-{n}-v1", tmp_path / f"compiled-{n}", fixture.images
        )
        for n in (320, 640)
    }
    dataset = tmp_path / f"compiled-{side}"
    recipe_id = f"full-letterbox-{side}-v1"
    compiled = variants[side]
    truth = tuple(
        EvaluationImage(
            s.asset.asset_id,
            s.asset.width,
            s.asset.height,
            tuple(
                GroundTruth(o.object_id, o.class_id, o.box, o.occluded, o.truncated_at_image_edge)
                for o in s.revision.objects
            ),
            revision=s.revision.revision,
            source_sha256=s.asset.sha256,
        )
        for s in fixture.release.samples
    )
    truths = {image.asset_id: image for image in truth}
    images = tuple(truths[s.asset.asset_id] for s in fixture.release.samples if s.split == "val")

    def digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    sources = tuple(
        SourcePin(
            s.asset.asset_id,
            s.group_id,
            s.asset.sha256,
            truths[s.asset.asset_id].truth_digest(),
            {"train": "curated", "val": "development", "test": "locked_final_test"}[s.split],
        )
        for s in fixture.release.samples
    )
    recipes = tuple(
        RecipePin(
            f"full-letterbox-{n}-v1",
            digest(json.dumps(value["recipe"], sort_keys=True, separators=(",", ":"))),
            InferencePolicy(n),
            digest("warm"),
            digest("base"),
            tuple(sorted({s.group_id for s in sources if s.role == "curated"})),
        )
        for n, value in variants.items()
    )
    manifest = ExperimentManifest(
        "input-controls",
        fixture.release.project_id,
        file_digest(dataset / "class-map.json"),
        tuple((c.stable_id, False) for c in fixture.release.ontology.classes),
        fixture.release.assignment.assignment_version,
        digest("base"),
        sources,
        recipes,
        tuple(
            DatasetPin(
                recipe.recipe_id,
                "curated-only",
                fixture.release.release_id,
                file_digest(tmp_path / f"compiled-{recipe.policy.imgsz}" / "manifest.json"),
                tuple(s.asset_id for s in sources if s.role == "curated"),
            )
            for recipe in recipes
        ),
        (BASELINE, AugmentationProfile("color-v1", hsv_v=0.2)),
        (0,),
        fixture_only=True,
    )
    backend = LocalUltralyticsBackend(
        dataset=dataset,
        initialization=tmp_path / "absent.pt",
        output=tmp_path / "unused-output",
        validation_images=images,
    )
    cell = next(cell for cell in manifest.cells() if cell.recipe_id == recipe_id)
    assert validate_inputs(manifest, cell, backend) == compiled
    assert not backend.output.exists()
    if change == "negative":
        negatives = [s for s in compiled["samples"] if s["split"] == "train"]
        assert negatives and all(s["verified_negative"] for s in negatives)
        assert all((dataset / s["label_path"]).read_bytes() == b"" for s in negatives)
        assert all(s["label_path"] in compiled["file_hashes"] for s in negatives)
    if change in {"none", "negative"}:
        return
    errors = {
        "manifest": "manifest digest",
        "bytes": "file digest",
        "recipe": "recipe configuration",
        "train": "compiled membership",
        "val": "compiled membership",
        "source": "source lineage",
        "truth": "source lineage",
        "group": "source lineage",
        "role": "final-test role",
        "evaluation": "evaluation truth",
        "classes": "class map",
    }
    if change == "manifest":
        (dataset / "manifest.json").write_text(json.dumps(compiled) + " ")
    elif change == "bytes":
        image = dataset / compiled["samples"][0]["image_path"]
        image.write_bytes(image.read_bytes() + b"changed")
    elif change == "evaluation":
        backend.validation_images = (
            replace(images[0], revision=images[0].revision + 1),
            *images[1:],
        )
    elif change == "classes":
        manifest = replace(manifest, class_map_sha256=digest("other classes"))
    else:
        if change.startswith(("label-", "duplicate-", "shared-", "pair-", "image-")):
            kind, split = change.rsplit("-", 1)
            sample = next(s for s in compiled["samples"] if s["split"] == split)
            if kind == "pair-swap":
                other = next(
                    s for s in compiled["samples"] if s["split"] == split and s is not sample
                )
                for key in ("image_path", "label_path"):
                    sample[key], other[key] = other[key], sample[key]
            elif kind == "image-extension":
                old = sample["image_path"]
                sample["image_path"] = str(Path(old).with_suffix(".bin"))
                (dataset / old).rename(dataset / sample["image_path"])
                compiled["file_hashes"][sample["image_path"]] = compiled["file_hashes"].pop(old)
            elif kind in {"image-digest", "label-digest"}:
                sample[kind.replace("-digest", "_sha256")] = digest("wrong derived digest")
            elif kind == "label-missing":
                label = dataset / sample["label_path"]
                assert label.read_bytes()
                label.unlink()
                del compiled["file_hashes"][sample["label_path"]]
            elif kind == "label-mismatch":
                sample["label_path"] = "labels/other.txt"
            elif kind == "label-extra":
                extra = f"labels/{split}/extra.txt"
                (dataset / extra).write_text("")
                compiled["file_hashes"][extra] = file_digest(dataset / extra)
            elif kind == "duplicate-source":
                duplicate = dict(sample)
                for key, folder, suffix in (
                    ("image_path", "images", ".jpg"),
                    ("label_path", "labels", ".txt"),
                ):
                    relative = f"{folder}/{split}/duplicate{suffix}"
                    (dataset / relative).write_bytes((dataset / sample[key]).read_bytes())
                    compiled["file_hashes"][relative] = file_digest(dataset / relative)
                    duplicate[key] = relative
                compiled["samples"].append(duplicate)
            elif kind == "duplicate-record":
                compiled["samples"].append(dict(sample))
            else:
                other = next(
                    s for s in compiled["samples"] if s["split"] == split and s is not sample
                )
                old = other["image_path"]
                (dataset / old).unlink()
                del compiled["file_hashes"][old]
                other["image_path"] = sample["image_path"]
            errors[change] = "compiled membership" if kind.startswith("duplicate-") else "loader"
        elif change in {
            "loader-train",
            "loader-val",
            "loader-extra-class",
            "loader-sparse-classes",
            "loader-nc",
            "loader-nc-bool",
            "loader-list-names",
        }:
            from ultralytics.utils import YAML

            data = YAML.load(dataset / "data.yaml")
            count = len(data["names"])
            if change in {"loader-train", "loader-val"}:
                data[change.removeprefix("loader-")] = (
                    "images/val" if change == "loader-train" else "images/train"
                )
            elif change == "loader-extra-class":
                data["names"][count] = "extra display name"
            elif change == "loader-sparse-classes":
                data["names"][count] = data["names"].pop(0)
            elif change in {"loader-nc", "loader-nc-bool"}:
                data["nc"] = count + 1 if change == "loader-nc" else True
            else:
                data["names"], data["nc"] = [f"display {n}" for n in range(count)], count
            YAML.save(dataset / "data.yaml", data)
            compiled["file_hashes"]["data.yaml"] = file_digest(dataset / "data.yaml")
        elif change == "loader-extra":
            sample = next(s for s in compiled["samples"] if s["split"] == "train")
            extra = dataset / "images/train/unlisted.png"
            extra.write_bytes((dataset / sample["image_path"]).read_bytes())
            compiled["file_hashes"]["images/train/unlisted.png"] = file_digest(extra)
        elif change == "loader-location":
            sample = next(s for s in compiled["samples"] if s["split"] == "train")
            sample["image_path"] = next(
                s["image_path"] for s in compiled["samples"] if s["split"] == "val"
            )
        elif change == "recipe":
            compiled["recipe"]["min_target_px"] = 99
        elif change in {"train", "val"}:
            compiled["samples"] = [s for s in compiled["samples"] if s["split"] != change]
        elif change == "role":
            sample = next(s for s in compiled["samples"] if s["split"] == "train")
            compiled["samples"].append({**sample, "split": "test"})
        else:
            key = {"source": "source_sha256", "truth": "source_truth_sha256", "group": "group_id"}[
                change
            ]
            compiled["samples"][0][key] = digest("different")
        (dataset / "manifest.json").write_text(json.dumps(compiled))
        manifest = replace(
            manifest,
            datasets=tuple(
                replace(pin, manifest_sha256=file_digest(dataset / "manifest.json"))
                if pin.recipe_id == recipe_id
                else pin
                for pin in manifest.datasets
            ),
        )
    if change == "loader-list-names":
        assert validate_inputs(manifest, cell, backend) == compiled
    else:
        with pytest.raises(
            ValueError, match="loader" if change.startswith("loader-") else errors[change]
        ):
            validate_inputs(manifest, cell, backend)
    assert not backend.output.exists()


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
    from labelloop_core.training import AugmentationProfile, InferencePolicy, TrainingRequest
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
    for safety in (
        (("unlisted-class", True),),
        tuple((class_id, True) for class_id in manifest["class_map"]) + (("unlisted-class", True),),
    ):
        with pytest.raises(ValueError, match="mirror safety must cover"):
            backend.train(
                replace(
                    request,
                    augmentation=AugmentationProfile("flip-v1", fliplr=0.5),
                    class_mirror_safety=safety,
                )
            )
        assert not output.exists()
    with pytest.raises(ValueError, match="differs from frozen ontology"):
        backend.train(
            replace(
                request,
                augmentation=AugmentationProfile("flip-v1", fliplr=0.5),
                class_mirror_safety=tuple((class_id, True) for class_id in manifest["class_map"]),
            )
        )
    assert not output.exists()
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
    exported = export_and_compare(
        weights=epoch_file,
        expected_weights_sha256=result.selected.sha256,
        images=tuple(
            dataset / sample["image_path"]
            for sample in manifest["samples"]
            if sample["split"] == "val"
        ),
        policy=request.policy,
        evidence=output / "export-equivalence.json",
    )
    assert exported.static_input_signature == (1, 3, side, side)
    assert exported.export_sha256 == file_digest(epoch_file.with_suffix(".onnx"))
    assert exported.sample_count == len(validation_images)
    assert all(0 < count < request.policy.max_det for count in exported.detection_counts)
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
