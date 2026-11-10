"""Verify first-grouping decisions against discovery and current canonical evidence.

No decision deletes an automatic edge. Assignment/reference history is rejected by
our caller before these checks; this is not regrouping or holdout quarantine.
"""

import json
from dataclasses import asdict
from types import SimpleNamespace

from pydantic import ValidationError

from labelloop_core.grouping import ALGORITHM, GroupingThresholds

from .management_contracts import (
    InitialSourceGrouping,
    ManagementError,
    Resource,
    SourcePairResolve,
)
from .management_ports import ManagementFailure, MutationContext
from .management_setup import draft_sha256


def reject():
    raise ManagementFailure(ManagementError(detail="missing_evidence"))


def verify_receipt(receipt, project_id, workspace_id, operation, command):
    payload = receipt.payload
    expected = command.expected_revision
    result = Resource(
        id=str(project_id),
        project_id=str(project_id),
        kind="project",
        revision=expected + 1,
        state="draft",
    )
    if (
        receipt.event_type != "management." + operation
        or receipt.aggregate_id != str(project_id)
        or receipt.actor_kind != "human"
        or type(receipt.actor_id) is not str
        or not receipt.actor_id
        or type(receipt.expected_revision) is not int
        or receipt.expected_revision != expected
        or payload.get("workspace_id") != str(workspace_id)
        or type(payload.get("idempotency_key")) is not str
        or not payload["idempotency_key"]
        or type(payload.get("aggregate_revision")) is not int
        or payload["aggregate_revision"] != expected + 1
        or payload.get("result") != result.model_dump(mode="json")
        or payload.get("fingerprint")
        != MutationContext.bind(
            SimpleNamespace(id=receipt.actor_id),
            payload["idempotency_key"],
            operation,
            str(project_id),
            command,
        ).fingerprint
    ):
        reject()


def consume_initial_pair_decisions(
    project_id, workspace_id, assets, pins, pairs, receipts, reviews
):
    """Return exact current reviewed suggestions, or refuse the whole publication."""
    if not pairs:
        if reviews or any(
            r.event_type != "management.discover_initial_source_pairs"
            or type(r.payload) is not dict
            or type(r.payload.get("evidence")) is not dict
            or r.payload["evidence"].get("pending_pairs") != []
            for r in receipts
        ):
            reject()
        return []
    by_id = {str(row.id): row.asset_key for row in assets}
    discoveries = [
        r for r in receipts if r.event_type == "management.discover_initial_source_pairs"
    ]
    decisions = [r for r in receipts if r.event_type == "management.resolve_source_pair"]
    if len(discoveries) != 1 or len(decisions) != len(pairs):
        reject()
    discovery = discoveries[0]
    try:
        command = InitialSourceGrouping(
            expected_revision=discovery.expected_revision,
            ontology_id=pins["ontology_id"],
            source_policy_sha256=pins["source_policy_sha256"],
            annotation_revisions=pins["annotation_revisions"],
        )
        verify_receipt(
            discovery, project_id, workspace_id, "discover_initial_source_pairs", command
        )
        saved = discovery.payload["evidence"]
        if saved["thresholds"] != asdict(GroupingThresholds()):
            reject()
        # Related decisions add conservative edges after discovery. All other
        # input pins (including normalized bytes, annotations and toolchain) match.
        for key, value in pins.items():
            if key != "persisted_relations" and draft_sha256(saved[key]) != draft_sha256(value):
                reject()
        current_relations = pins["persisted_relations"]
        relation_hashes = {draft_sha256(row) for row in current_relations}
        if any(draft_sha256(row) not in relation_hashes for row in saved["persisted_relations"]):
            reject()
        related_edges = {
            (
                row["left_asset_id"],
                row["right_asset_id"],
                row["reason"],
                row["algorithm_version"],
                row["evidence"],
            )
            for row in current_relations
        }
        by_pair = {r.payload["evidence"]["pair_id"]: r for r in decisions}
        if len(by_pair) != len(decisions):
            reject()
        discovered = {row["id"]: row for row in saved["pending_pairs"]}
        if (
            len(discovered) != len(saved["pending_pairs"])
            or len(discovered) != len(pairs)
            or set(discovered) != {str(row.id) for row in pairs}
        ):
            reject()
        current_reviews = {json.dumps(asdict(row), sort_keys=True) for row in reviews}
        if len(current_reviews) != len(discovered) or current_reviews != {
            json.dumps({k: v for k, v in row.items() if k != "id"}, sort_keys=True)
            for row in discovered.values()
        }:
            reject()
        consumed = []
        for pair in pairs:
            original = discovered[str(pair.id)]
            if (
                pair.review_state not in {"related", "unrelated"}
                or pair.algorithm_version != ALGORITHM
                or original
                != {
                    "id": str(pair.id),
                    "left_asset_id": by_id[str(pair.left_asset_id)],
                    "right_asset_id": by_id[str(pair.right_asset_id)],
                    "metric": pair.metric,
                    "value": int(pair.value),
                }
                or pair.metric != "phash"
            ):
                reject()
            receipt = by_pair[str(pair.id)]
            evidence = receipt.payload["evidence"]
            decision = SourcePairResolve(
                expected_revision=receipt.expected_revision,
                pair_id=str(pair.id),
                decision=pair.review_state,
                reason=evidence["reason"],
            )
            verify_receipt(receipt, project_id, workspace_id, "resolve_source_pair", decision)
            if receipt.expected_revision <= discovery.expected_revision or evidence != {
                "pair_id": str(pair.id),
                "left_asset_id": str(pair.left_asset_id),
                "right_asset_id": str(pair.right_asset_id),
                "metric": pair.metric,
                "value": pair.value,
                "algorithm_version": pair.algorithm_version,
                "decision": pair.review_state,
                "reason": decision.reason,
            }:
                reject()
            if (
                pair.review_state == "related"
                and (
                    original["left_asset_id"],
                    original["right_asset_id"],
                    pair.metric,
                    pair.algorithm_version,
                    json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                )
                not in related_edges
            ):
                reject()
            consumed.append(
                {
                    **evidence,
                    "receipt_id": str(receipt.id),
                    "receipt_revision": receipt.expected_revision + 1,
                }
            )
        return sorted(consumed, key=lambda row: row["pair_id"])
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, ValidationError):
        reject()
