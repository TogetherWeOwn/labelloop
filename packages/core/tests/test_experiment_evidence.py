from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from labelloop_core.evaluation import EvaluationReport, Metrics
from labelloop_core.experiments import ArmEvidence, Cell, PairedComparison, RetentionObservation
from labelloop_core.training import Checkpoint, InferencePolicy, TrainingRequest, TrainingResult


def test_arm_evidence_preserves_training_evaluation_retention_and_export_pins() -> None:
    policy = InferencePolicy(320)
    cell = Cell("recipe", "curated-only", "base", "no-scene-composition-v1", 0)
    request = TrainingRequest(
        "run", "project", "recipe", "release", "a" * 64, "b" * 64, "splits", "c" * 64, policy
    )
    checkpoint = Checkpoint(0, "d" * 64, 0.5, "eval")
    training = TrainingResult(
        request, (checkpoint,), checkpoint, request.arguments(), "8.4.204", "2.9.1+cpu", False
    )
    report = EvaluationReport(
        "eval",
        "release",
        "recipe",
        checkpoint.sha256,
        "b" * 64,
        "development",
        policy,
        Metrics(1, 0, None, None, None, None, {}),
        {},
        (),
    )
    retention = RetentionObservation("asset", 3, 2, 1, 0)
    shapes = (("inference", ((1, 3, 320, 320),)),)
    arm = ArmEvidence(
        "e" * 64,
        cell,
        training,
        report,
        (("group", report),),
        (retention,),
        shapes,
        "f" * 64,
        checkpoint.sha256,
        (1, 3, 320, 320),
        policy,
        1,
        2,
    )
    assert arm.training is training and arm.evaluation is report
    assert arm.group_reports == (("group", report),)
    assert arm.retention == (retention,) and arm.tensor_shapes == shapes
    assert arm.export_weights_sha256 == checkpoint.sha256
    assert arm.export_sha256 != arm.export_weights_sha256
    assert arm.export_signature == (1, 3, 320, 320) and arm.export_policy == policy
    assert (arm.export_samples, arm.export_detections) == (1, 2)
    assert replace(arm) == arm
    assert asdict(arm)["retention"][0]["after"] == 2
    for record, field, value in ((arm, "manifest_sha256", "0" * 64), (retention, "after", 3)):
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, value)


def test_comparison_record_does_not_establish_quality_exit_by_default() -> None:
    baseline = Cell("recipe", "curated-only", "base", "no-scene-composition-v1", 0)
    candidate = replace(baseline, initialization="warm-start")
    result = PairedComparison(
        "recipe",
        "initialization",
        baseline,
        candidate,
        "source-group-macro-map50-95-v1",
        (0, 1),
        2,
        4,
        8,
        (("object", 8),),
        (("g1", 0.1), ("g2", 0.2)),
        0.15,
        (0.1, 0.2),
        (),
        "observed-gain-unqualified",
        False,
    )
    assert not result.m6_exit_established
    assert result.baseline == baseline and result.candidate == candidate
    assert result.seeds == (0, 1) and result.source_groups == 2
    assert result.group_deltas == (("g1", 0.1), ("g2", 0.2))
    assert result.bootstrap_95_interval == (0.1, 0.2)
    assert not replace(result, fixture_only=True).m6_exit_established
    assert asdict(result)["per_class_support"] == (("object", 8),)
    field = "outcome"
    with pytest.raises(FrozenInstanceError):
        setattr(result, field, "qualified")
