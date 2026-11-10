"""Declared source constraints, not inferred identity or split authority."""

import base64
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase

from pydantic import ValidationError

from labelloop_api.management_contracts import AssetIngest
from labelloop_api.management_grouping_publication import constrained_evidence, declared_metadata
from labelloop_api.management_ports import ManagementFailure, MutationContext
from labelloop_core.grouping import AssetEvidence, assign_splits


def upload(**changes):
    return AssetIngest(
        **{
            "expected_revision": 1,
            "original_base64": base64.b64encode(b"fixture").decode(),
            "source_group": "capture",
            **changes,
        }
    )


class SourceMetadataTests(TestCase):
    def test_absent_and_explicit_empty_metadata_preserve_historical_retry_fingerprint(self):
        actor = SimpleNamespace(id="admin")
        original = upload().model_dump(mode="json", exclude={"source_metadata"})
        expected = hashlib.sha256(
            json.dumps(
                [actor.id, "ingest_asset", "project", original],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        for payload in (upload(), upload(source_metadata={})):
            context = MutationContext.bind(actor, "retry", "ingest_asset", "project", payload)
            self.assertEqual(context.fingerprint, expected)
        changed = MutationContext.bind(
            actor, "retry", "ingest_asset", "project", upload(source_metadata={"session": "one"})
        )
        self.assertNotEqual(changed.fingerprint, expected)

    def test_only_bounded_identifier_values_and_supported_keys_are_allowed(self):
        values = {"session": "session-1", "sequence": "sequence.2", "subject": "subject_3"}
        self.assertEqual(upload(source_metadata=values).source_metadata, values)
        for invalid in (
            None,
            [],
            {"class": "target"},
            {"split": "train"},
            {"subject": ""},
            {"subject": " "},
            {"subject": "x" * 129},
            {"subject": 1},
            {"subject": True},
            {"subject": ["target"]},
            {"subject": "../path"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                upload(source_metadata=invalid)

    def test_receipt_metadata_is_validated_against_current_policy_without_mutation(self):
        values = {"session": "one", "sequence": "two"}
        evidence = {"source_metadata": values}
        observed = declared_metadata(evidence, ["session", "sequence", "subject"])
        self.assertEqual(observed, values)
        observed["session"] = "changed"
        self.assertEqual(values["session"], "one")
        self.assertEqual(declared_metadata({}, ["session"]), {})
        for invalid in (None, {"session": ""}, {"subject": "one"}, {"session": 4}):
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(ManagementFailure, "missing_evidence"),
            ):
                declared_metadata({"source_metadata": invalid}, ["session"])

    def test_metadata_relations_are_transitive_with_declarations_and_do_not_select_splits(self):
        values = [
            {"session": "one"},
            {"session": "one", "sequence": "bridge"},
            {"sequence": "bridge"},
            {"subject": "independent"},
        ]
        hashes = [0, 2**64 - 1, int("a" * 16, 16), int("5" * 16, 16)]
        evidence = [
            AssetEvidence(str(index), str(index) * 64, phash, (), metadata, {})
            for index, (metadata, phash) in enumerate(zip(values, hashes, strict=True))
        ]
        declarations = {row.asset_id: row.asset_id for row in evidence}
        constrained, relations = constrained_evidence(evidence, declarations, [])
        assignment = assign_splits(constrained)
        self.assertEqual(len({assignment.group_ids[key] for key in ("0", "1", "2")}), 1)
        self.assertNotEqual(assignment.group_ids["0"], assignment.group_ids["3"])
        self.assertEqual({row.reason for row in relations}, {"metadata"})
        self.assertEqual({row.evidence for row in relations}, {"session", "sequence"})
        self.assertEqual([row.metadata for row in evidence], values)
        reversed_input = [replace(row, metadata=dict(row.metadata)) for row in reversed(evidence)]
        again, _ = constrained_evidence(reversed_input, declarations, [])
        self.assertEqual(assignment, assign_splits(again))
