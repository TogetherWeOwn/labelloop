"""Complete label replacement with durable recovery evidence, never a remote CAS fiction."""

import json
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .sync_records import ImageSnapshot, Label, SyncConflict, same_labels


@dataclass(frozen=True, slots=True)
class Replacement:
    operation_id: str
    source_id: str
    canonical_revision: int
    baseline: ImageSnapshot
    intended: tuple[Label, ...]
    window_id: str
    phase: str = "prepared"
    sequence: int = 0
    readback: ImageSnapshot | None = None
    reason: str = ""

    @classmethod
    def prepare(
        cls,
        *,
        operation_id: str,
        source_id: str,
        accepted_revision: int,
        current_revision: int,
        baseline: ImageSnapshot,
        canonical_labels: tuple[Label, ...],
        target_classes: frozenset[int],
        window_id: str,
        dataset_kind: str,
    ) -> "Replacement":
        if dataset_kind != "source_working":
            raise SyncConflict("frozen/derived datasets are never correction targets")
        if accepted_revision != current_revision or accepted_revision < 1:
            raise SyncConflict("stale canonical revision")
        if baseline.labels_truncated:
            raise SyncConflict("cannot replace from a truncated recovery snapshot")
        if not all((operation_id, source_id, window_id)):
            raise SyncConflict("replacement requires operation, source and writer-window identity")
        if any(
            not label.detection_only or label.class_id not in target_classes
            for label in canonical_labels
        ):
            raise SyncConflict("canonical replacement exceeds target task/class scope")
        if any(index < 0 or index >= len(baseline.class_names) for index in target_classes):
            raise SyncConflict("target classes must resolve in the working dataset catalog")
        # Mixed-task records are indivisible: preserve masks, pose, OBB and their bbox.
        preserved = tuple(
            label
            for label in baseline.labels
            if not label.detection_only or label.class_id not in target_classes
        )
        intended = (*canonical_labels, *preserved)
        if len(intended) > 10000:
            raise SyncConflict("replacement exceeds Platform label limit")
        return cls(operation_id, source_id, accepted_revision, baseline, intended, window_id)

    def body(self) -> dict[str, Any]:
        return {"labels": [label.wire() for label in self.intended]}

    def matches_readback(self, observed: ImageSnapshot) -> bool:
        return (
            not observed.labels_truncated
            and observed.storage_hash == self.baseline.storage_hash
            and (
                observed.image_id,
                observed.dataset_id,
                observed.filename,
                observed.split,
                observed.width,
                observed.height,
                observed.class_names,
            )
            == (
                self.baseline.image_id,
                self.baseline.dataset_id,
                self.baseline.filename,
                self.baseline.split,
                self.baseline.width,
                self.baseline.height,
                self.baseline.class_names,
            )
            and same_labels(self.intended, observed.labels)
        )

    def recover(self, observed: ImageSnapshot) -> "Replacement":
        if self.phase not in {"in_flight", "uncertain"}:
            raise SyncConflict("only outstanding replacement can reconcile readback")
        if self.matches_readback(observed) and not same_labels(self.baseline.labels, self.intended):
            return replace(self, phase="verified", sequence=self.sequence + 1, readback=observed)
        # Even an unchanged baseline cannot prove an outstanding PATCH will not arrive.
        return replace(
            self,
            phase="conflict",
            sequence=self.sequence + 1,
            readback=observed,
            reason="external divergence or unresolved write; adjudicate from snapshot",
        )


class ReplacementJournal(Protocol):
    """Persist snapshot + intent atomically before PATCH, unique active writer per image.

    Insertion reserves image_id across operations in the shared database. CAS must
    include that reservation and sequence, not just a process-local lock. Keep the
    reservation until verified/adjudicated; retries load the same operation ID.
    """

    def load(self, operation_id: str) -> Replacement: ...
    def compare_and_set(self, expected: Replacement, updated: Replacement) -> bool: ...


class WriterCoordinator(Protocol):
    """An enforced annotation-maintenance window, not a Python mutex.

    Verify the durable writer lease, current canonical revision and exclusion of
    external/UI writers. If UI exclusion cannot be established, return False.
    The API has no If-Match: this prerequisite cannot be replaced by hashing.
    """

    def permits(self, replacement: Replacement) -> bool: ...


class LabelTransport(Protocol):
    def read_image(self, image_id: str) -> ImageSnapshot: ...
    # operation_id is the durable replacement ID; the foundation requires a
    # stable write-intent ID for every mutation, so it must be carried here.
    def replace_labels(self, image_id: str, body: dict[str, Any], *, operation_id: str) -> None: ...


