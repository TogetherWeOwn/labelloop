import json
from dataclasses import replace
from typing import Any

import pytest

from labelloop_core.domain import AnnotationRevision, DatasetObject, SourceAsset
from labelloop_core.geometry import Box
from labelloop_core.review import (
    MAX_OPERATIONS,
    MAX_PATCH_BYTES,
    CorrectionLoop,
    ReviewContext,
    decode_patch,
    validate_patch,
)


def context(**changes: Any) -> ReviewContext:
    baseline = ReviewContext(
        SourceAsset("source", "project", "a" * 64, f"sha256/aa/{'a' * 64}", 100, 80),
        "ontology-scope-hash",
        frozenset({"a", "b"}),
        frozenset({"source", "reference"}),
        "scene_object",
        "blind-discovery",
        "review",
        discovery_complete=True,
    )
    return replace(baseline, **changes)


def revision(**changes: Any) -> AnnotationRevision:
    baseline = AnnotationRevision(1, (DatasetObject("one", "a", Box(1, 2, 20, 30)),), "model")
    return replace(baseline, **changes)


def operation(action: str, **changes: Any) -> dict[str, Any]:
    return {
        "action": action,
        "object_id": "one",
        "reason": "observed source pixels",
        "evidence_ids": ["source"],
        "representation": "scene_object",
        **changes,
    }


def patch(operations: Any = None, **changes: Any) -> str:
    return json.dumps(
        {
            "asset_id": "source",
            "expected_revision": 1,
            "operations": [] if operations is None else operations,
            "full_image_inspected": True,
            "output_truncated": False,
            "unresolved_regions": [],
            **changes,
        }
    )


@pytest.mark.parametrize("objects", [(), revision().objects])
def test_complete_machine_evidence_never_asserts_release_or_human_fields(
    objects: tuple[DatasetObject, ...],
) -> None:
    loop = CorrectionLoop(revision(objects=objects)).consume(context(), patch())
    assert loop.status == "accepted"
    assert loop.reason == "machine_evidence_only"
    assert loop.current.approval_state == "proposed"
    assert loop.current.review_tier == "machine_verified_no_human_review"
    assert loop.current.actor_kind == "model"
    assert not loop.current.training_eligible and not loop.current.gold_eligible
    assert loop.last_validation is not None and loop.last_validation.completeness is not None
    assert loop.last_validation.completeness.valid_for("ontology-scope-hash", 2)
    with pytest.raises(ValueError, match="terminal"):
        loop.consume(context(), patch())


@pytest.mark.parametrize(
    "change",
    [{"discovery_complete": False}, {"discovery_truncated": True}],
)
def test_zero_detections_need_independent_complete_discovery(change: dict[str, bool]) -> None:
    loop = CorrectionLoop(revision(objects=())).consume(context(**change), patch())
    assert loop.status != "accepted"
    assert loop.current.scope_completeness == "partial"
    assert not loop.current.training_eligible


@pytest.mark.parametrize(
    "change",
    [{"full_image_inspected": False}, {"unresolved_regions": [[50, 50, 60, 60]]}],
)
def test_partial_inspection_is_not_a_verified_negative(change: dict[str, Any]) -> None:
    loop = CorrectionLoop(revision(objects=()), maximum_passes=1).consume(
        context(), patch(**change)
    )
    assert loop.status == "unresolved"
    assert loop.reason == "pass_limit"


@pytest.mark.parametrize("transport_truncated", [False, True])
def test_transport_or_payload_truncation_never_passes_completeness(
    transport_truncated: bool,
) -> None:
    loop = CorrectionLoop(revision()).consume(
        context(),
        patch(output_truncated=not transport_truncated),
        transport_truncated=transport_truncated,
    )
    assert loop.status == "unresolved" and loop.reason == "truncated_output"
    assert loop.last_validation is not None and loop.last_validation.completeness is not None
    assert loop.last_validation.completeness.output_truncated
    assert not loop.last_validation.completeness.valid_for("ontology-scope-hash", 2)


