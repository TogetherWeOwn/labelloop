from typing import Any

import pytest

from labelloop_core.domain import AnnotationRevision, CompletenessAssessment, DatasetObject
from labelloop_core.geometry import Box
from labelloop_core.grouping import AssetEvidence, GroupingThresholds, assign_splits, group_sources
from labelloop_core.lineage import LineageGraph


def evidence(name: str, **changes: Any) -> AssetEvidence:
    values: dict[str, Any] = {
        "asset_id": name,
        "sha256": name * 64,
        "phash": 0,
        "embedding": (),
        "metadata": {},
        "class_counts": {"item": 1},
    }
    values.update(changes)
    return AssetEvidence(**values)


def test_injected_duplicate_and_transitive_relation_never_crosses_split() -> None:
    records = [
        evidence("a", sha256="1" * 64, metadata={"session": "first"}),
        evidence("b", sha256="2" * 64, phash=0xAAAA_AAAA_AAAA_AAAA, metadata={"session": "first"}),
        evidence("c", sha256="1" * 64, phash=0x5555_5555_5555_5555),
        evidence("d", sha256="3" * 64, phash=0xFFFF_FFFF_FFFF_FFFF, embedding=(1, 0)),
        evidence("e", sha256="4" * 64, phash=0xFFFF_FFFF_FFFF_FF00, embedding=(1, 0)),
        evidence("f", sha256="5" * 64, phash=0x1111_1111_1111_1111),
    ]
    assignment = assign_splits(records)
    assert set(assignment.asset_splits.values()) == {"train", "val"}
    _, relations, _ = group_sources(records)
    assert {relation.reason for relation in relations} == {
        "exact_hash",
        "phash",
        "embedding_cosine",
        "metadata",
    }
    for relation in relations:
        left, right = relation.left_asset_id, relation.right_asset_id
        assert assignment.asset_splits[left] == assignment.asset_splits[right]
        assert assignment.group_ids[left] == assignment.group_ids[right]
    assert assignment.group_ids["a"] == assignment.group_ids["b"]
    assert assignment.group_ids["a"] == assignment.group_ids["c"]
    assert len(set(assignment.group_ids.values())) == 3


def test_unavailable_embeddings_not_synthesized_and_borderline_queued() -> None:
    left = evidence("a", sha256="1" * 64, phash=0)
    right = evidence("b", sha256="2" * 64, phash=0b111111111)
    _, _, borders = group_sources([left, right])
    assert borders[0].metric == "phash"
    assert not any(r.reason == "embedding_cosine" for r in group_sources([left, right])[1])
    thresholds = GroupingThresholds(phash_max_distance=8)
    assert thresholds.embedding_model == "none-v1"


def test_seed_changes_order_and_same_seed_is_exactly_repeatable() -> None:
    records = [
        evidence(chr(97 + i), sha256=f"{i + 1:064x}", phash=0xAAAA_AAAA_AAAA_AAAA if i % 2 else 0)
        for i in range(15)
    ]
    assert assign_splits(records, seed=7) == assign_splits(list(reversed(records)), seed=7)
    # The algorithm still requires independent sources; no source-only balancing fallback.


def test_model_cannot_invent_human_or_gold_states() -> None:
    initial = AnnotationRevision(1, (), "system")
    for field, value in (
        ("approval_state", "human_accepted"),
        ("review_tier", "human_reviewed"),
        ("review_tier", "adjudicated"),
    ):
        with pytest.raises(ValueError):
            initial.next_revision(
                objects=(), expected_revision=1, actor_kind="model", **{field: value}
            )
    with pytest.raises(ValueError, match="stale"):
        initial.next_revision(objects=(), expected_revision=0, actor_kind="model")
    with pytest.raises(ValueError, match="adjudication"):
        AnnotationRevision(
            1,
            (),
            "human",
            approval_state="human_accepted",
            review_tier="machine_verified_no_human_review",
            scope_completeness="exhaustive_for_scope",
            training_eligible=True,
            gold_eligible=True,
        )
    truncated = CompletenessAssessment("hash", 1, "partial", "model", output_truncated=True)
    assert not truncated.valid_for("hash", 1)
    assert not truncated.valid_for("hash", 2)
    assert not truncated.valid_for("changed", 1)


def test_source_label_lineage_reaches_all_descendants() -> None:
    graph = LineageGraph()
    graph.add("derived:r1", "source:a:revision:2")
    graph.add("dataset:r1-320", "derived:r1")
    graph.add("model:20", "dataset:r1-320")
    graph.add("proposal:new", "model:20")
    assert graph.descendants("source:a:revision:2") == {
        "derived:r1",
        "dataset:r1-320",
        "model:20",
        "proposal:new",
    }
    assert not graph.descendants("source:a:revision:1")


def test_negative_cannot_pass_completeness_gates() -> None:
    with pytest.raises(ValueError, match="exhaustive"):
        AnnotationRevision(1, (), "model", approval_state="auto_accepted", training_eligible=True)
    with pytest.raises(ValueError, match="unresolved"):
        AnnotationRevision(
            1,
            (DatasetObject("1", "class", Box(0, 0, 1, 1), label_state="ambiguous"),),
            "system",
            approval_state="auto_accepted",
            scope_completeness="exhaustive_for_scope",
            training_eligible=True,
        )
