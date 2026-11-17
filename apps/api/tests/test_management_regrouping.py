"""Integrator mapping checks; persisted transaction semantics run in Actions."""

from types import SimpleNamespace as Row
from unittest import TestCase
from uuid import uuid4

from labelloop_api.management_contracts import Resource, SourcePairResolve, SourceRegrouping
from labelloop_api.management_ports import ManagementFailure, MutationContext
from labelloop_api.management_regrouping import (
    declared_group_decisions,
    trusted_observations,
    verified_pair_decisions,
)
from labelloop_core.grouping import ALGORITHM, AssetEvidence


def receipt(project, workspace, command, evidence, *, actor_id="human"):
    context = MutationContext.bind(
        Row(id=actor_id), "resolve_source_pair", "resolve_source_pair", str(project), command
    )
    return Row(
        id=uuid4(),
        event_type="management.resolve_source_pair",
        aggregate_id=str(project),
        expected_revision=command.expected_revision,
        actor_kind="human",
        actor_id=actor_id,
        payload={
            "workspace_id": str(workspace),
            "idempotency_key": context.idempotency_key,
            "fingerprint": context.fingerprint,
            "aggregate_revision": command.expected_revision + 1,
            "result": Resource(
                id=str(project),
                project_id=str(project),
                kind="project",
                revision=command.expected_revision + 1,
                state="draft",
            ).model_dump(mode="json"),
            "evidence": evidence,
        },
    )


def fixture(decision="related"):
    project, workspace = uuid4(), uuid4()
    assets = [Row(id=uuid4(), asset_key=key) for key in ("a", "b")]
    pair = Row(
        id=uuid4(),
        left_asset_id=assets[0].id,
        right_asset_id=assets[1].id,
        metric="phash",
        value="9",
        algorithm_version=ALGORITHM,
        review_state=decision,
    )
    command = SourcePairResolve(
        expected_revision=7, pair_id=str(pair.id), decision=decision, reason="Same capture"
    )
    evidence = {
        "pair_id": str(pair.id),
        "left_asset_id": str(pair.left_asset_id),
        "right_asset_id": str(pair.right_asset_id),
        "metric": pair.metric,
        "value": pair.value,
        "algorithm_version": ALGORITHM,
        "decision": decision,
        "reason": command.reason,
    }
    return project, workspace, assets, pair, receipt(project, workspace, command, evidence)


class RegroupingMappingTests(TestCase):
    def test_verified_human_decisions_map_with_actor_and_reason(self):
        project, workspace, assets, pair, saved = fixture()
        mapped, consumed = verified_pair_decisions(project, workspace, assets, [pair], [saved])
        self.assertEqual(len(mapped), 1)
        self.assertEqual(mapped[0].decision, "same_group")
        self.assertEqual(mapped[0].actor_kind, "human")
        self.assertEqual(mapped[0].actor_id, "human")
        self.assertEqual(mapped[0].rationale, "Same capture")
        self.assertEqual(consumed[0]["receipt_id"], str(saved.id))

    def test_unrelated_decision_maps_to_different_group(self):
        project, workspace, assets, pair, saved = fixture("unrelated")
        mapped, _ = verified_pair_decisions(project, workspace, assets, [pair], [saved])
        self.assertEqual(mapped[0].decision, "different_group")

    def test_missing_duplicate_or_pending_decisions_refuse(self):
        project, workspace, assets, pair, saved = fixture()
        for pairs, receipts in (
            ([pair], []),
            ([pair], [saved, saved]),
            ([Row(**{**vars(pair), "review_state": "pending"})], [saved]),
        ):
            with self.assertRaises(ManagementFailure):
                verified_pair_decisions(project, workspace, assets, pairs, receipts)

    def test_mutated_receipt_evidence_refuses(self):
        project, workspace, assets, pair, saved = fixture()
        saved.payload["evidence"] = {**saved.payload["evidence"], "reason": "forged"}
        with self.assertRaises(ManagementFailure):
            verified_pair_decisions(project, workspace, assets, [pair], [saved])

    def test_declared_groups_chain_deterministically_as_system_decisions(self):
        decisions = declared_group_decisions({"b": "shared", "a": "shared", "c": "solo"})
        self.assertEqual(len(decisions), 1)
        self.assertEqual((decisions[0].left_asset_id, decisions[0].right_asset_id), ("a", "b"))
        self.assertEqual(decisions[0].actor_kind, "system")
        self.assertIn("declared source group at ingest: shared", decisions[0].rationale)

    def test_observations_forward_no_unnamed_embedding(self):
        clean = AssetEvidence("a", "a" * 64, 1, (), {"session": "s"}, {"cat": 2})
        mapped = trusted_observations([clean])
        self.assertEqual(mapped[0].metadata, {"session": "s"})
        embedded = AssetEvidence("a", "a" * 64, 1, (0.5,), {}, {})
        with self.assertRaises(ManagementFailure):
            trusted_observations([embedded])

    def test_regrouping_contract_requires_assignment_pin(self):
        payload = SourceRegrouping(
            expected_revision=3,
            ontology_id="ontology",
            source_policy_sha256="a" * 64,
            annotation_revisions={"a": 1, "b": 0},
            expected_assignment_version="initial-abc",
        )
        self.assertEqual(payload.expected_assignment_version, "initial-abc")
