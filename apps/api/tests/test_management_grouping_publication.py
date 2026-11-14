"""Pure constraints and internal pins; database publication is tested in Actions."""

import io
import json
import random
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from unittest import TestCase

from PIL import Image
from pydantic import ValidationError

from labelloop_api.management_grouping_evidence import collect_source_grouping_evidence
from labelloop_api.management_grouping_publication import (
    InitialSourceGrouping,
    constrained_evidence,
)
from labelloop_api.management_ports import ManagementFailure
from labelloop_compiler.ingest import process_image
from labelloop_core.grouping import AssetEvidence, assign_splits, phash_grayscale


def observations():
    return [
        AssetEvidence(key, str(index) * 64, phash, (), {}, {"target": index + 1})
        for index, (key, phash) in enumerate(
            [("a", 0), ("b", 2**64 - 1), ("c", int("a" * 16, 16)), ("d", int("5" * 16, 16))]
        )
    ]


class InitialGroupingTests(TestCase):
    def test_real_interface_rasters_reproduce_declared_fixture_partition(self):
        root = Path(__file__).resolve().parents[3] / "fixtures"
        document = json.loads((root / "golden/cc0-interface.labels.json").read_text())
        evidence = []
        for item in document["images"]:
            asset, data = process_image(
                (root / "cc0-interface/images" / item["file"]).read_bytes(),
                asset_id=item["asset_id"],
                project_id="cc0-interface",
            )
            observed = collect_source_grouping_evidence(
                asset,
                data,
                metadata={},
                class_counts=dict(Counter(obj["class_id"] for obj in item["objects"])),
            )
            self.assertEqual(asset.sha256, item["normalized_sha256"])
            # Golden pHashes describe pre-normalization uploads. Publication
            # must observe the canonical JPEG, not reuse those cached values.
            with Image.open(io.BytesIO(data)) as normalized:
                self.assertEqual(observed.phash, phash_grayscale(normalized))
            self.assertEqual(observed.class_counts, item["evidence"]["class_counts"])
            evidence.append(observed)
        expected = document["split_assignment"]
        constrained, relations = constrained_evidence(evidence, expected["group_ids"], [])
        assignment = assign_splits(constrained)
        self.assertEqual(len(evidence), 16)
        self.assertEqual(len(set(assignment.group_ids.values())), 15)
        self.assertEqual(assignment.group_ids, expected["group_ids"])
        self.assertEqual(assignment.asset_splits, expected["asset_splits"])
        self.assertTrue(relations)

    def test_actual_normalized_fixture_rasters_have_independent_groups(self):
        evidence = []
        for index in range(4):
            image = Image.frombytes("RGB", (48, 32), random.Random(index).randbytes(48 * 32 * 3))
            output = io.BytesIO()
            image.save(output, format="PNG")
            asset, data = process_image(
                output.getvalue(), asset_id=str(index), project_id="fixture"
            )
            evidence.append(
                collect_source_grouping_evidence(asset, data, metadata={}, class_counts={})
            )
        constrained, _ = constrained_evidence(
            evidence, {"0": "shared", "1": "shared", "2": "second", "3": "third"}, [("1", "2")]
        )
        self.assertEqual(len(set(assign_splits(constrained).group_ids.values())), 2)

    def test_transitive_declarations_and_edges_feed_core_stratification_without_mutation(self):
        original = observations()
        before = [asdict(row) for row in original]
        constrained, relations = constrained_evidence(
            original, {"a": "one", "b": "one", "c": "two", "d": "three"}, [("b", "c")]
        )
        assignment = assign_splits(constrained)
        self.assertEqual(relations, [])
        self.assertEqual(len(set(assignment.group_ids.values())), 2)
        self.assertEqual(len({assignment.group_ids[key] for key in ("a", "b", "c")}), 1)
        self.assertEqual(set(assignment.asset_splits.values()), {"train", "val"})
        self.assertEqual([asdict(row) for row in original], before)
        self.assertEqual(
            [row.class_counts for row in original], [row.class_counts for row in constrained]
        )

    def test_order_does_not_change_assignment(self):
        evidence = observations()
        declarations = {row.asset_id: row.asset_id for row in evidence}
        left, _ = constrained_evidence(evidence, declarations, [("c", "b")])
        right, _ = constrained_evidence(list(reversed(evidence)), declarations, [("b", "c")])
        self.assertEqual(assign_splits(left), assign_splits(right))

    def test_exact_hash_and_automatic_phash_constraints_remain(self):
        evidence = observations()
        evidence[1] = replace(evidence[1], sha256=evidence[0].sha256)
        evidence[2] = replace(evidence[2], phash=1)
        constrained, relations = constrained_evidence(
            evidence, {row.asset_id: row.asset_id for row in evidence}, []
        )
        assignment = assign_splits(constrained)
        self.assertEqual(len({assignment.group_ids[key] for key in ("a", "b", "c")}), 1)
        self.assertEqual({row.reason for row in relations}, {"exact_hash", "phash"})

    def test_borderline_phash_is_not_silently_merged_or_separated(self):
        evidence = observations()
        evidence[1] = replace(evidence[1], phash=2**9 - 1)
        with self.assertRaisesRegex(ManagementFailure, "missing_evidence"):
            constrained_evidence(evidence, {row.asset_id: row.asset_id for row in evidence}, [])

    def test_unknown_and_self_edges_refuse(self):
        evidence = observations()
        for edges in ([("a", "foreign")], [("foreign", "a")], [("a", "a")]):
            with self.subTest(edges=edges), self.assertRaises(ManagementFailure):
                constrained_evidence(
                    evidence, {row.asset_id: row.asset_id for row in evidence}, edges
                )

    def test_missing_extra_and_empty_declarations_refuse(self):
        evidence = observations()
        declarations = {row.asset_id: row.asset_id for row in evidence}
        for changes in ({"a": ""}, {"foreign": "x"}):
            with self.subTest(changes=changes), self.assertRaises(ManagementFailure):
                constrained_evidence(evidence, {**declarations, **changes}, [])
        declarations.pop("a")
        with self.assertRaises(ManagementFailure):
            constrained_evidence(evidence, declarations, [])

    def test_single_conservative_group_cannot_manufacture_independent_holdout(self):
        evidence = observations()
        constrained, _ = constrained_evidence(
            evidence, {row.asset_id: "one" for row in evidence}, []
        )
        with self.assertRaisesRegex(ValueError, "independent"):
            assign_splits(constrained)

    def test_internal_request_has_no_split_threshold_seed_or_human_authority(self):
        values = {
            "expected_revision": 3,
            "ontology_id": "ontology",
            "source_policy_sha256": "a" * 64,
            "annotation_revisions": {"a": 0, "b": 1},
        }
        self.assertEqual(InitialSourceGrouping(**values).annotation_revisions["a"], 0)
        for field, value in (
            ("split", "test"),
            ("seed", 4),
            ("thresholds", {}),
            ("actor_id", "forged"),
            ("training_eligible", True),
            ("expected_revision", True),
            ("annotation_revisions", {"a": True, "b": 1}),
            ("annotation_revisions", {"a": 1}),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                InitialSourceGrouping(**{**values, field: value})