def replace_once(
    journal: ReplacementJournal,
    transport: LabelTransport,
    coordinator: WriterCoordinator,
    operation_id: str,
) -> Replacement:
    before = journal.load(operation_id)
    if before.phase != "prepared":
        raise SyncConflict("replacement already dispatched; reconcile instead of replaying PATCH")
    if not coordinator.permits(before):
        raise SyncConflict("no coordinated single-writer window; external writers must quiesce")
    current = transport.read_image(before.baseline.image_id)
    if current.fingerprint() != before.baseline.fingerprint():
        conflict = replace(
            before,
            phase="conflict",
            sequence=before.sequence + 1,
            readback=current,
            reason="external edit before replacement",
        )
        if not journal.compare_and_set(before, conflict):
            raise SyncConflict("replacement reservation changed")
        return conflict
    if before.matches_readback(current):
        satisfied = replace(
            before,
            phase="verified",
            sequence=before.sequence + 1,
            readback=current,
            reason="already satisfied; no PATCH dispatched",
        )
        if not journal.compare_and_set(before, satisfied):
            raise SyncConflict("replacement reservation changed")
        return satisfied
    prepared = replace(before, phase="in_flight", sequence=before.sequence + 1)
    if not journal.compare_and_set(before, prepared):
        raise SyncConflict("another writer claimed this image")
    if not coordinator.permits(prepared):
        result = replace(
            prepared,
            phase="conflict",
            sequence=prepared.sequence + 1,
            reason="writer window or canonical revision changed before dispatch",
        )
    else:
        try:
            transport.replace_labels(
                before.baseline.image_id, before.body(), operation_id=before.operation_id
            )
            result = prepared.recover(transport.read_image(before.baseline.image_id))
        except Exception:
            result = replace(
                prepared,
                phase="uncertain",
                sequence=prepared.sequence + 1,
                reason="mutation/readback uncertain; reconcile before any further write",
            )
    if not journal.compare_and_set(prepared, result):
        raise SyncConflict("replacement changed during dispatch; reconcile outstanding write")
    return result


@dataclass(frozen=True, slots=True)
class InverseEditProposal:
    retained_source_box: tuple[float, float, float, float]
    requires_source_review: bool
    reason: str


def inverse_edit(
    label: Label, transform_json: str, *, original_was_clipped: bool
) -> InverseEditProposal:
    """Propose retained source geometry only; never synthesize discarded extent."""
    if label.bbox is None:
        raise SyncConflict("inverse detection edit requires a box")
    transform = json.loads(transform_json)
    size = transform["output_size"]
    x, y, w, h = label.bbox
    edges = ((x - w / 2) * size, (y - h / 2) * size, (x + w / 2) * size, (y + h / 2) * size)
    left, top, right, bottom = transform["valid_content_rectangle"]
    retained = (
        max(left, edges[0]),
        max(top, edges[1]),
        min(right, edges[2]),
        min(bottom, edges[3]),
    )
    if retained[0] >= retained[2] or retained[1] >= retained[3]:
        raise SyncConflict("edit contains no source pixels")
    inverse = transform["inverse"]
    crop = transform["crop"]
    mapped = (
        max(crop["x"], inverse[0][0] * retained[0] + inverse[0][2]),
        max(crop["y"], inverse[1][1] * retained[1] + inverse[1][2]),
        min(crop["x"] + crop["width"], inverse[0][0] * retained[2] + inverse[0][2]),
        min(crop["y"] + crop["height"], inverse[1][1] * retained[3] + inverse[1][2]),
    )
    internal_edge = (
        (crop["x"] > 0 and abs(mapped[0] - crop["x"]) <= 1e-7)
        or (crop["y"] > 0 and abs(mapped[1] - crop["y"]) <= 1e-7)
        or (
            crop["x"] + crop["width"] < transform.get("source_width", float("inf"))
            and abs(mapped[2] - crop["x"] - crop["width"]) <= 1e-7
        )
        or (
            crop["y"] + crop["height"] < transform.get("source_height", float("inf"))
            and abs(mapped[3] - crop["y"] - crop["height"]) <= 1e-7
        )
    )
    clipped = original_was_clipped or retained != edges or internal_edge
    return InverseEditProposal(
        mapped,
        clipped,
        "discarded extent requires source-image review"
        if clipped
        else "proposed source revision; canonical state is unchanged",
    )
