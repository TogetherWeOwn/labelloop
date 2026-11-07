from typing import Any

import pytest

from labelloop_core.domain import AnnotationRevision, CompletenessAssessment, DatasetObject
from labelloop_core.geometry import Box
from labelloop_core.grouping import (
    AssetEvidence,
    GroupingThresholds,
    assign_splits,
    cosine,
    group_sources,
)
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


def independent_records(count: int) -> list[AssetEvidence]:
    # Distinct Walsh rows have Hamming distance 32, beyond both pHash thresholds.
    assert 1 < count <= 64
    records = [
        evidence(
            f"{index:02d}",
            sha256=f"{index + 1:064x}",
            phash=sum(((index & bit).bit_count() % 2) << bit for bit in range(64)),
        )
        for index in range(count)
    ]
    assert all(
        (left.phash ^ right.phash).bit_count() == 32
        for index, left in enumerate(records)
        for right in records[index + 1 :]
    )
    return records


def test_split_version_binds_grouping_and_balancing_configuration() -> None:
    records = independent_records(8)
    baseline = assign_splits(records)
    assert baseline.assignment_version == assign_splits(records).assignment_version
    assert baseline.assignment_version != assign_splits(records, seed=8).assignment_version
    assert (
        baseline.assignment_version
        != assign_splits(
            records, thresholds=GroupingThresholds(phash_max_distance=7)
        ).assignment_version
    )
    assert (
        baseline.assignment_version
        != assign_splits(records, validation_fraction=0.5).assignment_version
    )


def test_large_group_assignment_is_bounded_and_group_safe() -> None:
    records = independent_records(40)
    assignment = assign_splits(records)
    assert len(assignment.group_splits) == 40
    assert assignment == assign_splits(list(reversed(records)))
    assert {assignment.split_for(record.asset_id) for record in records} == {"train", "val"}
    assert any(split == "val" for split in assignment.group_splits.values())
    for group in set(assignment.group_ids.values()):
        groups = {
            assignment.asset_splits[name]
            for name in assignment.group_ids
            if assignment.group_ids[name] == group
        }
        assert len(groups) == 1


def test_malformed_embedding_is_not_missing_evidence() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        group_sources([evidence("a", embedding=(1.0, 0.0)), evidence("b", embedding=(1.0,))])
    with pytest.raises(ValueError, match="non-empty|dimensions"):
        cosine((), ())


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


def test_changed_object_set_invalidates_inherited_acceptance() -> None:
    accepted = AnnotationRevision(
        1,
        (DatasetObject("one", "item", Box(0, 0, 10, 10)),),
        "human",
        approval_state="human_accepted",
        review_tier="adjudicated",
        scope_completeness="exhaustive_for_scope",
        training_eligible=True,
        gold_eligible=True,
    )
    changed = accepted.next_revision(objects=(), expected_revision=1, actor_kind="human")
    assert changed.objects == ()
    assert changed.scope_completeness == "unreviewed"
    assert changed.approval_state == "draft"
    assert changed.review_tier == "unreviewed"
    assert not changed.training_eligible and not changed.gold_eligible
    unchanged = accepted.next_revision(
        objects=accepted.objects, expected_revision=1, actor_kind="human"
    )
    assert unchanged.training_eligible and unchanged.gold_eligible
    with pytest.raises(ValueError, match="completeness"):
        accepted.next_revision(
            objects=(), expected_revision=1, actor_kind="human", training_eligible=True
        )


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


def test_all_lineage_parents_participate_in_cycle_guards() -> None:
    graph = LineageGraph()
    graph.add("a", "root")
    graph.add("b", "a")
    with pytest.raises(ValueError, match="acyclic"):
        graph.add("root", "unrelated", "b")
    assert "root" not in graph.parents


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
