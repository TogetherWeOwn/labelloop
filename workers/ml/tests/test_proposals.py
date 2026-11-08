"""Offline proposal ports run without loading Torch or making hosted calls."""

import hashlib
import json
from dataclasses import asdict, replace

import pytest

from labelloop_core.domain import Recipe, ReferenceExample, SourceAsset
from labelloop_core.evaluation import DerivedPredictions, Detection
from labelloop_core.geometry import Box, full_frame_transform
from labelloop_core.registry import ModelRelease, RegistryState
from labelloop_core.training import ULTRALYTICS_VERSION, InferencePolicy
from labelloop_worker_ml.proposals import (
    DiscoveryResult,
    PredictionBatch,
    ProposalProvider,
    ProposalRequest,
    record_digest,
)


def inputs(side=320):
    original = b"licence-clean source stand-in; real raster tested in ML smoke"
    sha256 = hashlib.sha256(original).hexdigest()
    source = SourceAsset("asset", "project", sha256, f"sha256/{sha256[:2]}/{sha256}", 1001, 701)
    recipe = Recipe(f"full-{side}", side, "auto-area-linear")
    request = ProposalRequest("run", source, 3, "a" * 64, "scope", "b" * 64, recipe, ("sku",))
    model = ModelRelease(
        "champion",
        "project",
        recipe.recipe_id,
        "c" * 64,
        record_digest(asdict(recipe)),
        "d" * 64,
        ("sku",),
        InferencePolicy(side),
        ULTRALYTICS_VERSION,
        "torch-fixture",
        "e" * 64,
        "onnx",
        (1, 3, side, side),
        ("evaluation",),
    )
    registry = RegistryState("project", recipe.recipe_id, 2, model)
    evidence = (ReferenceExample("ref", "ref-asset", "sku", "fixture", True, "scope"),)
    return original, request, registry, evidence


class BlindDiscovery:
    def __init__(self, events, **flags):
        self.events, self.flags = events, flags

    def discover(self, source, original, scope):
        self.events.append("discovery")
        assert source.asset_id == "asset" and scope == "scope" and original
        return DiscoveryResult("blind-fixture-v1", (Box(1, 2, 30, 40),), **self.flags)


class FakePredictor:
    def __init__(self, events, *, empty=False):
        self.events, self.empty = events, empty

    def predict(self, request, original, champion):
        assert self.events == ["discovery"]
        self.events.append("teacher")
        transform = full_frame_transform(
            request.source.width, request.source.height, request.recipe.output_size
        )
        box = transform.apply(Box(100, 200, 150, 250))
        return PredictionBatch(
            (
                DerivedPredictions(
                    request.source.asset_id,
                    transform,
                    () if self.empty else (Detection("sku", box, 0.02),),
                ),
            ),
            ((1, 3, champion.policy.imgsz, champion.policy.imgsz),),
            (("fixture", "recorded-v1"),),
        )


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("empty", [False, True])
def test_discovery_first_zero_detections_and_pinned_lineage(side, empty):
    original, request, registry, evidence = inputs(side)
    events = []
    provider = ProposalProvider(FakePredictor(events, empty=empty))
    discovery = provider.discover(request, original, BlindDiscovery(events))
    # Exercise persisted JSON normalization on the resume path.
    discovery = json.loads(json.dumps(discovery))
    result = provider.propose(request, original, discovery, registry, evidence)
    assert events == ["discovery", "teacher"]
    assert result["checkpoint_sha256"] == registry.champion.weights_sha256
    assert result["binding"]["expected_revision"] == 3
    assert result["parameters"]["conf"] == 0.001
    assert result["deployment_conf"] == 0.25
    assert result["parameters"]["rect"] is False
    assert result["parameters"]["tile"] is False
    assert result["evidence_ids"] == ("ref",)
    assert result["tensor_shapes"] == ((1, 3, side, side),)
    assert result["approval_state"] == "proposed"
    assert result["review_tier"] == result["scope_completeness"] == "unreviewed"
    assert result["gold_eligible"] is result["training_eligible"] is False
    assert len(result["discovery_candidates"]) == 1
    assert result["discovery_candidates"][0]["class_id"] is None
    if empty:
        assert result["teacher_candidates"] == []
    else:
        candidate = result["teacher_candidates"][0]
        assert candidate["class_id"] == "sku"
        assert candidate["box"] == pytest.approx(asdict(Box(100, 200, 150, 250)))
        assert candidate["score"] < result["deployment_conf"]


