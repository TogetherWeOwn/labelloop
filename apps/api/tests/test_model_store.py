"""Cheap serialization/schema checks; real PostgreSQL transitions run in the CPU smoke."""

from labelloop_api import model_schema
from labelloop_api.model_store import decode_release, decode_report, json_record, record_digest
from labelloop_api.schema import metadata as canonical_metadata
from labelloop_core.evaluation import (
    DerivedPredictions,
    Detection,
    EvaluationImage,
    EvaluationRunner,
    GroundTruth,
)
from labelloop_core.geometry import Box, full_frame_transform
from labelloop_core.registry import ModelRelease
from labelloop_core.training import InferencePolicy


def test_persisted_release_and_source_frame_evaluation_round_trip():
    policy = InferencePolicy(320)
    transform = full_frame_transform(100, 100, 320)
    box = Box(10, 20, 30, 40)
    mapped = transform.apply(box)
    assert mapped is not None
    report = EvaluationRunner(("object", "absent"), policy).evaluate(
        run_id="eval",
        dataset_release_id="data",
        recipe_id="recipe",
        weights_sha256="a" * 64,
        class_map_sha256="b" * 64,
        role="development",
        images=(EvaluationImage("source", 100, 100, (GroundTruth("one", "object", box),)),),
        derived=(DerivedPredictions("source", transform, (Detection("object", mapped, 0.9),)),),
    )
    release = ModelRelease(
        "model",
        "project",
        "recipe",
        "a" * 64,
        "c" * 64,
        "b" * 64,
        ("object", "absent"),
        policy,
        "8.4.204",
        "2.9.1+cpu",
        "d" * 64,
        "onnx",
        (1, 3, 320, 320),
        ("eval",),
    )
    assert decode_release(json_record(release)) == release
    restored = decode_report(json_record(report))
    assert restored == report
    assert not restored.overall.per_class["absent"].supported
    assert restored.overall.per_class["absent"].map50_95 is None


def test_training_tables_do_not_change_the_initial_canonical_migration():
    assert not set(model_schema.metadata.tables) & set(canonical_metadata.tables)
    assert set(model_schema.metadata.tables) == {
        "training_run",
        "evaluation_run",
        "model_release",
        "model_evaluation",
        "registry_head",
        "promotion_event",
    }
    head = model_schema.registry_head
    assert {column.name for column in head.primary_key} == {"project_id", "recipe_id"}
    assert {foreign.column.table.name for foreign in head.foreign_keys} == {
        "project",
        "preprocessing_recipe",
        "model_release",
    }


def test_preprocessing_digest_ignores_mapping_insertion_order_but_not_policy():
    assert record_digest({"side": 320, "pad": 114}) == record_digest({"pad": 114, "side": 320})
    assert record_digest({"side": 320}) != record_digest({"side": 640})