@pytest.mark.parametrize(
    "change",
    [
        {"actor_kind": "human", "approval_state": "human_accepted"},
        {"actor_kind": "human", "review_tier": "human_reviewed"},
        {"actor_kind": "human", "review_tier": "adjudicated"},
    ],
)
def test_even_noop_model_pass_cannot_supersede_human_review(change: dict[str, str]) -> None:
    current = revision(**change)
    result = validate_patch(context(), current, patch())
    assert result.needs_adjudication and result.proposal is None
    loop = CorrectionLoop(current).consume(context(), patch())
    assert loop.status == "unresolved" and loop.current == current


@pytest.mark.parametrize("action", ["delete", "duplicate"])
def test_deletions_and_duplicates_remain_adjudication_proposals(action: str) -> None:
    current = revision(objects=(*revision().objects, DatasetObject("two", "a", Box(40, 2, 60, 30))))
    extra = {"duplicate_of": "two"} if action == "duplicate" else {}
    result = validate_patch(context(), current, patch([operation(action, **extra)]))
    assert result.needs_adjudication and result.proposal is None
    assert len(current.objects) == 2


@pytest.mark.parametrize("target", ["one", "missing"])
def test_duplicate_target_must_exist_and_be_distinct(target: str) -> None:
    with pytest.raises(ValueError, match="duplicate target"):
        validate_patch(context(), revision(), patch([operation("duplicate", duplicate_of=target)]))


@pytest.mark.parametrize(
    "edit",
    [
        operation("relabel", class_id="b"),
        operation("geometry", box=[2, 3, 21, 31]),
        operation("add", object_id="two", class_id="b", box=[40, 40, 60, 60]),
    ],
)
def test_changed_object_set_needs_a_second_completeness_pass(edit: dict[str, Any]) -> None:
    current = revision()
    first = CorrectionLoop(current).consume(context(), patch([edit]))
    assert first.status == "running" and first.passes == 1
    assert first.current.scope_completeness == "partial"
    assert current.revision == 1 and first.current.revision == 2
    second = first.consume(context(), patch(expected_revision=2))
    assert second.status == "accepted" and second.passes == 2
    assert not second.current.training_eligible and not second.current.gold_eligible


def test_oscillation_is_escalated_and_never_runs_a_third_pass() -> None:
    first = CorrectionLoop(revision()).consume(
        context(), patch([operation("relabel", class_id="b")])
    )
    second = first.consume(
        context(), patch([operation("relabel", class_id="a")], expected_revision=2)
    )
    assert second.status == "unresolved" and second.reason == "oscillating_corrections"
    with pytest.raises(ValueError, match="terminal"):
        second.consume(context(), patch(expected_revision=3))


def test_persistent_disagreement_stops_at_two_passes() -> None:
    first = CorrectionLoop(revision()).consume(
        context(), patch([operation("geometry", box=[2, 3, 21, 31])])
    )
    second = first.consume(
        context(), patch([operation("geometry", box=[3, 4, 22, 32])], expected_revision=2)
    )
    assert second.status == "unresolved" and second.reason == "pass_limit"
    assert second.passes == 2


@pytest.mark.parametrize("limit", [0, 3, True])
def test_loop_limit_cannot_be_raised(limit: int) -> None:
    with pytest.raises(ValueError, match="limited"):
        CorrectionLoop(revision(), maximum_passes=limit)


@pytest.mark.parametrize("state", ["ambiguous", "unknown", "insufficient_evidence"])
def test_uncertainty_stays_visible_and_ineligible(state: str) -> None:
    loop = CorrectionLoop(revision(), maximum_passes=1).consume(
        context(), patch([operation("uncertain", label_state=state)])
    )
    assert loop.status == "unresolved"
    assert loop.current.objects[0].label_state == state
    assert not loop.current.training_eligible


