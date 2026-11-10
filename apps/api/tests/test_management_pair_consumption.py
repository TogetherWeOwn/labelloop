"""Receipt/current-input checks; persisted transaction semantics run in Actions."""

import json
from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace as Row
from unittest import TestCase
from uuid import uuid4

from labelloop_api.management_contracts import InitialSourceGrouping, Resource, SourcePairResolve
from labelloop_api.management_grouping_publication import constrained_evidence
from labelloop_api.management_pair_consumption import consume_initial_pair_decisions
from labelloop_api.management_ports import ManagementFailure, MutationContext
from labelloop_core.grouping import ALGORITHM, AssetEvidence, GroupingThresholds, group_sources


def receipt(project, workspace, operation, command, evidence):
    context = MutationContext.bind(Row(id="human"), operation, operation, str(project), command)
    return Row(
        id=uuid4(),
        event_type="management." + operation,
        aggregate_id=str(project),
        expected_revision=command.expected_revision,
        actor_kind="human",
        actor_id="human",
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


def fixture(decision="unrelated", *, automatic=False):
    project, workspace = uuid4(), uuid4()
    assets = [Row(id=uuid4(), asset_key=key) for key in ("a", "b", "c")]
    sources = [
        AssetEvidence("a", "a" * 64, 0, (), {}, {}),
        AssetEvidence("b", ("a" if automatic else "b") * 64, 511, (), {}, {}),
        AssetEvidence("c", "c" * 64, 2**64 - 1, (), {}, {}),
    ]
    _, _, reviews = group_sources(sources)
    command = InitialSourceGrouping(
        expected_revision=8,
        ontology_id="ontology",
        source_policy_sha256="a" * 64,
        annotation_revisions={a.asset_key: 0 for a in assets},
    )
    pins = {
        "ontology_id": command.ontology_id,
        "source_policy_sha256": command.source_policy_sha256,
        "annotation_revisions": command.annotation_revisions,
        "sources": [asdict(s) for s in sources],
        "source_groups": {a.asset_key: a.asset_key for a in assets},
        "persisted_relations": [],
        "toolchain": {"pillow": "fixture"},
    }
    pair = Row(
        id=uuid4(),
        left_asset_id=assets[0].id,
        right_asset_id=assets[1].id,
        metric="phash",
        value="9",
        algorithm_version=ALGORITHM,
        review_state=decision,
    )
    discovery = receipt(
        project,
        workspace,
        "discover_initial_source_pairs",
        command,
        deepcopy(
            {
                **pins,
                "thresholds": asdict(GroupingThresholds()),
                "pending_pairs": [{"id": str(pair.id), **asdict(reviews[0])}],
            }
        ),
    )
    resolve = SourcePairResolve(
        expected_revision=9, pair_id=str(pair.id), decision=decision, reason="Reviewed source pair"
    )
    evidence = {
        "pair_id": str(pair.id),
        "left_asset_id": str(pair.left_asset_id),
        "right_asset_id": str(pair.right_asset_id),
        "metric": pair.metric,
        "value": pair.value,
        "algorithm_version": ALGORITHM,
        "decision": decision,
        "reason": resolve.reason,
    }
    resolved = receipt(project, workspace, "resolve_source_pair", resolve, evidence)
    if decision == "related":
        pins["persisted_relations"].append(
            {
                "id": str(uuid4()),
                "left_asset_id": "a",
                "right_asset_id": "b",
                "reason": "phash",
                "evidence": json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                "algorithm_version": ALGORITHM,
                "embedding_model": None,
            }
        )
    return [project, workspace, assets, pins, [pair], [discovery, resolved], reviews], sources


class PairConsumptionTests(TestCase):
    def test_current_attributed_decisions_preserve_conservative_edges(self):
        for decision in ("related", "unrelated"):
            for automatic in (True, False):
                with self.subTest(decision=decision, automatic=automatic):
                    args, sources = fixture(decision, automatic=automatic)
                    before = deepcopy(args[3])
                    consumed = consume_initial_pair_decisions(*args)
                    self.assertEqual(consumed[0]["decision"], decision)
                    self.assertEqual(consumed[0]["receipt_revision"], 10)
                    edges = [("a", "b")] if decision == "related" else []
                    constrained, _ = constrained_evidence(
                        sources, args[3]["source_groups"], edges, reviewed=args[6]
                    )
                    self.assertEqual(
                        constrained[0].metadata == constrained[1].metadata,
                        decision == "related" or automatic,
                    )
                    self.assertEqual(args[3], before)

    def test_pending_unknown_and_mutated_pair_states_refuse(self):
        for field, value in (
            ("review_state", "pending"),
            ("review_state", "bogus"),
            ("value", "10"),
            ("metric", "embedding_cosine"),
            ("algorithm_version", "old"),
            ("left_asset_id", uuid4()),
            ("right_asset_id", uuid4()),
        ):
            with self.subTest(field=field):
                args, _ = fixture()
                setattr(args[4][0], field, value)
                with self.assertRaises(ManagementFailure):
                    consume_initial_pair_decisions(*args)

    def test_missing_duplicate_and_foreign_receipts_refuse(self):
        for index in (0, 1):
            for change in ("missing", "duplicate", "foreign", "worker", "actor", "revision"):
                with self.subTest(index=index, change=change):
                    args, _ = fixture()
                    row = args[5][index]
                    if change == "missing":
                        args[5].pop(index)
                    elif change == "duplicate":
                        args[5].append(deepcopy(row))
                    elif change == "foreign":
                        row.payload["workspace_id"] = str(uuid4())
                    elif change == "worker":
                        row.actor_kind = "model"
                    elif change == "actor":
                        row.actor_id = "other"
                    else:
                        row.expected_revision -= 1
                    with self.assertRaises(ManagementFailure):
                        consume_initial_pair_decisions(*args)

    def test_receipt_bodies_are_checked_not_just_row_state(self):
        for index in (0, 1):
            for field, value in (
                ("fingerprint", "f" * 64),
                ("aggregate_revision", True),
                ("result", {}),
                ("idempotency_key", ""),
                ("evidence", {}),
            ):
                with self.subTest(index=index, field=field):
                    args, _ = fixture()
                    args[5][index].payload[field] = value
                    with self.assertRaises(ManagementFailure):
                        consume_initial_pair_decisions(*args)

    def test_changed_current_source_annotation_policy_or_toolchain_refuses(self):
        for key, value in (
            ("annotation_revisions", {"a": 1}),
            ("ontology_id", "next"),
            ("source_policy_sha256", "b" * 64),
            ("sources", []),
            ("source_groups", {}),
            ("toolchain", {"pillow": "next"}),
        ):
            with self.subTest(key=key):
                args, _ = fixture()
                args[3][key] = value
                with self.assertRaises(ManagementFailure):
                    consume_initial_pair_decisions(*args)

    def test_new_or_missing_current_band_refuses(self):
        args, _ = fixture()
        args[6] = []
        with self.assertRaises(ManagementFailure):
            consume_initial_pair_decisions(*args)
        args, _ = fixture()
        args[6].append(replace(args[6][0], right_asset_id="c"))
        with self.assertRaises(ManagementFailure):
            consume_initial_pair_decisions(*args)

    def test_related_requires_its_exact_persisted_edge(self):
        for change in ("missing", "body", "endpoints"):
            with self.subTest(change=change):
                args, _ = fixture("related")
                relations = args[3]["persisted_relations"]
                if change == "missing":
                    relations.clear()
                elif change == "body":
                    relations[0]["evidence"] = "{}"
                else:
                    relations[0]["right_asset_id"] = "c"
                with self.assertRaises(ManagementFailure):
                    consume_initial_pair_decisions(*args)

    def test_no_pairs_never_dismisses_an_observed_review(self):
        args, _ = fixture()
        args[4] = []
        with self.assertRaises(ManagementFailure):
            consume_initial_pair_decisions(*args)

    def test_unreviewed_core_constraints_still_refuse(self):
        args, sources = fixture()
        with self.assertRaises(ManagementFailure):
            constrained_evidence(sources, args[3]["source_groups"], [])
