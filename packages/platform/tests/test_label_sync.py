"""Offline complete-replacement, external-divergence and restart fixtures."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from labelloop_platform.label_sync import Replacement, inverse_edit, replace_once
from labelloop_platform.sync_records import ImageSnapshot, Label, SyncConflict


@pytest.fixture
def baseline():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "sync-http.json").read_text())
    return ImageSnapshot.from_wire(fixture["image"]["response"])


@pytest.fixture
def replacement(baseline):
    return Replacement.prepare(
        operation_id="write-1",
        source_id="source-1",
        accepted_revision=3,
        current_revision=3,
        baseline=baseline,
        canonical_labels=(Label(0, (0.3, 0.4, 0.2, 0.1)),),
        target_classes=frozenset({0}),
        window_id="maintenance-1",
        dataset_kind="source_working",
    )


class Journal:
    def __init__(self, replacement):
        self.state = replacement
        self.records = [replacement]

    def load(self, operation_id):
        assert operation_id == self.state.operation_id
        return self.state

    def compare_and_set(self, expected, updated):
        if expected != self.state:
            return False
        self.state = updated
        self.records.append(updated)
        return True


class Coordinator:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def permits(self, replacement):
        return self.allowed


class Transport:
    def __init__(self, journal, before, after, *, lost_response=False):
        self.journal, self.before, self.after = journal, before, after
        self.lost_response = lost_response
        self.writes = []

    def read_image(self, image_id):
        assert image_id == self.before.image_id
        return self.after if self.writes else self.before

    def replace_labels(self, image_id, body, *, operation_id):
        assert self.journal.state.phase == "in_flight"
        assert self.journal.state.baseline == self.before
        assert operation_id == self.journal.state.operation_id
        self.writes.append((image_id, body))
        if self.lost_response:
            raise TimeoutError("provider response must not be logged")


def test_replaces_complete_set_and_preserves_mixed_task_labels(replacement):
    journal = Journal(replacement)
    saved = replace(replacement.baseline, labels=replacement.intended, updated_at="later")
    transport = Transport(journal, replacement.baseline, saved)
    verified = replace_once(journal, transport, Coordinator(), "write-1")
    assert verified.phase == "verified"
    assert verified.baseline.labels != verified.intended
    assert verified.intended[-1] == replacement.baseline.labels[1]
    assert transport.writes[0][1] == replacement.body()
    assert set(transport.writes[0][1]) == {"labels"}
    assert "segments" in transport.writes[0][1]["labels"][-1]
    assert "metadata" not in transport.writes[0][1]


def test_external_edit_before_dispatch_stops_without_write(replacement):
    journal = Journal(replacement)
    changed = replace(replacement.baseline, updated_at="external-edit")
    transport = Transport(journal, changed, changed)
    conflict = replace_once(journal, transport, Coordinator(), "write-1")
    assert conflict.phase == "conflict" and not transport.writes
    assert conflict.baseline == replacement.baseline
    assert conflict.readback == changed


def test_external_edit_after_dispatch_is_adjudicated_not_copied_to_canonical(replacement):
    journal = Journal(replacement)
    changed = replace(replacement.baseline, labels=(Label(1, (0.5, 0.5, 0.3, 0.3)),))
    transport = Transport(journal, replacement.baseline, changed)
    result = replace_once(journal, transport, Coordinator(), "write-1")
    assert result.phase == "conflict" and result.readback == changed
    assert result.canonical_revision == 3 and result.source_id == "source-1"
    assert len(transport.writes) == 1
    with pytest.raises(SyncConflict, match="replay"):
        replace_once(journal, transport, Coordinator(), "write-1")


def test_local_mutex_or_read_hash_is_not_ui_exclusion(replacement):
    journal = Journal(replacement)
    transport = Transport(journal, replacement.baseline, replacement.baseline)
    with pytest.raises(SyncConflict, match="external writers must quiesce"):
        replace_once(journal, transport, Coordinator(False), "write-1")
    assert not transport.writes


def test_uncertain_patch_and_restart_read_back_never_duplicate_write(replacement):
    journal = Journal(replacement)
    saved = replace(replacement.baseline, labels=replacement.intended)
    transport = Transport(journal, replacement.baseline, saved, lost_response=True)
    uncertain = replace_once(journal, transport, Coordinator(), "write-1")
    assert uncertain.phase == "uncertain"
    assert "provider response" not in repr(uncertain)
    with pytest.raises(SyncConflict, match="reconcile"):
        replace_once(journal, transport, Coordinator(), "write-1")
    assert uncertain.recover(saved).phase == "verified"
    assert uncertain.recover(replacement.baseline).phase == "conflict"
    assert len(transport.writes) == 1


def test_cas_contention_prevents_second_writer(replacement):
    class Contended(Journal):
        def compare_and_set(self, expected, updated):
            return False

    journal = Contended(replacement)
    transport = Transport(journal, replacement.baseline, replacement.baseline)
    with pytest.raises(SyncConflict, match="another writer"):
        replace_once(journal, transport, Coordinator(), "write-1")
    assert not transport.writes


@pytest.mark.parametrize(
    "changes",
    [
        {"current_revision": 4},
        {"dataset_kind": "frozen_release"},
        {"dataset_kind": "derived_working"},
        {"baseline": "truncated"},
        {"canonical_labels": (Label(1, (0.5, 0.5, 0.1, 0.2)),)},
    ],
)
def test_stale_frozen_derived_partial_and_scope_violations_fail(baseline, changes):
    if changes.get("baseline") == "truncated":
        changes = {"baseline": replace(baseline, labels_truncated=True)}
    with pytest.raises(SyncConflict):
        Replacement.prepare(
            **{
                "operation_id": "write",
                "source_id": "source",
                "accepted_revision": 3,
                "current_revision": 3,
                "baseline": baseline,
                "canonical_labels": (),
                "target_classes": frozenset({0}),
                "window_id": "window",
                "dataset_kind": "source_working",
                **changes,
            }
        )


def test_empty_target_set_preserves_other_task_labels(baseline):
    replacement = Replacement.prepare(
        operation_id="negative",
        source_id="source",
        accepted_revision=3,
        current_revision=3,
        baseline=baseline,
        canonical_labels=(),
        target_classes=frozenset({0}),
        window_id="window",
        dataset_kind="source_working",
    )
    assert replacement.intended == (baseline.labels[1],)


def test_clipped_inverse_edit_proposes_only_retained_source_intersection():
    # Recorded actual-raster transform for a 100x100 crop at (50,20), output 320.
    transform = json.dumps(
        {
            "output_size": 320,
            "valid_content_rectangle": [0, 0, 320, 320],
            "inverse": [[0.3125, 0, 50], [0, 0.3125, 20], [0, 0, 1]],
            "crop": {"x": 50, "y": 20, "width": 100, "height": 100},
        }
    )
    proposed = inverse_edit(Label(0, (0.1, 0.4, 0.2, 0.4)), transform, original_was_clipped=True)
    assert proposed.retained_source_box == pytest.approx((50, 40, 70, 80))
    assert proposed.requires_source_review
    assert "discarded extent" in proposed.reason
    unclipped = inverse_edit(Label(0, (0.5, 0.5, 0.2, 0.4)), transform, original_was_clipped=False)
    assert unclipped.retained_source_box == pytest.approx((90, 50, 110, 90))
    assert not unclipped.requires_source_review
