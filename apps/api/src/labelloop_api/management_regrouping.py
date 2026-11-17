"""Canonical regrouping publication over the source-group lifecycle leaf.

Bound as ``publishSourceRegrouping`` through the explicit trusted reader
and shared human-admin/session/CSRF boundary; no default mounting. The
leaf recomputes relations from trusted observations plus pair decisions
and never reads management receipts, so this integrator maps verified
canonical evidence into its DTOs under the existing management transaction
authority (admin principal, project/retry locks, revision and replay):

* raster evidence pins are the shared initial-grouping preparation with the
  history guards inverted - assignment/reference history is the pinned
  input, an unreviewed pending pair still refuses;
* each resolved borderline pair must carry exactly one verified human
  ``management.resolve_source_pair`` receipt; it becomes a human leaf
  decision (``related`` -> ``same_group``, ``unrelated`` ->
  ``different_group``) with the receipt reason and actor preserved;
* declared ingest ``source_group`` values are not core metadata keys, so
  each declaration component becomes a chain of derived system
  ``same_group`` decisions naming their ingest-receipt derivation. They are
  deterministic and hash-deduplicated by the leaf on later regroups;
* before applying, the leaf proposal must keep every declaration, related
  decision and persisted relation inside one group, and every borderline
  pair it reports must already carry a verified decision. A regroup that
  would split the conservative closure, publish over an unreviewed band, or
  reproduce the pinned version unchanged refuses instead of writing.

Leaf publication events keep their ``system`` actor kind with the human
actor ID preserved; the final attributed audit is the management receipt
recorded in the same outer transaction, so either both commit or neither.
Quarantined releases are reported in the receipt evidence, never relabelled.
"""

from dataclasses import asdict, dataclass

from sqlalchemy import select, update

from labelloop_core.grouping import ALGORITHM, GroupingThresholds

from .management_canonical import CanonicalManagementMutations
from .management_compile_worker import CompilationObjects
from .management_contracts import ManagementError, Resource, SourcePairResolve, SourceRegrouping
from .management_grouping_publication import MAX_RELATIONS, prepare_initial_grouping
from .management_pair_consumption import verify_receipt
from .management_ports import ManagementFailure
from .schema import borderline_review, canonical_event, project
from .source_grouping_lifecycle import (
    LifecyclePins,
    PairDecision,
    StalePinError,
    TrustedObservation,
    apply_regroup,
    propose_regroup,
)

DECLARED_GROUP_ACTOR = "management-declared-source-group"


def reject(code):
    raise ManagementFailure(ManagementError(detail=code))


def declared_group_decisions(declarations):
    """Chain each declared ingest source group into derived system decisions."""
    by_group = {}
    for key, group in declarations.items():
        by_group.setdefault(group, []).append(key)
    decisions = []
    for group, keys in sorted(by_group.items()):
        ordered = sorted(keys)
        for left, right in zip(ordered, ordered[1:], strict=False):
            decisions.append(
                PairDecision(
                    left_asset_id=left,
                    right_asset_id=right,
                    decision="same_group",
                    rationale=f"declared source group at ingest: {group}",
                    actor_kind="system",
                    actor_id=DECLARED_GROUP_ACTOR,
                )
            )
    return decisions


def verified_pair_decisions(project_id, workspace_id, assets, pairs, receipts):
    """Map resolved pairs to leaf decisions only under verified human receipts."""
    by_id = {str(row.id): row.asset_key for row in assets}
    decisions = [row for row in receipts if row.event_type == "management.resolve_source_pair"]
    if len(decisions) != len(pairs):
        reject("missing_evidence")
    by_pair = {}
    for receipt in decisions:
        try:
            pair_id = receipt.payload["evidence"]["pair_id"]
        except (AttributeError, KeyError, TypeError):
            reject("missing_evidence")
        if pair_id in by_pair:
            reject("missing_evidence")
        by_pair[pair_id] = receipt
    mapped = []
    consumed = []
    for pair in pairs:
        if (
            pair.review_state not in {"related", "unrelated"}
            or pair.metric != "phash"
            or pair.algorithm_version != ALGORITHM
            or str(pair.left_asset_id) not in by_id
            or str(pair.right_asset_id) not in by_id
        ):
            reject("missing_evidence")
        receipt = by_pair.get(str(pair.id))
        if receipt is None:
            reject("missing_evidence")
        try:
            evidence = receipt.payload["evidence"]
            command = SourcePairResolve(
                expected_revision=receipt.expected_revision,
                pair_id=str(pair.id),
                decision=pair.review_state,
                reason=evidence["reason"],
            )
            verify_receipt(receipt, project_id, workspace_id, "resolve_source_pair", command)
            if evidence != {
                "pair_id": str(pair.id),
                "left_asset_id": str(pair.left_asset_id),
                "right_asset_id": str(pair.right_asset_id),
                "metric": pair.metric,
                "value": pair.value,
                "algorithm_version": pair.algorithm_version,
                "decision": pair.review_state,
                "reason": command.reason,
            }:
                reject("missing_evidence")
        except ManagementFailure:
            raise
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
            reject("missing_evidence")
        mapped.append(
            PairDecision(
                left_asset_id=by_id[str(pair.left_asset_id)],
                right_asset_id=by_id[str(pair.right_asset_id)],
                decision="same_group" if pair.review_state == "related" else "different_group",
                rationale=command.reason,
                actor_kind="human",
                actor_id=receipt.actor_id,
            )
        )
        consumed.append(
            {
                **evidence,
                "receipt_id": str(receipt.id),
                "receipt_revision": receipt.expected_revision + 1,
            }
        )
    return mapped, sorted(consumed, key=lambda row: row["pair_id"])