@pytest.mark.parametrize(
    "extra",
    [
        {"approval_state": "human_accepted"},
        {"review_tier": "adjudicated"},
        {"training_eligible": True},
        {"endpoint": "not-an-endpoint"},
        {"split": "train"},
        {"spend": 1},
        {"gold_eligible": True},
    ],
)
def test_model_state_endpoint_split_and_spend_fields_are_forbidden(extra: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        decode_patch(patch(**extra))
    loop = CorrectionLoop(revision()).consume(context(), patch(**extra))
    assert loop.status == "failed" and loop.current == revision()


@pytest.mark.parametrize(
    "edit",
    [
        operation("relabel", class_id="invented"),
        operation("confirm", object_id="missing"),
        operation("add", class_id="a", box=[40, 40, 60, 60]),
        operation("confirm", evidence_ids=["holdout"]),
        operation("confirm", representation="hud_portrait"),
        operation("uncertain", label_state="identified"),
        operation("confirm", training_eligible=True),
        operation("relabel", class_id="a", box=[1, 2, 3, 4]),
    ],
)
def test_semantically_invalid_operations_leave_source_untouched(edit: dict[str, Any]) -> None:
    current = revision()
    with pytest.raises(ValueError):
        validate_patch(context(), current, patch([edit]))
    assert current == revision()


@pytest.mark.parametrize(
    "coordinates",
    [
        [-1, 0, 5, 5],
        [0, 0, 0, 5],
        [2, 0, 1, 5],
        [0, 0, 101, 5],
        [0, 0, 5, 81],
        [0, 0, float("nan"), 5],
        [0, 0, float("inf"), 5],
        [False, 0, 5, 5],
        [0, 0, 10**400, 5],
        [0, 0, "5", 5],
    ],
)
def test_malformed_and_out_of_frame_geometry_is_rejected(coordinates: list[Any]) -> None:
    with pytest.raises(ValueError):
        validate_patch(context(), revision(), patch([operation("geometry", box=coordinates)]))


def test_near_identical_boxes_are_escalated_even_across_classes() -> None:
    result = validate_patch(
        context(),
        revision(),
        patch([operation("add", object_id="two", class_id="b", box=[1, 2, 20, 30])]),
    )
    assert result.needs_adjudication and result.proposal is None


@pytest.mark.parametrize("changes", [{"asset_id": "other"}, {"expected_revision": 2}])
def test_foreign_or_stale_patch_is_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="source or expected revision"):
        validate_patch(context(), revision(), patch(**changes))


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "null",
        "{",
        '{"asset_id":"first","asset_id":"second"}',
        patch(expected_revision=True),
        patch(full_image_inspected=1),
        patch(output_truncated="false"),
        patch(operations={}),
        patch(operations=[operation("confirm")] * (MAX_OPERATIONS + 1)),
        patch(unresolved_regions={}),
        " " * (MAX_PATCH_BYTES + 1),
    ],
)
def test_malformed_or_unbounded_patch_fails_closed(raw: str) -> None:
    with pytest.raises(ValueError):
        decode_patch(raw)


def test_repeated_object_operations_cannot_partially_apply() -> None:
    current = revision()
    with pytest.raises(ValueError, match="one operation"):
        validate_patch(context(), current, patch([operation("confirm"), operation("delete")]))
    assert current == revision()


def test_changed_ontology_invalidates_prior_machine_completeness() -> None:
    result = validate_patch(context(), revision(), patch())
    assert result.completeness is not None
    assert not result.completeness.valid_for("different-scope", 2)
    assert not result.completeness.valid_for("ontology-scope-hash", 3)


def test_discovery_cannot_reuse_the_review_run_identity() -> None:
    with pytest.raises(ValueError, match="separate run"):
        context(discovery_run_id="review")


@pytest.mark.parametrize(
    "changes",
    [
        {"ontology_hash": "other"},
        {"evidence_ids": frozenset({"source"})},
        {"representation": "hud_portrait"},
    ],
)
def test_loop_cannot_switch_scope_between_passes(changes: dict[str, Any]) -> None:
    first = CorrectionLoop(revision()).consume(
        context(), patch([operation("relabel", class_id="b")])
    )
    second = first.consume(context(**changes), patch(expected_revision=2))
    assert second.status == "failed" and second.reason == "review_context_changed"
    assert second.current == first.current


def test_oscillation_hash_normalizes_integer_and_float_coordinates() -> None:
    first = CorrectionLoop(revision()).consume(
        context(), patch([operation("geometry", box=[2, 3, 21, 31])])
    )
    second = first.consume(
        context(),
        patch(
            [operation("geometry", box=[1.0, 2.0, 20.0, 30.0])],
            expected_revision=2,
        ),
    )
    assert second.status == "unresolved" and second.reason == "oscillating_corrections"