@pytest.mark.parametrize("flags", [{"partial": True}, {"truncated": True}, {"full_image": False}])
def test_partial_or_truncated_discovery_never_proves_completeness(flags):
    original, request, registry, evidence = inputs()
    events = []
    provider = ProposalProvider(FakePredictor(events))
    discovery = provider.discover(request, original, BlindDiscovery(events, **flags))
    result = provider.propose(request, original, discovery, registry, evidence)
    assert result["scope_completeness"] == "unreviewed"
    assert result["output_partial"] or result["output_truncated"]


def test_no_champion_is_unsupported_and_discovery_still_runs():
    original, request, registry, evidence = inputs()
    events = []
    provider = ProposalProvider(FakePredictor(events))
    discovery = provider.discover(request, original, BlindDiscovery(events))
    result = provider.propose(
        request, original, discovery, replace(registry, champion=None), evidence
    )
    assert events == ["discovery"]
    assert result["supported"] is False
    assert result["checkpoint_sha256"] is None
    assert result["teacher_candidates"] == []
    assert result["discovery_candidates"][0]["label_state"] == "unknown"


def test_no_fine_grained_evidence_keeps_class_uncertain():
    original, request, registry, _ = inputs()
    events = []
    provider = ProposalProvider(FakePredictor(events))
    discovery = provider.discover(request, original, BlindDiscovery(events))
    result = provider.propose(request, original, discovery, registry, ())
    assert result["teacher_candidates"][0]["class_id"] is None
    assert result["teacher_candidates"][0]["label_state"] == "insufficient_evidence"


@pytest.mark.parametrize(
    "change",
    [
        {"expected_revision": 4},
        {"ontology_sha256": "f" * 64},
        {"reference_pack_sha256": "e" * 64},
        {"scope": "different"},
        {"candidate_conf": 0.002},
    ],
)
def test_discovery_cache_is_bound_to_all_inputs(change):
    original, request, registry, evidence = inputs()
    events = []
    provider = ProposalProvider(FakePredictor(events))
    discovery = provider.discover(request, original, BlindDiscovery(events))
    with pytest.raises(ValueError, match="different pinned"):
        provider.propose(replace(request, **change), original, discovery, registry, evidence)
    assert events == ["discovery"]


@pytest.mark.parametrize("bad", ["source", "recipe", "classes", "project", "threshold", "evidence"])
def test_rejects_untrusted_lineage_before_inference(bad):
    original, request, registry, evidence = inputs()
    events = []
    provider = ProposalProvider(FakePredictor(events))
    discovery = provider.discover(request, original, BlindDiscovery(events))
    if bad == "source":
        original += b"changed"
    if bad == "recipe":
        registry = replace(
            registry, champion=replace(registry.champion, preprocessing_sha256="f" * 64)
        )
    if bad == "classes":
        registry = replace(registry, champion=replace(registry.champion, class_ids=("invented",)))
    if bad == "project":
        registry = RegistryState("other", request.recipe.recipe_id)
    if bad == "threshold":
        request = replace(request, candidate_conf=0.9)
        discovery["binding"] = request.binding()
    if bad == "evidence":
        evidence = (replace(evidence[0], approved=False),)
    with pytest.raises(ValueError):
        provider.propose(request, original, discovery, registry, evidence)
    assert events == ["discovery"]