def trusted_observations(evidence):
    observations = []
    for row in evidence:
        # Management raster evidence carries no approved pinned embedding
        # model; an embedding observation can never be forwarded unnamed.
        if row.embedding:
            reject("missing_evidence")
        try:
            observations.append(
                TrustedObservation(
                    asset_id=row.asset_id,
                    sha256=row.sha256,
                    phash=row.phash,
                    metadata=dict(row.metadata),
                    class_counts=dict(row.class_counts),
                )
            )
        except (TypeError, ValueError):
            reject("missing_evidence")
    return observations


def publish_regrouping(connection, project_row, workspace_id, store, payload, actor_id):
    assets, evidence, declarations, edges, pins = prepare_initial_grouping(
        connection, project_row, workspace_id, store, payload, regrouping=True
    )
    ids = [row.id for row in assets]
    pairs = connection.execute(
        select(borderline_review)
        .where(
            borderline_review.c.left_asset_id.in_(ids) | borderline_review.c.right_asset_id.in_(ids)
        )
        .order_by(borderline_review.c.id)
        .limit(MAX_RELATIONS + 1)
    ).all()
    receipts = connection.execute(
        select(canonical_event)
        .where(
            canonical_event.c.aggregate_id == str(project_row.id),
            canonical_event.c.event_type == "management.resolve_source_pair",
        )
        .limit(MAX_RELATIONS + 1)
    ).all()
    if len(pairs) > MAX_RELATIONS or len(receipts) > MAX_RELATIONS:
        reject("management_unavailable")
    human_decisions, consumed = verified_pair_decisions(
        project_row.id, workspace_id, assets, pairs, receipts
    )
    decisions = [*human_decisions, *declared_group_decisions(declarations)]
    observations = trusted_observations(evidence)
    # The leaf omits zero-revision assets from its latest-revision map, while
    # the shared preparation has already verified every pin including zeros.
    leaf_pins = LifecyclePins(
        expected_assignment_version=payload.expected_assignment_version,
        expected_annotation_revisions={
            key: revision for key, revision in payload.annotation_revisions.items() if revision != 0
        },
    )
    try:
        proposal = propose_regroup(
            connection,
            project_id=project_row.id,
            observations=observations,
            decisions=decisions,
            pins=leaf_pins,
        )
    except StalePinError:
        reject("revision_conflict")
    except (TypeError, ValueError):
        reject("missing_evidence")
    decided = {frozenset((row.left_asset_id, row.right_asset_id)) for row in human_decisions}
    if any(
        frozenset((row.left_asset_id, row.right_asset_id)) not in decided
        for row in proposal.borderline
    ):
        reject("missing_evidence")
    groups = proposal.groups
    required = [
        *edges,
        *(
            (row.left_asset_id, row.right_asset_id)
            for row in decisions
            if row.decision == "same_group"
        ),
    ]
    if any(
        groups.get(left) is None or groups.get(left) != groups.get(right)
        for left, right in required
    ):
        reject("missing_evidence")
    if proposal.new_assignment_version is None or proposal.assignment is None:
        reject("missing_evidence")
    if proposal.new_assignment_version == payload.expected_assignment_version:
        # No new assignment exists to publish; never record a no-op receipt
        # or clear setup approvals for unchanged grouping.
        reject("revision_conflict")
    try:
        published = apply_regroup(
            connection,
            project_id=project_row.id,
            observations=observations,
            decisions=decisions,
            pins=leaf_pins,
            actor_id=actor_id,
        )
    except StalePinError:
        reject("revision_conflict")
    except (TypeError, ValueError):
        reject("missing_evidence")
    if not published.published or published.assignment_version != proposal.new_assignment_version:
        reject("missing_evidence")
    return {
        **pins,
        "previous_assignment_version": payload.expected_assignment_version,
        "assignment_version": published.assignment_version,
        "groups": dict(sorted(published.groups.items())),
        "quarantined_releases": [row.release_key for row in published.quarantined_releases],
        "requires_fresh_holdout": published.requires_fresh_holdout,
        "reviewed_pairs": consumed,
        "declared_group_decisions": len(decisions) - len(human_decisions),
        "thresholds": {**asdict(GroupingThresholds()), "validation_fraction": 0.25},
        "lifecycle_adapter": "source-grouping-lifecycle-v1",
    }


@dataclass(frozen=True)
class CanonicalSourceRegrouping:
    canonical: CanonicalManagementMutations
    objects: CompilationObjects

    def publish(self, actor, context, payload: SourceRegrouping) -> Resource:
        canonical = self.canonical
        canonical.authorize(actor, context, "publish_source_regrouping", context.target_id, payload)
        with canonical.engine.begin() as connection:
            canonical.begin_mutation(connection, context)
            owned = canonical.lock_project(connection, context.target_id)
            if replay := canonical.replay(connection, context):
                return replay
            canonical.check_project_revision(connection, owned.id, payload.expected_revision)
            evidence = publish_regrouping(
                connection, owned, canonical.workspace_id, self.objects, payload, actor.id
            )
            policy = dict(owned.access_policy)
            policy["management_setup_approvals"] = {}
            policy.pop("auto_accept_qualification", None)
            connection.execute(
                update(project).where(project.c.id == owned.id).values(access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            canonical.record(
                connection, context, str(owned.id), payload.expected_revision, result, evidence
            )
            return result
