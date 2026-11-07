from dataclasses import replace
from typing import Any

import pytest

from labelloop_core.audit import (
    AuditCandidate,
    AuditObservation,
    AuditSample,
    assess_audit,
    select_audit_sample,
)
from labelloop_core.domain import AnnotationRevision, DatasetObject
from labelloop_core.geometry import Box


def candidate(asset_id: str, *, negative: bool = False) -> AuditCandidate:
    return AuditCandidate(
        asset_id,
        AnnotationRevision(
            1,
            () if negative else (DatasetObject("one", "a", Box(0, 0, 5, 5)),),
            "system",
            approval_state="auto_accepted",
            review_tier="machine_verified_no_human_review",
            scope_completeness="exhaustive_for_scope",
            training_eligible=True,
        ),
        "qualified-pipeline",
        frozenset({"teacher", "reviewer-model"}),
    )


def observation(asset_id: str) -> AuditObservation:
    return AuditObservation(asset_id, 1, "independent-auditor", True, True, True)


def test_sample_is_reproducible_input_order_independent_and_covers_negatives() -> None:
    pool = tuple(candidate(f"source-{index}", negative=index < 4) for index in range(20))
    sample = select_audit_sample(pool, sample_id="batch", size=5, seed=7)
    assert sample == select_audit_sample(tuple(reversed(pool)), sample_id="batch", size=5, seed=7)
    assert len(sample.candidates) == 5
    assert any(not item.revision.objects for item in sample.candidates)
    assert any(item.revision.objects for item in sample.candidates)
    assert sample != select_audit_sample(pool, sample_id="batch", size=5, seed=8)


@pytest.mark.parametrize("negative", [False, True])
def test_single_stratum_pool_can_still_be_audited(negative: bool) -> None:
    pool = tuple(candidate(str(index), negative=negative) for index in range(4))
    sample = select_audit_sample(pool, sample_id="sample", size=2, seed=1)
    assert len(sample.candidates) == 2


@pytest.mark.parametrize("size", [0, 1, 4, True])
def test_sample_size_cannot_skip_a_present_stratum_or_exceed_pool(size: int) -> None:
    with pytest.raises(ValueError):
        select_audit_sample(
            (candidate("positive"), candidate("negative", negative=True)),
            sample_id="sample",
            size=size,
            seed=7,
        )


@pytest.mark.parametrize("seed", [True, 1.0, "7", None])
def test_audit_seed_requires_a_strict_integer(seed: Any) -> None:
    with pytest.raises(ValueError, match="seed must be an integer"):
        select_audit_sample((candidate("source"),), sample_id="sample", size=1, seed=seed)


@pytest.mark.parametrize("reviewer_id", ["", " \t"])
def test_audit_candidate_requires_nonblank_prior_reviewer_ids(reviewer_id: str) -> None:
    with pytest.raises(ValueError, match="provenance"):
        replace(candidate("source"), prior_reviewer_ids=frozenset({"teacher", reviewer_id}))


def test_prior_reviewer_identity_is_also_normalized() -> None:
    item = replace(candidate("source"), prior_reviewer_ids=frozenset({" Teacher \t"}))
    sample = select_audit_sample((item,), sample_id="sample", size=1, seed=7)
    with pytest.raises(ValueError, match="independent"):
        assess_audit(sample, (replace(observation("source"), reviewer_id="teacher"),))


def test_duplicate_pool_members_and_nonaccepted_labels_are_rejected() -> None:
    item = candidate("source")
    with pytest.raises(ValueError, match="one revision"):
        select_audit_sample((item, item), sample_id="sample", size=1, seed=7)
    with pytest.raises(ValueError, match="accepted labels"):
        replace(item, revision=AnnotationRevision(1, (), "model"))


def test_confidently_wrong_ordinary_acceptance_blocks_the_audit() -> None:
    sample = select_audit_sample(
        (candidate("wrong-positive"), candidate("missed-object", negative=True)),
        sample_id="sample",
        size=2,
        seed=7,
    )
    result = assess_audit(
        sample,
        (
            replace(observation("wrong-positive"), labels_correct=False),
            replace(observation("missed-object"), scope_complete=False),
        ),
    )
    assert not result.passed
    assert result.blocking_asset_ids == ("missed-object", "wrong-positive")
    assert result.pending_asset_ids == ()


@pytest.mark.parametrize(("blind", "truncated"), [(False, False), (True, True)])
def test_nonblind_or_truncated_evidence_cannot_pass(blind: bool, truncated: bool) -> None:
    sample = select_audit_sample((candidate("source"),), sample_id="sample", size=1, seed=7)
    result = assess_audit(
        sample,
        (
            replace(
                observation("source"),
                blind_full_image_inspected=blind,
                output_truncated=truncated,
            ),
        ),
    )
    assert not result.passed and result.blocking_asset_ids == ("source",)


def test_missing_evidence_is_pending_not_success() -> None:
    sample = select_audit_sample((candidate("source"),), sample_id="sample", size=1, seed=7)
    result = assess_audit(sample, ())
    assert not result.passed and result.pending_asset_ids == ("source",)
    assert assess_audit(sample, (observation("source"),)).passed
    with pytest.raises(ValueError, match="empty audit"):
        assess_audit(AuditSample("empty", (), 1), ())


@pytest.mark.parametrize(
    "reviewer_id", ["teacher", "reviewer-model", "", " \t", "Teacher", "teacher "]
)
def test_original_decider_cannot_independently_audit_its_acceptance(reviewer_id: str) -> None:
    sample = select_audit_sample((candidate("source"),), sample_id="sample", size=1, seed=7)
    with pytest.raises(ValueError, match="independent"):
        assess_audit(sample, (replace(observation("source"), reviewer_id=reviewer_id),))


@pytest.mark.parametrize(
    "field",
    ["blind_full_image_inspected", "labels_correct", "scope_complete", "output_truncated"],
)
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_malformed_audit_flags_are_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValueError, match="must be booleans"):
        replace(observation("source"), **{field: value})


def test_observations_cannot_change_frozen_membership_or_revisions() -> None:
    sample = select_audit_sample((candidate("source"),), sample_id="sample", size=1, seed=7)
    for observations in (
        (observation("other"),),
        (replace(observation("source"), revision=2),),
        (observation("source"), observation("source")),
    ):
        with pytest.raises(ValueError):
            assess_audit(sample, observations)
