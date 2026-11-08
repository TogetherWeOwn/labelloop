"""Offline proposals. Discovery is blind to teacher answers; neither establishes completeness."""

import hashlib
import io
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from labelloop_core.domain import Recipe, ReferenceExample, SourceAsset
from labelloop_core.evaluation import DerivedPredictions, Detection, merge_detections
from labelloop_core.geometry import Box, full_frame_transform
from labelloop_core.registry import ModelRelease, RegistryState
from labelloop_core.training import require_digest


def record_digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ProposalRequest:
    run_id: str
    source: SourceAsset
    expected_revision: int
    ontology_sha256: str
    scope: str
    reference_pack_sha256: str
    recipe: Recipe
    class_ids: tuple[str, ...]
    candidate_conf: float = 0.001

    def __post_init__(self) -> None:
        for sha256 in (self.ontology_sha256, self.reference_pack_sha256):
            require_digest(sha256)
        if not self.run_id or not self.scope or self.expected_revision < 0:
            raise ValueError("proposals require pinned run, scope and expected revision")
        if not self.class_ids or len(set(self.class_ids)) != len(self.class_ids):
            raise ValueError("proposal ontology must have unique stable classes")
        if not math.isfinite(self.candidate_conf) or not 0 <= self.candidate_conf <= 1:
            raise ValueError("candidate threshold must be a finite probability")
        if self.recipe.output_size not in (320, 640):
            raise ValueError("proposal recipes require 320 or 640")

    def binding(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryResult:
    provider_version: str
    boxes: tuple[Box, ...] = ()
    full_image: bool = True
    partial: bool = False
    truncated: bool = False

    def record(self, request: ProposalRequest) -> dict:
        if not self.provider_version:
            raise ValueError("discovery provider version is required")
        for box in self.boxes:
            box.within(request.source.width, request.source.height)
        return {"binding": request.binding(), **asdict(self)}


class DiscoveryProvider(Protocol):
    # No teacher model, boxes, scores or answers can enter this call.
    def discover(self, source: SourceAsset, original: bytes, scope: str) -> DiscoveryResult: ...


@dataclass(frozen=True)
class PredictionBatch:
    derived: tuple[DerivedPredictions, ...]
    tensor_shapes: tuple[tuple[int, ...], ...]
    runtime: tuple[tuple[str, str], ...]


class Predictor(Protocol):
    def predict(
        self, request: ProposalRequest, original: bytes, champion: ModelRelease
    ) -> PredictionBatch: ...


class ProposalProvider:
    def __init__(self, predictor: Predictor) -> None:
        self.predictor = predictor

    @staticmethod
    def _source(request: ProposalRequest, original: bytes) -> None:
        if (
            len(original) > 40 * 1024 * 1024
            or request.source.width * request.source.height > 100_000_000
        ):
            raise ValueError("source exceeds ingest resource bounds")
        if hashlib.sha256(original).hexdigest() != request.source.sha256:
            raise ValueError("original image digest mismatch")

    def discover(
        self, request: ProposalRequest, original: bytes, provider: DiscoveryProvider
    ) -> dict:
        self._source(request, original)
        return provider.discover(request.source, original, request.scope).record(request)

    def propose(
        self,
        request: ProposalRequest,
        original: bytes,
        discovery: dict,
        registry: RegistryState,
        references: tuple[ReferenceExample, ...],
    ) -> dict:
        self._source(request, original)
        # Saved discovery must pin this exact operation, not another source/revision/cache key.
        if discovery.get("binding") != json.loads(json.dumps(request.binding())):
            # asdict preserves tuples in-process; JSON storage normalizes them to lists.
            if discovery.get("binding") != request.binding():
                raise ValueError("discovery artifact belongs to different pinned inputs")
        discovery_result = DiscoveryResult(
            discovery["provider_version"],
            tuple(Box(**box) for box in discovery["boxes"]),
            discovery["full_image"],
            discovery["partial"],
            discovery["truncated"],
        )
        discovery_result.record(request)
        if (registry.project_id, registry.recipe_id) != (
            request.source.project_id,
            request.recipe.recipe_id,
        ):
            raise ValueError("registry cannot cross project or recipe boundaries")
        if len({ref.reference_id for ref in references}) != len(references):
            raise ValueError("duplicate evidence reference identity")
        if any(
            not ref.approved
            or ref.validity_scope != request.scope
            or ref.class_id not in request.class_ids
            for ref in references
        ):
            raise ValueError("evidence must be approved for the exact scope and ontology")
        evidence_ids = tuple(sorted(ref.reference_id for ref in references))
        supported_classes = {ref.class_id for ref in references}
        champion = registry.champion
        candidates: list[dict] = []
        shapes: tuple[tuple[int, ...], ...] = ()
        runtime: tuple[tuple[str, str], ...] = ()
        parameters = None
        teacher_truncated = False
        if champion:
            if champion.class_ids != request.class_ids:
                raise ValueError("teacher class mapping differs from pinned ontology")
            if champion.preprocessing_sha256 != record_digest(asdict(request.recipe)):
                raise ValueError("teacher preprocessing differs from pinned recipe")
            if champion.policy.imgsz != request.recipe.output_size:
                raise ValueError("teacher tensor size differs from recipe")
            if champion.tiling_mode != "off":
                raise ValueError("v1 proposals support full-frame recipes only")
            if request.candidate_conf > champion.policy.conf:
                raise ValueError("candidate threshold exceeds deployment threshold")
            batch = self.predictor.predict(request, original, champion)
            shapes, runtime = batch.tensor_shapes, batch.runtime
            teacher_truncated = any(
                len(item.detections) >= champion.policy.max_det for item in batch.derived
            )
            if not shapes or not batch.derived:
                raise ValueError("inference requires tensor and coverage evidence")
            for shape in shapes:
                champion.policy.assert_shape(shape, stride=1)
            expected_transform = full_frame_transform(
                request.source.width,
                request.source.height,
                request.recipe.output_size,
                allow_upscale=request.recipe.allow_upscale,
            )
            if len(batch.derived) != 1:
                raise ValueError("full-frame proposal must return one source result")
            mapped: list[Detection] = []
            for item in batch.derived:
                if item.asset_id != request.source.asset_id or item.transform != expected_transform:
                    raise ValueError("prediction source transform differs from the pinned recipe")
                for detection in item.detections:
                    if detection.class_id not in champion.class_ids:
                        raise ValueError("teacher invented a class")
                    box = item.transform.restore(detection.box)
                    if box is not None and detection.score >= request.candidate_conf:
                        mapped.append(Detection(detection.class_id, box, detection.score))
            for index, detection in enumerate(merge_detections(tuple(mapped), champion.policy)):
                identified = detection.class_id in supported_classes
                candidates.append(
                    {
                        "object_id": f"{request.run_id}:teacher:{index}",
                        "class_id": detection.class_id if identified else None,
                        "label_state": "identified" if identified else "insufficient_evidence",
                        "box": asdict(detection.box),
                        "score": detection.score,
                        "evidence_ids": [
                            ref.reference_id
                            for ref in references
                            if ref.class_id == detection.class_id
                        ],
                    }
                )
            parameters = champion.policy.arguments() | {"conf": request.candidate_conf}
        # Discovery detections remain explicitly uncertain, separate from teacher predictions.
        blind = [
            {
                "object_id": f"{request.run_id}:discovery:{index}",
                "class_id": None,
                "label_state": "unknown",
                "box": asdict(box),
                "score": None,
                "evidence_ids": [],
            }
            for index, box in enumerate(discovery_result.boxes)
        ]
        return {
            "run_id": request.run_id,
            "project_id": request.source.project_id,
            "binding": request.binding(),
            "registry_revision": registry.revision,
            "teacher": asdict(champion) if champion else None,
            "checkpoint_sha256": champion.weights_sha256 if champion else None,
            "provider_version": "controlled-m2-proposals-v1",
            "runtime": dict(runtime),
            "parameters": parameters,
            "deployment_conf": champion.policy.conf if champion else None,
            "evidence_ids": evidence_ids,
            "discovery": discovery,
            "teacher_candidates": candidates,
            "discovery_candidates": blind,
            "tensor_shapes": shapes,
            "supported": champion is not None and set(request.class_ids) <= supported_classes,
            "unsupported_class_ids": sorted(set(request.class_ids) - supported_classes),
            "output_partial": discovery_result.partial or not discovery_result.full_image,
            "output_truncated": discovery_result.truncated or teacher_truncated,
            "approval_state": "proposed",
            "review_tier": "unreviewed",
            "scope_completeness": "unreviewed",
            "training_eligible": False,
            "gold_eligible": False,
        }


class ControlledPredictor:
    """Code injects checkpoint lookup; weights never come from a model-selected path/URL."""

    def __init__(self, checkpoint_path: Callable[[str], Path]) -> None:
        self.checkpoint_path = checkpoint_path

    def predict(
        self, request: ProposalRequest, original: bytes, champion: ModelRelease
    ) -> PredictionBatch:
        import cv2
        import numpy as np
        import torch
        import ultralytics
        from PIL import Image

        from labelloop_worker_ml.training import file_digest, predict_samples

        weights = self.checkpoint_path(champion.weights_sha256)
        if not weights.is_file() or file_digest(weights) != champion.weights_sha256:
            raise ValueError("teacher checkpoint digest mismatch")
        if (ultralytics.__version__, str(torch.__version__)) != (
            champion.ultralytics_version,
            champion.torch_version,
        ):
            raise ValueError("teacher runtime differs from pinned registry release")
        if hashlib.sha256(original).hexdigest() != request.source.sha256:
            raise ValueError("source pixels changed")
        with Image.open(io.BytesIO(original)) as opened:
            opened.load()
            if opened.mode != "RGB" or opened.size != (request.source.width, request.source.height):
                raise ValueError("source must be normalized RGB with recorded dimensions")
            source = np.asarray(opened)
        transform = full_frame_transform(
            request.source.width,
            request.source.height,
            request.recipe.output_size,
            allow_upscale=request.recipe.allow_upscale,
        )
        modes = {
            "area": cv2.INTER_AREA,
            "linear": cv2.INTER_LINEAR,
            "nearest": cv2.INTER_NEAREST,
            "cubic": cv2.INTER_CUBIC,
        }
        interpolation = request.recipe.interpolation
        if interpolation == "auto-area-linear":
            interpolation = "area" if transform.scale_x < 1 else "linear"
        if interpolation not in modes:
            raise ValueError("unsupported pinned interpolation")
        resized = cv2.resize(
            source,
            (transform.resized_width, transform.resized_height),
            interpolation=modes[interpolation],
        )
        canvas = Image.new("RGB", (request.recipe.output_size,) * 2, request.recipe.padding_color)
        canvas.paste(Image.fromarray(resized), (transform.left, transform.top))
        shapes: list = []
        with TemporaryDirectory(prefix="labelloop-proposal-") as temporary:
            root = Path(temporary)
            # Match the compiler encoder; inference sees the same recipe raster as training.
            filename = "input." + request.recipe.encoder.lower()
            canvas.save(
                root / filename, format=request.recipe.encoder, **request.recipe.encoder_options
            )
            sample = {
                "asset_id": request.source.asset_id,
                "image_path": filename,
                "transform": asdict(transform)
                | {"valid_content_rectangle": asdict(transform.valid_content)},
            }
            predictions = predict_samples(
                weights,
                root,
                [sample],
                champion.class_ids,
                champion.policy,
                shapes,
                confidence=request.candidate_conf,
            )
        return PredictionBatch(
            predictions,
            tuple(shapes),
            (
                ("ultralytics", ultralytics.__version__),
                ("torch", str(torch.__version__)),
                ("opencv", cv2.__version__),
                ("pillow", Image.__version__),
            ),
        )
