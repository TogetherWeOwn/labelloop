"""Static float32 exports checked against native predictions on identical actual tensors."""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import onnx
import onnxruntime as ort
import ultralytics
from ultralytics import YOLO

from labelloop_core.training import (
    ULTRALYTICS_VERSION,
    InferencePolicy,
    argument_diff,
)
from labelloop_worker_ml.training import file_digest, write_record


@dataclass(frozen=True)
class ExportResult:
    weights_sha256: str
    export_sha256: str
    static_input_signature: tuple[int, int, int, int]
    policy: InferencePolicy
    sample_count: int
    detection_counts: tuple[int, ...]
    coordinate_atol_px: float
    confidence_atol: float
    onnx_version: str
    onnxruntime_version: str


def compare_detections(native: np.ndarray, exported: np.ndarray) -> None:
    """Ignore ordering, but require a one-to-one match, including every class and score."""
    if native.shape != exported.shape or native.ndim != 2 or native.shape[1] != 6:
        raise ValueError("native/exported detection count or layout mismatch")
    if not np.isfinite(native).all() or not np.isfinite(exported).all():
        raise ValueError("nonfinite native/exported detections")
    remaining = list(range(len(exported)))
    for row in native:
        matches = [
            index
            for index in remaining
            if row[5] == exported[index, 5]
            and np.allclose(row[:4], exported[index, :4], rtol=0, atol=0.01)
            and abs(float(row[4]) - float(exported[index, 4])) <= 1e-5
        ]
        if not matches:
            raise ValueError("native/exported detections exceed declared tolerance")
        remaining.remove(matches[0])


def export_and_compare(
    *,
    weights: Path,
    expected_weights_sha256: str,
    images: tuple[Path, ...],
    policy: InferencePolicy,
    evidence: Path,
) -> ExportResult:
    if ultralytics.__version__ != ULTRALYTICS_VERSION:
        raise ValueError("unpinned Ultralytics runtime")
    if file_digest(weights) != expected_weights_sha256:
        raise ValueError("export checkpoint digest mismatch")
    if not images:
        raise ValueError("export equivalence needs real recipe samples")
    if evidence.exists() or weights.with_suffix(".onnx").exists():
        raise ValueError("export artifacts and evidence must not overwrite an existing run")
    requested = policy.arguments() | {
        "format": "onnx",
        "dynamic": False,
        "batch": 1,
        "device": "cpu",
        "quantize": 32,
        "simplify": False,
        "opset": 17,
    }
    resolved: dict[str, Any] = {}

    def audit_export(exporter: Any) -> None:
        resolved.update(vars(exporter.args))
        normalized = resolved | {"imgsz": policy.imgsz}
        if tuple(exporter.im.shape) != (1, 3, policy.imgsz, policy.imgsz):
            raise ValueError("actual export tensor shape violates the recipe")
        if resolved["imgsz"] != [policy.imgsz, policy.imgsz] or argument_diff(
            requested, normalized
        ):
            raise ValueError("export args_mismatch")

    model = YOLO(str(weights))
    model.add_callback("on_export_end", audit_export)
    artifact = Path(model.export(**requested))
    if not resolved:
        raise ValueError("missing resolved export arguments")
    graph = onnx.load(artifact, load_external_data=False)
    onnx.checker.check_model(graph)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(artifact), sess_options=options, providers=["CPUExecutionProvider"]
    )
    inputs = session.get_inputs()
    signature = (1, 3, policy.imgsz, policy.imgsz)
    if (
        len(inputs) != 1
        or tuple(inputs[0].shape) != signature
        or inputs[0].type != "tensor(float)"
        or len(session.get_outputs()) != 1
    ):
        raise ValueError("export runtime signature is not static float32 NCHW")
    native_model = YOLO(str(weights))
    stride = int(native_model.model.stride.max())
    observed: list[np.ndarray] = []

    def observe(_module: Any, args: tuple) -> None:
        policy.assert_shape(tuple(args[0].shape), stride=stride)
        observed.append(args[0].detach().cpu().numpy().copy())

    hook = native_model.model.register_forward_pre_hook(observe)
    counts = []
    try:
        for image in images:
            observed.clear()
            native = native_model.predict(
                source=str(image),
                **policy.arguments(),
                device="cpu",
                batch=1,
                verbose=False,
                save=False,
            )[0]
            if not observed or tuple(observed[-1].shape) != signature:
                raise ValueError("native inference did not expose its actual recipe tensor")
            if native.orig_shape != (policy.imgsz, policy.imgsz):
                raise ValueError("export comparison requires precompiled recipe images")
            outputs = session.run(None, {inputs[0].name: observed[-1]})[0]
            if outputs.shape != (1, policy.max_det, 6) or not np.isfinite(outputs).all():
                raise ValueError("unexpected exported detection layout or nonfinite output")
            exported = outputs[0]
            # Embedded external NMS zero-pads; end2end outputs need their operating threshold.
            exported = exported[exported[:, 4] > policy.conf].copy()
            exported[:, :4] = exported[:, :4].clip(0, policy.imgsz)
            rows = native.boxes.data.cpu().numpy()
            compare_detections(rows, exported)
            counts.append(len(rows))
    finally:
        hook.remove()
    if file_digest(weights) != expected_weights_sha256:
        raise ValueError("checkpoint changed during export verification")
    result = ExportResult(
        expected_weights_sha256,
        file_digest(artifact),
        signature,
        policy,
        len(images),
        tuple(counts),
        0.01,
        1e-5,
        onnx.__version__,
        ort.__version__,
    )
    write_record(evidence, {"result": asdict(result), "resolved_arguments": resolved})
    return result
