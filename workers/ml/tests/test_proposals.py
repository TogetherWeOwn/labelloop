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
    InitialDetectorRelease,
    PredictionBatch,
    ProposalProvider,
    ProposalRequest,
    record_digest,
)


@pytest.mark.parametrize("reverse", [False, True])
def test_record_digest_rejects_colliding_json_keys(reverse):
    pairs = [(1, "original"), ("1", "replacement")]
    payload = {"nested": ({"values": dict(reversed(pairs) if reverse else pairs)},)}
    with pytest.raises(ValueError, match="colliding JSON object keys"):
        record_digest(payload)
    assert record_digest({"values": {2: "two", 10: "ten"}}) == record_digest(
        {"values": {"2": "two", "10": "ten"}}
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


@pytest.mark.parametrize("field", ["full_image", "partial", "truncated"])
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
@pytest.mark.parametrize("resume", [False, True])
def test_discovery_rejects_non_boolean_coverage_before_teacher(field, value, resume):
    original, request, registry, evidence = inputs()
    events = []
    provider = ProposalProvider(FakePredictor(events))
    if resume:
        discovery = provider.discover(request, original, BlindDiscovery(events))
        discovery = json.loads(json.dumps(discovery)) | {field: value}
        with pytest.raises(ValueError, match="coverage flags must be booleans"):
            provider.propose(request, original, discovery, registry, evidence)
    else:
        with pytest.raises(ValueError, match="coverage flags must be booleans"):
            provider.discover(request, original, BlindDiscovery(events, **{field: value}))
    assert events == ["discovery"]


@pytest.mark.parametrize("field,value", [("allow_upscale", 1), ("optimize", 0)])
@pytest.mark.parametrize("persisted", [False, True])
def test_discovery_binding_preserves_nested_json_types(field, value, persisted):
    original, request, registry, evidence = inputs()
    events = []
    provider = ProposalProvider(FakePredictor(events))
    discovery = provider.discover(request, original, BlindDiscovery(events))
    if persisted:
        discovery = json.loads(json.dumps(discovery))
    binding = discovery["binding"]["recipe"]
    if field == "optimize":
        binding = binding["encoder_options"]
    assert type(binding[field]) is bool
    binding[field] = value
    with pytest.raises(ValueError, match="different pinned"):
        provider.propose(request, original, discovery, registry, evidence)
    assert events == ["discovery"]


@pytest.mark.parametrize("field", ["reference_approved", "registry_nms"])
def test_workflow_binding_preserves_nested_json_types_before_io(field):
    from types import SimpleNamespace

    from labelloop_worker_ml.proposal_workflow import ProposalWorkflow

    _, request, registry, evidence = inputs()
    events = []
    workflow = ProposalWorkflow(
        request,
        registry,
        evidence,
        "blind-fixture-v1",
        ProposalProvider(FakePredictor(events)),
        BlindDiscovery(events),
        lambda source: pytest.fail("must reject before source IO"),
    )
    job_input = workflow.job_input()
    if field == "reference_approved":
        job_input["references"][0]["approved"] = 1
    else:
        job_input["registry"]["champion"]["policy"]["agnostic_nms"] = 0
    context = SimpleNamespace(
        lease=SimpleNamespace(input=job_input),
        artifacts={},
        heartbeat=lambda: pytest.fail("must reject before heartbeat"),
    )
    for execute in (workflow.discover, workflow.propose):
        with pytest.raises(ValueError, match="configuration differs"):
            execute(context)
    assert events == []


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
    assert result["source"] == "none"
    assert result["discovery_candidates"] == []
    assert result["discovery"]["boxes"]  # Blind inspection evidence remains separate.


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


@pytest.mark.parametrize("nms,expected", [("external", 1), ("end2end", 2)])
def test_full_frame_end2end_preserves_overlapping_instances(nms, expected):
    original, request, registry, evidence = inputs()
    events = []

    class Overlapping(FakePredictor):
        def predict(self, request, original, champion):
            batch = super().predict(request, original, champion)
            item = batch.derived[0]
            detection = item.detections[0]
            return replace(
                batch,
                derived=(
                    replace(
                        item,
                        detections=(detection, replace(detection, score=0.019)),
                    ),
                ),
            )

    registry = replace(
        registry,
        champion=replace(
            registry.champion,
            policy=replace(registry.champion.policy, nms=nms),
        ),
    )
    provider = ProposalProvider(Overlapping(events))
    discovery = provider.discover(request, original, BlindDiscovery(events))
    result = provider.propose(request, original, discovery, registry, evidence)
    assert len(result["teacher_candidates"]) == expected
    assert result["parameters"]["nms"] is (nms == "external")


def initial_detector(model):
    return InitialDetectorRelease(
        "initial",
        model.project_id,
        "frozen-project-v1",
        model.recipe_id,
        model.weights_sha256,
        model.preprocessing_sha256,
        model.class_ids,
        model.policy,
        model.ultralytics_version,
        model.torch_version,
        "constructive-fixture",
        "seed0-v1",
        "CC0-1.0",
        "fixture-admin",
        "admin",
        True,
        "frozen-project-v1",
    )


@pytest.mark.parametrize("mapping,expected", [((), None), ((("sku", "sku"),), "sku")])
@pytest.mark.parametrize("side", [320, 640])
def test_initial_detector_requires_explicit_frozen_mapping(side, mapping, expected):
    original, request, registry, evidence = inputs(side)
    request = replace(request, project_release_id="frozen-project-v1")
    initial = replace(initial_detector(registry.champion), class_mapping=mapping)
    events = []
    provider = ProposalProvider(FakePredictor(events))
    discovery = provider.discover(request, original, BlindDiscovery(events))
    result = provider.propose(
        request, original, discovery, replace(registry, champion=None), evidence, initial
    )
    assert result["source"] == "initial"
    assert result["checkpoint_sha256"] == initial.weights_sha256
    assert result["teacher"]["licence"] == "CC0-1.0"
    assert result["teacher"]["project_release_id"] == "frozen-project-v1"
    assert result["teacher_candidates"][0]["class_id"] == expected
    assert result["teacher_candidates"][0]["label_state"] == (
        "identified" if expected else "unknown"
    )
    assert result["approval_state"] == "proposed"
    assert result["scope_completeness"] == "unreviewed"


@pytest.mark.parametrize(
    "changes",
    [
        {"frozen": False},
        {"approved_role": "worker-ml"},
        {"approved_role": "reviewer"},
        {"mapping_release_id": "not-the-frozen-release"},
        {"approved_principal_id": ""},
        {"licence": ""},
        {"checkpoint_source": ""},
    ],
)
def test_initial_detector_refuses_unfrozen_or_worker_mapping(changes):
    _, _, registry, _ = inputs()
    with pytest.raises(ValueError):
        replace(initial_detector(registry.champion), **changes)


def test_qualifying_champion_precedes_initial_detector():
    original, request, registry, evidence = inputs()
    events = []
    provider = ProposalProvider(FakePredictor(events))
    discovery = provider.discover(request, original, BlindDiscovery(events))
    initial = replace(initial_detector(registry.champion), weights_sha256="f" * 64)
    result = provider.propose(request, original, discovery, registry, evidence, initial)
    assert result["source"] == "champion"
    assert result["checkpoint_sha256"] == registry.champion.weights_sha256
