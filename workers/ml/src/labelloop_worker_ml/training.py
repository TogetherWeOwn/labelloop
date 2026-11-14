"""Controlled CPU training; vendor imports and all filesystem I/O stay in the worker."""

import hashlib
import importlib.util
import json
import shutil
from copy import copy, deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import torch
import ultralytics
from ultralytics import YOLO
from ultralytics.data.build import build_yolo_dataset
from ultralytics.models.yolo.detect import DetectionTrainer, DetectionValidator
from ultralytics.utils import YAML
from ultralytics.utils.torch_utils import unwrap_model

from labelloop_core.evaluation import (
    DerivedPredictions,
    Detection,
    EvaluationImage,
    EvaluationReport,
    EvaluationRunner,
)
from labelloop_core.geometry import AffineTransform, Box, Crop
from labelloop_core.training import (
    ULTRALYTICS_VERSION,
    Checkpoint,
    InferencePolicy,
    TrainingRequest,
    TrainingResult,
    argument_diff,
    select_checkpoint,
)


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_record(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def verify_dataset_files(dataset: Path, manifest: dict) -> None:
    """Validate bytes and exact membership at every consumer of compiled artifacts."""
    entries = tuple(dataset.rglob("*"))
    if any(path.is_symlink() for path in entries) or {
        path.relative_to(dataset).as_posix() for path in entries if path.is_file()
    } != set(manifest["file_hashes"]) | {"manifest.json"}:
        raise ValueError("compiled dataset membership differs from frozen manifest")
    for relative, expected in manifest["file_hashes"].items():
        path = (dataset / relative).resolve()
        if not path.is_relative_to(dataset) or file_digest(path) != expected:
            raise ValueError("compiled dataset file digest mismatch")


@dataclass(frozen=True)
class TargetRetention:
    sample: str
    before: int
    after: int
    before_at_or_below_6px: int
    after_at_or_below_6px: int


class AuditedTransforms:
    """Audit the transforms used by the real loader, not a parallel approximation."""

    def __init__(self, transforms: Any, records: list[TargetRetention]) -> None:
        self.transforms = transforms
        self.records = records

    def __call__(self, labels: dict) -> dict:
        sample = Path(labels["im_file"]).name
        instances = deepcopy(labels["instances"])
        instances.convert_bbox("xywh")
        height, width = labels["img"].shape[:2]
        instances.denormalize(width, height)
        before_sizes = instances.bboxes[:, 2:]
        before = len(labels["cls"])
        before_small = int((before_sizes.min(axis=1) <= 6).sum())
        result = self.transforms(labels)
        after_sizes = result["bboxes"][:, 2:] * result["img"].shape[-1]
        record = TargetRetention(
            sample,
            before,
            len(result["cls"]),
            before_small,
            int((after_sizes.min(dim=1).values <= 6).sum()),
        )
        self.records.append(record)
        if record.after != record.before:
            raise ValueError(
                f"dataloader dropped targets: {sample}: {record.before}->{record.after}"
            )
        return result


def _observe_shape(policy: InferencePolicy, shape: tuple, stride: int, records: list) -> None:
    policy.assert_shape(tuple(int(value) for value in shape), stride=stride)
    records.append(tuple(int(value) for value in shape))


def controlled_trainer(
    policy: InferencePolicy, retention: list[TargetRetention], shapes: dict[str, list]
) -> type[DetectionTrainer]:
    class FixedValidator(DetectionValidator):
        def preprocess(self, batch: dict) -> dict:
            result = super().preprocess(batch)
            _observe_shape(policy, result["img"].shape, int(self.stride), shapes["validation"])
            return result

    class FixedTrainer(DetectionTrainer):
        def build_dataset(self, img_path: str, mode: str = "train", batch: int | None = None):
            stride = max(int(unwrap_model(self.model).stride.max()), 32)
            dataset = build_yolo_dataset(
                self.args, img_path, batch, self.data, mode=mode, rect=False, stride=stride
            )
            if mode == "train":
                dataset.transforms = AuditedTransforms(dataset.transforms, retention)
            return dataset

        def preprocess_batch(self, batch: dict) -> dict:
            result = super().preprocess_batch(batch)
            _observe_shape(policy, result["img"].shape, int(self.stride), shapes["training"])
            return result

        def get_validator(self):
            validator = FixedValidator(
                self.test_loader,
                save_dir=self.save_dir,
                args=copy(self.args),
                _callbacks=self.callbacks,
            )
            # The base validator sets this when wrapping a standalone model; training does not.
            validator.stride = int(self.stride)
            return validator

    return FixedTrainer


def predict_samples(
    weights: Path,
    root: Path,
    samples: list[dict],
    class_ids: tuple[str, ...],
    policy: InferencePolicy,
    shapes: list,
    *,
    confidence: float | None = None,
) -> tuple[DerivedPredictions, ...]:
    threshold = min(0.001, policy.conf) if confidence is None else confidence
    if not 0 <= threshold <= policy.conf:
        raise ValueError("candidate threshold must not exceed the deployment threshold")
    model = YOLO(str(weights))
    stride = int(model.model.stride.max())

    def assert_input(_module: Any, args: tuple) -> None:
        _observe_shape(policy, args[0].shape, stride, shapes)

    hook = model.model.register_forward_pre_hook(assert_input)
    predictions = []
    try:
        for sample in samples:
            results = model.predict(
                source=str(root / sample["image_path"]),
                **(policy.arguments() | {"conf": threshold}),
                device="cpu",
                batch=1,
                verbose=False,
                save=False,
            )
            detections = []
            for row in results[0].boxes.data.cpu().tolist():
                x1, y1, x2, y2, confidence, index = row
                if x2 > x1 and y2 > y1:
                    detections.append(
                        Detection(class_ids[int(index)], Box(x1, y1, x2, y2), confidence)
                    )
            transform = dict(sample["transform"])
            transform.pop("valid_content_rectangle")
            transform["crop"] = Crop(**transform["crop"])
            transform["forward"] = tuple(tuple(row) for row in transform["forward"])
            transform["inverse"] = tuple(tuple(row) for row in transform["inverse"])
            predictions.append(
                DerivedPredictions(
                    sample["asset_id"], AffineTransform(**transform), tuple(detections)
                )
            )
    finally:
        hook.remove()
    return tuple(predictions)


class LocalUltralyticsBackend:
    """Worker-owned paths and frozen source-frame development truth are injected by code."""

    def __init__(
        self,
        *,
        dataset: Path,
        initialization: Path,
        output: Path,
        validation_images: tuple[EvaluationImage, ...],
    ) -> None:
        self.dataset = dataset.resolve()
        self.initialization = initialization.resolve()
        self.output = output.resolve()
        self.validation_images = validation_images

    def train(self, request: TrainingRequest) -> TrainingResult:
        if ultralytics.__version__ != ULTRALYTICS_VERSION:
            raise ValueError("unpinned Ultralytics runtime")
        if importlib.util.find_spec("albumentations") is not None:
            raise ValueError("baseline forbids implicit Albumentations transforms")
        if (
            not self.initialization.is_file()
            or file_digest(self.initialization) != request.initialization_sha256
        ):
            raise ValueError("initialization checkpoint digest mismatch")
        manifest_path = self.dataset / "manifest.json"
        if file_digest(manifest_path) != request.dataset_manifest_sha256:
            raise ValueError("dataset manifest digest mismatch")
        manifest = json.loads(manifest_path.read_text())
        if file_digest(self.dataset / "class-map.json") != request.class_map_sha256:
            raise ValueError("class map digest mismatch")
        if (
            manifest["release_id"] != request.dataset_release_id
            or manifest["recipe"]["recipe_id"] != request.recipe_id
            or manifest["recipe"]["output_size"] != request.policy.imgsz
            or manifest["split_assignment_version"] != request.split_assignment_version
        ):
            raise ValueError("dataset lineage does not match training request")
        class_map = json.loads((self.dataset / "class-map.json").read_text())
        if class_map != manifest["class_map"] or sorted(class_map.values()) != list(
            range(len(class_map))
        ):
            raise ValueError("class mapping must be contiguous and match the manifest")
        class_ids = tuple(sorted(class_map, key=class_map.__getitem__))
        if request.augmentation.fliplr and set(dict(request.class_mirror_safety)) != set(class_ids):
            raise ValueError("mirror safety must cover the complete dataset class catalog")
        verify_dataset_files(self.dataset, manifest)
        samples = [sample for sample in manifest["samples"] if sample["split"] == "val"]
        validation_ids = {image.asset_id for image in self.validation_images}
        if (
            not samples
            or not validation_ids
            or len(validation_ids) != len(self.validation_images)
            or {sample["asset_id"] for sample in samples} != validation_ids
        ):
            raise ValueError("development validation truth must match every validation source")
        truth_digests = {image.asset_id: image.truth_digest() for image in self.validation_images}
        if any(
            truth_digests[sample["asset_id"]] != sample.get("source_truth_sha256")
            for sample in samples
        ):
            raise ValueError("development validation truth differs from frozen canonical truth")
        if any(
            sample["asset_id"] in validation_ids and sample["split"] != "val"
            for sample in manifest["samples"]
        ):
            raise ValueError("development evaluation cannot include training or test sources")
        self.output.mkdir(parents=True, exist_ok=False)
        # Ultralytics creates label caches: only its disposable run copy may be mutated.
        data_root = self.output / "dataset"
        # Copy only the frozen allowlist, then hash the actual copies before the loader sees them.
        for relative, expected in manifest["file_hashes"].items():
            destination = data_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.dataset / relative, destination)
            if file_digest(destination) != expected:
                raise ValueError("compiled dataset changed while copying frozen inputs")
        data = YAML.load(data_root / "data.yaml")
        data.pop("test", None)
        data["path"] = str(data_root)
        YAML.save(data_root / "data.yaml", data)
        retention: list[TargetRetention] = []
        shapes: dict[str, list] = {"training": [], "validation": [], "inference": []}
        checkpoints: list[Checkpoint] = []
        resolved: dict[str, Any] = {}
        requested = request.arguments() | {
            "data": str(data_root / "data.yaml"),
            "model": str(self.initialization),
            "project": str(self.output),
            "name": "trainer",
            "exist_ok": False,
        }
        write_record(self.output / "request.json", asdict(request))
        write_record(
            self.output / "evaluation-inference-arguments.json",
            {
                "arguments": request.policy.arguments() | {"conf": min(0.001, request.policy.conf)},
                "operating_conf": request.policy.conf,
                "reason": "AP uses low-threshold ranking; precision/recall use operating_conf",
            },
        )

        def audit_arguments(trainer: Any) -> None:
            resolved.update(vars(trainer.args))
            write_record(self.output / "resolved-arguments.json", resolved)
            if argument_diff(requested, resolved):
                raise ValueError("args_mismatch")

        def evaluate_epoch(trainer: Any) -> None:
            path = trainer.wdir / f"epoch{trainer.epoch}.pt"
            weights_sha256 = file_digest(path)
            derived = predict_samples(
                path, data_root, samples, class_ids, request.policy, shapes["inference"]
            )
            report: EvaluationReport = EvaluationRunner(class_ids, request.policy).evaluate(
                images=self.validation_images,
                derived=derived,
                run_id=f"{request.run_id}-epoch-{trainer.epoch}",
                dataset_release_id=request.dataset_release_id,
                recipe_id=request.recipe_id,
                weights_sha256=weights_sha256,
                class_map_sha256=request.class_map_sha256,
                role=request.validation_role,
            )
            if report.overall.map50_95 is None:
                raise ValueError("fixed-shape checkpoint selection requires supported ground truth")
            checkpoints.append(
                Checkpoint(trainer.epoch, weights_sha256, report.overall.map50_95, report.run_id)
            )
            write_record(self.output / f"evaluation-epoch-{trainer.epoch}.json", asdict(report))

        model = YOLO(str(self.initialization))
        model.add_callback("on_pretrain_routine_end", audit_arguments)
        model.add_callback("on_model_save", evaluate_epoch)
        try:
            model.train(trainer=controlled_trainer(request.policy, retention, shapes), **requested)
            if not all(shapes.values()) or not retention or len(checkpoints) != request.epochs:
                raise ValueError("incomplete training, shape or dataloader evidence")
            result = TrainingResult(
                request,
                tuple(checkpoints),
                select_checkpoint(tuple(checkpoints)),
                resolved,
                ultralytics.__version__,
                str(torch.__version__),
                False,
            )
            # Vendor best.pt is diagnostic; the selected epoch file remains immutable.
            selected_path = self.output / "trainer" / "weights" / f"epoch{result.selected.epoch}.pt"
            if file_digest(selected_path) != result.selected.sha256:
                raise ValueError("selected checkpoint changed after evaluation")
            write_record(self.output / "result.json", asdict(result))
            return result
        finally:
            write_record(
                self.output / "target-retention.json", [asdict(item) for item in retention]
            )
            write_record(self.output / "tensor-shapes.json", shapes)
