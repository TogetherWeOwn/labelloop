"""Configuration preparation contracts; no database, object or ML I/O."""

import json
from contextlib import ExitStack
from dataclasses import asdict, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from pydantic import ValidationError

from labelloop_api.management_configuration import ConfigurationSnapshotPublish, profile_snapshots
from labelloop_api.management_setup import draft_sha256
from labelloop_api.management_training import ApprovedTrainingProfile
from labelloop_core.training import InferencePolicy


class ConfigurationSnapshotTests(TestCase):
    def profiles(self):
        project_id = uuid4()
        return project_id, tuple(
            ApprovedTrainingProfile(
                project_id, f"baseline-{side}", 1, f"full-letterbox-{side}-v1", "1" * 64
            )
            for side in (320, 640)
        )

    def test_profiles_pin_actual_recipe_arguments_checkpoint_and_postprocessing(self):
        project_id, profiles = self.profiles()
        snapshots = profile_snapshots(profiles, project_id)
        self.assertEqual(json.loads(json.dumps(snapshots)), snapshots)
        for item, side in zip(snapshots, (320, 640), strict=True):
            self.assertEqual(item["arguments"]["imgsz"], side)
            self.assertEqual(item["recipe"]["output_size"], side)
            self.assertEqual(item["inference_policy"], asdict(InferencePolicy(imgsz=side)))
            self.assertEqual(item["initialization_sha256"], "1" * 64)
            self.assertFalse(item["arguments"]["tile"])
            self.assertFalse(item["arguments"]["rect"])

    def test_profiles_have_canonical_order_and_no_output_aliases(self):
        project_id, profiles = self.profiles()
        first = profile_snapshots(profiles, project_id)
        self.assertEqual(first, profile_snapshots(tuple(reversed(profiles)), project_id))
        first[0]["arguments"]["imgsz"] = 999
        self.assertEqual(profile_snapshots(profiles, project_id)[0]["arguments"]["imgsz"], 320)

    def test_foreign_duplicate_mutable_empty_and_oversized_profiles_refuse(self):
        project_id, profiles = self.profiles()
        for values, target in (
            (profiles, uuid4()),
            (profiles * 2, project_id),
            (list(profiles), project_id),
            ((), project_id),
            (profiles * 17, project_id),
            ((object(),), project_id),
        ):
            with self.subTest(values=type(values), target=target):
                with self.assertRaises(ValueError):
                    profile_snapshots(values, target)

    def test_profile_digests_change_with_checkpoint_arguments_and_postprocessing(self):
        project_id, profiles = self.profiles()
        before = draft_sha256({"profiles": profile_snapshots(profiles, project_id)})
        for changes in (
            {"initialization_sha256": "2" * 64},
            {"epochs": 2},
            {"batch": 4},
            {"seed": 9},
            {"inference_policy": InferencePolicy(imgsz=320, conf=0.5)},
            {"revision": 2},
        ):
            with self.subTest(changes=changes):
                updated = (replace(profiles[0], **changes), profiles[1])
                self.assertNotEqual(
                    before, draft_sha256({"profiles": profile_snapshots(updated, project_id)})
                )

    def prepare(self, *, summary="1" * 64, assets=1, lock_sources=True):
        from labelloop_api.management_configuration import prepare_configuration

        project_id, profiles = self.profiles()
        owned = SimpleNamespace(
            id=project_id,
            workspace_id=uuid4(),
            access_policy={
                "management_setup_approvals": {"scope": {"draft_sha256": "2" * 64}},
                "management_scope_draft": {"scope": "Packages"},
                "management_guide_draft": {"extent": "visible"},
                "management_source_policy_draft": {"metadata_keys": ["session"]},
                "provider_key": "must-not-be-copied",
            },
        )
        detail = SimpleNamespace(
            setup_summary_sha256=summary,
            versions={"assignment": "a1", "ontology_id": "o1"},
            source_permitted_use="Licence-clean fixture",
        )
        source = SimpleNamespace(id="internal", asset_key="source", sha256="3" * 64)
        assignment = SimpleNamespace(
            group_id="g1", split="train", seed=0, algorithm="v1", thresholds={}
        )
        with (
            patch("labelloop_api.management_configuration.read_setup_approvals") as approvals,
            patch(
                "labelloop_api.management_configuration.project_detail", return_value=detail
            ) as projection,
            patch(
                "labelloop_api.management_configuration.load_grouping",
                return_value=([source] * assets, {"internal": assignment}, [], {}),
            ) as grouping,
        ):
            connection = Mock()
            connection.get_isolation_level.return_value = (
                "READ COMMITTED" if lock_sources else "REPEATABLE READ"
            )
            result = prepare_configuration(
                connection,
                owned,
                owned.workspace_id,
                7,
                profile_snapshots(profiles, project_id),
                lock_sources=lock_sources,
            )
            for reader in (approvals, projection, grouping):
                self.assertEqual(reader.call_args.kwargs, {"lock_sources": lock_sources})
        return result, owned

    def test_project_projection_forwards_locked_mutation_context_and_defaults_to_snapshot(self):
        from labelloop_api.management_details import SourceGroupsRead
        from labelloop_api.management_project_reads import project_detail

        owned = SimpleNamespace(
            id=uuid4(),
            scope="Visible packages",
            domain="Packaging",
            access_policy={
                "source_permitted_use": "Licence-clean fixture",
                "external_llm_providers_allowed": [],
                "annotation_guide": "Visible extents",
            },
        )
        ontology = SimpleNamespace(
            ontology_key="ontology-1",
            version=1,
            classes=[{"stable_id": "package", "name": "Package", "definition": "A package"}],
            class_map={"package": 0},
        )
        sources = SourceGroupsRead(
            images=1,
            groups=1,
            metadata_keys=None,
            pending_pairs=[],
            split_counts={"train": 1, "val": 0, "test": 0},
            assignment_version="assignment-1",
        )
        for lock_sources in (False, True):
            with self.subTest(lock_sources=lock_sources), ExitStack() as stack:
                connection = Mock()
                connection.execute.return_value.one_or_none.return_value = ontology
                connection.execute.return_value.all.return_value = [
                    SimpleNamespace(created_at=datetime(2026, 1, 1, tzinfo=UTC))
                ]
                mocks = {
                    name: stack.enter_context(
                        patch(f"labelloop_api.management_project_reads.{name}", return_value=value)
                    )
                    for name, value in {
                        "scope_draft": None,
                        "guide_draft": None,
                        "class_catalog_draft": None,
                        "source_policy_draft": None,
                        "published_catalog_content": None,
                        "read_setup_approvals": [],
                        "read_references": None,
                        "source_groups": sources,
                    }.items()
                }
                # Omit the keyword for normal reads: the default must remain read-only.
                options = {"lock_sources": True} if lock_sources else {}
                detail = project_detail(connection, owned, 7, "workspace", **options)
                self.assertIsNone(detail.setup_summary_sha256)
                for name in ("read_setup_approvals", "read_references"):
                    mocks[name].assert_called_once_with(
                        connection, owned, "workspace", lock_sources=lock_sources
                    )

    def test_preparation_copies_only_named_facts_with_no_runtime_or_quality_grant(self):
        snapshot, owned = self.prepare()
        self.assertEqual(snapshot["qualification"], "not_assessed")
        self.assertIsNone(snapshot["promotion_targets"])
        self.assertIsNone(snapshot["runtime_binding"])
        self.assertFalse(snapshot["automatic_acceptance"])
        self.assertFalse(snapshot["autonomous_spend"])
        self.assertNotIn("provider_key", snapshot)
        self.assertEqual(snapshot["grouping"][0]["asset_id"], "source")
        snapshot["scope"]["scope"] = "changed"
        self.assertEqual(owned.access_policy["management_scope_draft"]["scope"], "Packages")

    def test_preparation_refuses_missing_approved_setup_and_unbounded_sources(self):
        from labelloop_api.management_ports import ManagementFailure

        for kwargs in ({"summary": None}, {"assets": 0}, {"assets": 1001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ManagementFailure):
                self.prepare(**kwargs)

    def receipt(self):
        from labelloop_api.management_configuration import ConfigurationSnapshotPin
        from labelloop_api.management_contracts import Resource
        from labelloop_api.management_ports import MutationContext

        snapshot, owned = self.prepare(lock_sources=False)
        request = ConfigurationSnapshotPublish(
            expected_revision=7,
            version="v1",
            setup_summary_sha256=snapshot["setup_summary_sha256"],
            reason="Reviewed configuration",
        )
        context = MutationContext.bind(
            SimpleNamespace(id="admin"),
            "config",
            "publish_configuration_snapshot",
            str(owned.id),
            request,
        )
        digest = draft_sha256(snapshot)
        receipt = SimpleNamespace(
            event_type="management.publish_configuration_snapshot",
            aggregate_id=str(owned.id),
            actor_kind="human",
            actor_id="admin",
            expected_revision=7,
            payload={
                "workspace_id": str(owned.workspace_id),
                "idempotency_key": "config",
                "fingerprint": context.fingerprint,
                "aggregate_revision": 8,
                "result": Resource(
                    id=str(owned.id),
                    project_id=str(owned.id),
                    kind="project",
                    revision=8,
                    state="draft",
                ).model_dump(mode="json"),
                "evidence": {
                    "version": "v1",
                    "reason": request.reason,
                    "snapshot": snapshot,
                    "snapshot_sha256": digest,
                },
            },
        )
        return (
            receipt,
            owned,
            ConfigurationSnapshotPin(version="v1", snapshot_sha256=digest),
            snapshot,
        )

    def test_receipt_verifier_requires_current_full_snapshot_and_attribution(self):
        from copy import deepcopy

        from labelloop_api.management_configuration import verify_configuration_receipt
        from labelloop_api.management_ports import ManagementFailure

        receipt, owned, pin, snapshot = self.receipt()
        verify_configuration_receipt(receipt, owned, owned.workspace_id, 8, pin, snapshot)
        for field, value in (
            ("actor_kind", "worker"),
            ("actor_id", ""),
            ("actor_id", "other"),
            ("event_type", "management.create_project"),
            ("aggregate_id", str(uuid4())),
            ("expected_revision", True),
            ("expected_revision", 8),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ManagementFailure):
                changed = deepcopy(receipt)
                setattr(changed, field, value)
                verify_configuration_receipt(changed, owned, owned.workspace_id, 8, pin, snapshot)
        for path, value in (
            (("workspace_id",), "foreign"),
            (("aggregate_revision",), True),
            (("fingerprint",), "f" * 64),
            (("idempotency_key",), ""),
            (("result", "state"), "ready"),
            (("result", "revision"), 9),
            (("evidence", "version"), "v2"),
            (("evidence", "reason"), "Changed"),
            (("evidence", "snapshot_sha256"), "f" * 64),
            (("evidence", "snapshot", "automatic_acceptance"), True),
        ):
            changed = deepcopy(receipt)
            target = changed.payload
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(ManagementFailure):
                verify_configuration_receipt(changed, owned, owned.workspace_id, 8, pin, snapshot)
        with self.assertRaises(ManagementFailure):
            verify_configuration_receipt(receipt, owned, owned.workspace_id, 7, pin, snapshot)
        changed = deepcopy(snapshot)
        changed["training_profiles"][0]["seed"] += 1
        with self.assertRaises(ManagementFailure):
            verify_configuration_receipt(receipt, owned, owned.workspace_id, 8, pin, changed)

    def test_current_reader_refuses_missing_duplicate_malformed_and_unbounded_history(self):
        from copy import deepcopy

        from labelloop_api.management_configuration import read_current_configuration
        from labelloop_api.management_ports import ManagementFailure

        receipt, owned, pin, snapshot = self.receipt()
        connection = Mock()
        connection.get_isolation_level.return_value = "REPEATABLE READ"
        malformed = deepcopy(receipt)
        malformed.payload = {}
        for rows in ([], [receipt, receipt], [malformed], [receipt] * 1001):
            connection.execute.return_value.all.return_value = rows
            with self.subTest(count=len(rows)), self.assertRaises(ManagementFailure):
                read_current_configuration(connection, owned, owned.workspace_id, 8, [], pin)
        connection.execute.return_value.all.return_value = [receipt]
        with patch(
            "labelloop_api.management_configuration.prepare_configuration", return_value=snapshot
        ) as prepare:
            self.assertEqual(
                read_current_configuration(connection, owned, owned.workspace_id, 8, [], pin),
                snapshot,
            )
            self.assertEqual(prepare.call_args.kwargs, {"lock_sources": False})
        connection.get_isolation_level.return_value = "READ COMMITTED"
        with self.assertRaisesRegex(ManagementFailure, "management_unavailable"):
            read_current_configuration(connection, owned, owned.workspace_id, 8, [], pin)
        with patch(
            "labelloop_api.management_configuration.prepare_configuration", return_value=snapshot
        ) as prepare:
            self.assertEqual(
                read_current_configuration(
                    connection, owned, owned.workspace_id, 8, [], pin, lock_sources=True
                ),
                snapshot,
            )
            self.assertEqual(prepare.call_args.kwargs, {"lock_sources": True})
        connection.get_isolation_level.return_value = "REPEATABLE READ"
        with self.assertRaisesRegex(ManagementFailure, "management_unavailable"):
            read_current_configuration(
                connection, owned, owned.workspace_id, 8, [], pin, lock_sources=True
            )

    def test_request_never_accepts_runtime_or_quality_authority(self):
        base = dict(
            expected_revision=1, version="v1", setup_summary_sha256="1" * 64, reason="Reviewed"
        )
        for field, value in (
            ("profiles", []),
            ("promotion_targets", {}),
            ("automatic_acceptance", True),
            ("actor_id", "forged"),
            ("workspace_id", "forged"),
            ("runtime_binding", "live"),
            ("expected_revision", True),
            ("setup_summary_sha256", "bad"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    ConfigurationSnapshotPublish.model_validate({**base, field: value})
