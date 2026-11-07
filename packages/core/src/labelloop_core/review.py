"""Untrusted review patches and bounded, offline correction decisions.

These functions produce proposals and machine evidence, never release eligibility.
Persistence must authenticate the writer and atomically recheck the base revision.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal, cast

from .domain import (
    AnnotationRevision,
    CompletenessAssessment,
    DatasetObject,
    LabelState,
    SourceAsset,
)
from .geometry import Box

MAX_PATCH_BYTES = 262_144
MAX_OPERATIONS = 128
ACTIONS = frozenset({"confirm", "relabel", "geometry", "add", "duplicate", "delete", "uncertain"})


@dataclass(frozen=True, slots=True)
class ReviewContext:
    asset: SourceAsset
    ontology_hash: str
    class_ids: frozenset[str]
    evidence_ids: frozenset[str]
    representation: str
    discovery_run_id: str
    review_run_id: str
    discovery_complete: bool = False
    discovery_truncated: bool = False

    def __post_init__(self) -> None:
        if not all(
            (
                self.ontology_hash,
                self.class_ids,
                self.evidence_ids,
                self.representation,
                self.discovery_run_id,
                self.review_run_id,
            )
        ):
            raise ValueError("review requires pinned scope, evidence and discovery provenance")
        if self.discovery_run_id == self.review_run_id:
            raise ValueError("discovery must precede review in a separate run")

    def scope_key(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "asset": asdict(self.asset),
                    "ontology": self.ontology_hash,
                    "classes": sorted(self.class_ids),
                    "evidence": sorted(self.evidence_ids),
                    "representation": self.representation,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class PatchOperation:
    action: str
    object_id: str
    reason: str
    evidence_ids: tuple[str, ...]
    representation: str
    class_id: str | None = None
    box: Box | None = None
    label_state: str | None = None
    duplicate_of: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewPatch:
    asset_id: str
    expected_revision: int
    operations: tuple[PatchOperation, ...]
    full_image_inspected: bool
    output_truncated: bool
    unresolved_regions: tuple[Box, ...]


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError("patch text must be nonempty and bounded")
    return value


def _box(value: Any) -> Box:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("patch boxes require four source coordinates")
    try:
        valid = all(type(part) in {int, float} and math.isfinite(part) for part in value)
    except OverflowError as error:
        raise ValueError("patch coordinates exceed the numeric range") from error
    if not valid:
        raise ValueError("patch coordinates must be finite numbers, not booleans")
    return Box(*value)


def _record(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError("duplicate JSON fields are not permitted")
        record[key] = value
    return record


def decode_patch(raw: str) -> ReviewPatch:
    """Reject extra fields rather than let model text smuggle state or actions."""
    if len(raw.encode("utf-8")) > MAX_PATCH_BYTES:
        raise ValueError("review patch exceeds the byte limit")
    try:
        data = json.loads(raw, object_pairs_hook=_record)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError("malformed review JSON") from error
    fields = {
        "asset_id",
        "expected_revision",
        "operations",
        "full_image_inspected",
        "output_truncated",
        "unresolved_regions",
    }
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError("review patch has missing or forbidden fields")
    revision = data["expected_revision"]
    if type(revision) is not int or revision < 1:
        raise ValueError("expected revision must be a positive integer")
    for field in ("full_image_inspected", "output_truncated"):
        if type(data[field]) is not bool:
            raise ValueError("inspection and truncation flags must be booleans")
    raw_operations = data["operations"]
    if not isinstance(raw_operations, list) or len(raw_operations) > MAX_OPERATIONS:
        raise ValueError("review operations must be a bounded list")
    operations: list[PatchOperation] = []
    required = {"action", "object_id", "reason", "evidence_ids", "representation"}
    optional = {"class_id", "box", "label_state", "duplicate_of"}
    for item in raw_operations:
        if not isinstance(item, dict) or not required <= set(item) <= required | optional:
            raise ValueError("operation has missing or forbidden fields")
        action = item["action"]
        if not isinstance(action, str) or action not in ACTIONS:
            raise ValueError("unsupported patch action")
        evidence = item["evidence_ids"]
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= MAX_OPERATIONS:
            raise ValueError("every operation requires bounded evidence references")
        allowed = {
            "confirm": set(),
            "relabel": {"class_id"},
            "geometry": {"box"},
            "add": {"class_id", "box"},
            "duplicate": {"duplicate_of"},
            "delete": set(),
            "uncertain": {"label_state"},
        }[action]
        if set(item) - required != allowed:
            raise ValueError("operation fields do not match its action")
        operations.append(
            PatchOperation(
                action=action,
                object_id=_text(item["object_id"]),
                reason=_text(item["reason"]),
                evidence_ids=tuple(_text(value) for value in evidence),
                representation=_text(item["representation"]),
                class_id=_text(item["class_id"]) if "class_id" in item else None,
                box=_box(item["box"]) if "box" in item else None,
                label_state=_text(item["label_state"]) if "label_state" in item else None,
                duplicate_of=_text(item["duplicate_of"]) if "duplicate_of" in item else None,
            )
        )
    regions = data["unresolved_regions"]
    if not isinstance(regions, list) or len(regions) > MAX_OPERATIONS:
        raise ValueError("unresolved regions must be a bounded list")
    return ReviewPatch(
        _text(data["asset_id"]),
        revision,
        tuple(operations),
        data["full_image_inspected"],
        data["output_truncated"],
        tuple(_box(region) for region in regions),
    )


def object_set_hash(revision: AnnotationRevision) -> str:
    objects = [
        {**asdict(item), "box": [float(value) for value in asdict(item.box).values()]}
        for item in sorted(revision.objects, key=lambda item: item.object_id)
    ]
    return hashlib.sha256(
        json.dumps(objects, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class PatchValidation:
    base_revision: int
    base_object_hash: str
    proposal: AnnotationRevision | None
    completeness: CompletenessAssessment | None
    needs_adjudication: bool
    reason: str


def validate_patch(
    context: ReviewContext,
    current: AnnotationRevision,
    raw: str,
    *,
    transport_truncated: bool = False,
) -> PatchValidation:
    patch = decode_patch(raw)
    if patch.asset_id != context.asset.asset_id or patch.expected_revision != current.revision:
        raise ValueError("patch source or expected revision does not match")
    objects = {item.object_id: item for item in current.objects}
    touched: set[str] = set()
    adjudicate = current.approval_state == "human_accepted" or current.review_tier in {
        "human_reviewed",
        "adjudicated",
    }
    for operation in patch.operations:
        if operation.object_id in touched:
            raise ValueError("one operation per object per pass is permitted")
        touched.add(operation.object_id)
        if not set(operation.evidence_ids) <= context.evidence_ids:
            raise ValueError("operation cites unavailable or unapproved evidence")
        if operation.representation != context.representation:
            raise ValueError("operation violates the pinned representation policy")
        if operation.class_id is not None and operation.class_id not in context.class_ids:
            raise ValueError("operation class is absent from the pinned ontology")
        if operation.box is not None:
            operation.box.within(context.asset.width, context.asset.height)
        if operation.action == "add":
            if (
                operation.object_id in objects
                or operation.class_id is None
                or operation.box is None
            ):
                raise ValueError("addition requires a new identity, class and geometry")
            objects[operation.object_id] = DatasetObject(
                operation.object_id, operation.class_id, operation.box
            )
            continue
        if operation.object_id not in objects:
            raise ValueError("operation refers to a missing source object")
        obj = objects[operation.object_id]
        if operation.action == "relabel":
            assert operation.class_id is not None
            objects[obj.object_id] = replace(obj, class_id=operation.class_id)
        elif operation.action == "geometry":
            assert operation.box is not None
            objects[obj.object_id] = replace(obj, box=operation.box)
        elif operation.action == "uncertain":
            if operation.label_state not in ("ambiguous", "unknown", "insufficient_evidence"):
                raise ValueError("uncertainty must preserve an unresolved label state")
            objects[obj.object_id] = replace(
                obj, label_state=cast(LabelState, operation.label_state)
            )
        elif operation.action in {"delete", "duplicate"}:
            if operation.action == "duplicate" and (
                operation.duplicate_of not in objects or operation.duplicate_of == obj.object_id
            ):
                raise ValueError("duplicate target must name a different existing object")
            adjudicate = True
    for region in patch.unresolved_regions:
        region.within(context.asset.width, context.asset.height)
    next_objects = tuple(objects.values())
    for index, left in enumerate(next_objects):
        if left.class_id not in context.class_ids:
            raise ValueError("source object class is absent from the pinned ontology")
        left.box.within(context.asset.width, context.asset.height)
        for right in next_objects[index + 1 :]:
            overlap = left.box.intersect(right.box)
            if overlap and overlap.area / (left.box.area + right.box.area - overlap.area) >= 0.95:
                adjudicate = True
    base_hash = object_set_hash(current)
    if adjudicate:
        return PatchValidation(
            current.revision, base_hash, None, None, True, "adjudication_required"
        )
    truncated = transport_truncated or patch.output_truncated or context.discovery_truncated
    changed = next_objects != current.objects
    complete = (
        not changed
        and not truncated
        and patch.full_image_inspected
        and context.discovery_complete
        and not patch.unresolved_regions
        and all(item.label_state == "identified" for item in next_objects)
    )
    proposal = current.next_revision(
        objects=next_objects,
        expected_revision=current.revision,
        actor_kind="model",
        approval_state="proposed",
        review_tier="machine_verified_no_human_review",
        scope_completeness="exhaustive_for_scope" if complete else "partial",
        training_eligible=False,
        gold_eligible=False,
    )
    assessment = CompletenessAssessment(
        context.ontology_hash,
        proposal.revision,
        "exhaustive_for_scope" if complete else "partial",
        "model",
        output_truncated=truncated,
        unresolved_regions=patch.unresolved_regions,
    )
    return PatchValidation(current.revision, base_hash, proposal, assessment, False, "proposed")


@dataclass(frozen=True, slots=True)
class CorrectionLoop:
    current: AnnotationRevision
    maximum_passes: int = 2
    passes: int = 0
    status: Literal["running", "accepted", "unresolved", "failed"] = "running"
    reason: str = ""
    object_hashes: tuple[str, ...] = ()
    last_validation: PatchValidation | None = None
    scope_key: str = ""

    def __post_init__(self) -> None:
        if type(self.maximum_passes) is not int or not 1 <= self.maximum_passes <= 2:
            raise ValueError("automatic correction is limited to one or two passes")
        if not 0 <= self.passes <= self.maximum_passes:
            raise ValueError("invalid correction pass count")

    def consume(
        self, context: ReviewContext, raw: str, *, transport_truncated: bool = False
    ) -> "CorrectionLoop":
        if self.status != "running" or self.passes >= self.maximum_passes:
            raise ValueError("correction loop is terminal")
        count = self.passes + 1
        if self.scope_key and self.scope_key != context.scope_key():
            return replace(self, passes=count, status="failed", reason="review_context_changed")
        try:
            result = validate_patch(
                context, self.current, raw, transport_truncated=transport_truncated
            )
        except ValueError:
            return replace(self, passes=count, status="failed", reason="invalid_patch")
        if result.needs_adjudication:
            return replace(
                self,
                passes=count,
                status="unresolved",
                reason=result.reason,
                last_validation=result,
            )
        assert result.proposal is not None and result.completeness is not None
        proposal, assessment = result.proposal, result.completeness
        before, after = object_set_hash(self.current), object_set_hash(proposal)
        history = self.object_hashes or (before,)
        status: Literal["running", "accepted", "unresolved", "failed"] = "running"
        reason = "another_pass_required"
        if assessment.output_truncated:
            status, reason = "unresolved", "truncated_output"
        elif after != before and after in history:
            status, reason = "unresolved", "oscillating_corrections"
        elif assessment.valid_for(context.ontology_hash, proposal.revision):
            status, reason = "accepted", "machine_evidence_only"
        elif count == self.maximum_passes:
            status, reason = "unresolved", "pass_limit"
        return replace(
            self,
            current=proposal,
            passes=count,
            status=status,
            reason=reason,
            object_hashes=(*history, after),
            last_validation=result,
            scope_key=context.scope_key(),
        )
