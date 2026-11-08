"""Actual offline controlled inference tensor evidence; Actions CPU environment only."""

import builtins
import hashlib
import io
import json
import os
from dataclasses import asdict, replace

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LABELLOOP_ML_SMOKE") != "1", reason="requires Actions CPU ML environment"
)


@pytest.mark.parametrize("side", [320, 640])
def test_controlled_proposals_keep_tensor_checkpoint_and_source_lineage(
    tmp_path, monkeypatch, side
):
    original_import = builtins.__import__

    def isolated_import(name, *args, **kwargs):
        if name == "labelloop_compiler" or name.startswith("labelloop_compiler."):
            raise ModuleNotFoundError("compiler is not installed in the ML image")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", isolated_import)
    import torch
    from PIL import Image, ImageDraw, features
    from ultralytics import YOLO

    from labelloop_core.domain import Recipe, SourceAsset
    from labelloop_core.registry import ModelRelease, RegistryState
    from labelloop_core.training import ULTRALYTICS_VERSION, InferencePolicy
    from labelloop_worker_ml.proposals import (
        ControlledPredictor,
        DiscoveryResult,
        ProposalProvider,
        ProposalRequest,
        record_digest,
    )
    from labelloop_worker_ml.training import file_digest

    torch.set_num_threads(2)
    torch.manual_seed(0)
    weights = tmp_path / "fixture.pt"
    # Random weights exercise shape and lineage, not a model-qualification assertion.
    YOLO("yolo26n.yaml").save(weights)
    image = Image.new("RGB", (701, 503), (15, 25, 35))
    ImageDraw.Draw(image).rectangle((20, 30, 130, 170), fill=(200, 10, 100))
    output = io.BytesIO()
    image.save(output, format="PNG")
    original = output.getvalue()
    sha256 = hashlib.sha256(original).hexdigest()
    source = SourceAsset("fixture", "project", sha256, f"sha256/{sha256[:2]}/{sha256}", 701, 503)
    recipe = Recipe(f"full-letterbox-{side}-v1", side, "auto-area-linear")
    class_ids = tuple(f"fixture-{i}" for i in range(80))
    champion = ModelRelease(
        "fixture-only",
        "project",
        recipe.recipe_id,
        file_digest(weights),
        record_digest(asdict(recipe)),
        "a" * 64,
        class_ids,
        InferencePolicy(side, conf=0.25),
        ULTRALYTICS_VERSION,
        str(torch.__version__),
        "b" * 64,
        "onnx",
        (1, 3, side, side),
        ("fixture-eval",),
    )
    request = ProposalRequest(
        "run", source, 0, "c" * 64, "fixture scope", "d" * 64, recipe, class_ids
    )
    registry = RegistryState("project", recipe.recipe_id, 1, champion)
    events = []

    class Blind:
        def discover(self, source, original, scope):
            events.append("discovery")
            return DiscoveryResult("recorded-blind-empty-v1")

    def checkpoint(sha256):
        assert events == ["discovery"]
        assert sha256 == champion.weights_sha256
        return weights

    provider = ProposalProvider(ControlledPredictor(checkpoint))
    discovery = provider.discover(request, original, Blind())
    record = provider.propose(request, original, discovery, registry, ())
    assert events == ["discovery"]
    assert record["tensor_shapes"]
    assert all(shape[1:] == (3, side, side) for shape in record["tensor_shapes"])
    assert record["checkpoint_sha256"] == file_digest(weights)
    assert record["teacher"]["recipe_id"] == recipe.recipe_id
    assert record["binding"]["source"]["sha256"] == sha256
    assert record["scope_completeness"] == "unreviewed"
    assert all(candidate["class_id"] is None for candidate in record["teacher_candidates"])
    evidence_path = tmp_path / "proposal-evidence.json"
    evidence_path.write_text(json.dumps(record, sort_keys=True, indent=2))
    persisted = json.loads(evidence_path.read_text())
    toolchain = {
        "pillow": Image.__version__,
        "libjpeg_turbo": features.version("libjpeg_turbo") or "unknown",
        "jpeg_compatibility": features.version_codec("jpg") or "unknown",
    }
    for name in ("pillow", "libjpeg_turbo", "jpeg_compatibility"):
        assert persisted["runtime"][name] == toolchain[name]
        assert persisted["runtime"][name] != "unknown"
    bad_champion = replace(champion, weights_sha256="e" * 64)
    bad_provider = ProposalProvider(ControlledPredictor(lambda sha256: weights))
    with pytest.raises(ValueError, match="checkpoint digest"):
        bad_provider.propose(
            request, original, discovery, replace(registry, champion=bad_champion), ()
        )
