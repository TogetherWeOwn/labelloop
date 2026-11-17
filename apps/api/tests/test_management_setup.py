"""Receipt-history invalidation contracts; no database or object I/O."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from pydantic import ValidationError

from labelloop_api.management_contracts import SetupStepApprove
from labelloop_api.management_ports import ManagementFailure
from labelloop_api.management_setup import draft_sha256, read_setup_approvals


class SetupApprovalTests(TestCase):
    def inputs(self, invalidation):
        draft = {"scope": "Visible packages"}
        digest = draft_sha256(draft)
        project = SimpleNamespace(
            id="project",
            workspace_id="workspace",
            access_policy={
                "management_scope_draft": draft,
                "management_setup_approvals": {
                    "scope": {"draft_sha256": digest, "receipt_revision": 4}
                },
            },
        )
        receipt = SimpleNamespace(
            expected_revision=3,
            aggregate_id="project",
            event_type="management.approve_setup_step",
            actor_kind="human",
            actor_id="admin",
            payload={
                "workspace_id": "workspace",
                "aggregate_revision": 4,
                "result": {
                    "id": "project",
                    "project_id": "project",
                    "kind": "project",
                    "state": "draft",
                    "revision": 4,
                },
                "evidence": {
                    "step": "scope",
                    "draft_sha256": digest,
                    "reason": "Reviewed scope",
                },
            },
        )
        connection = Mock()
        connection.receipt = receipt
        connection.execute.side_effect = [
            Mock(scalar_one=Mock(return_value=invalidation)),
            Mock(all=Mock(return_value=[receipt])),
        ]
        return connection, project

    def test_history_query_includes_anchor_publication_as_an_invalidator(self):
        connection, project = self.inputs(2)
        self.assertEqual(read_setup_approvals(connection, project, "workspace"), ["scope"])
        query = connection.execute.call_args_list[0].args[0]
        parameters = query.compile().params
        event_types = [value for value in parameters.values() if isinstance(value, list)]
        self.assertEqual(len(event_types), 1)
        self.assertIn("management.publish_anchor_pack", event_types[0])
        self.assertIn("project", parameters.values())
        self.assertIn("workspace", parameters.values())
        self.assertIn("human", parameters.values())

    def test_approval_requires_intact_attribution_revision_and_result(self):
        for field, value in [
            ("actor_id", ""),
            ("actor_kind", "worker"),
            ("aggregate_id", "foreign"),
            ("event_type", "management.save_scope_draft"),
            ("expected_revision", True),
        ]:
            with self.subTest(field=field):
                connection, project = self.inputs(2)
                receipt = connection.receipt
                setattr(receipt, field, value)
                with self.assertRaisesRegex(ManagementFailure, "missing_evidence"):
                    read_setup_approvals(connection, project, "workspace")
        for section, field, value in [
            (None, "workspace_id", "foreign"),
            (None, "aggregate_revision", 5),
            (None, "aggregate_revision", True),
            ("result", "id", "foreign"),
            ("result", "project_id", "foreign"),
            ("result", "kind", "asset"),
            ("result", "state", "frozen"),
            ("result", "revision", 5),
            ("evidence", "step", "guide"),
            ("evidence", "reason", ""),
        ]:
            with self.subTest(section=section, field=field):
                connection, project = self.inputs(2)
                receipt = connection.receipt
                target = receipt.payload if section is None else receipt.payload[section]
                target[field] = value
                with self.assertRaisesRegex(ManagementFailure, "missing_evidence"):
                    read_setup_approvals(connection, project, "workspace")

    def test_duplicate_latest_approval_is_not_arbitrarily_selected(self):
        connection, project = self.inputs(2)
        connection.execute.side_effect = [
            Mock(scalar_one=Mock(return_value=2)),
            Mock(all=Mock(return_value=[connection.receipt, connection.receipt])),
        ]
        with self.assertRaisesRegex(ManagementFailure, "missing_evidence"):
            read_setup_approvals(connection, project, "workspace")

    def test_older_distinct_approval_history_does_not_obscure_latest_receipt(self):
        connection, project = self.inputs(2)
        connection.execute.side_effect = [
            Mock(scalar_one=Mock(return_value=2)),
            Mock(all=Mock(return_value=[connection.receipt, SimpleNamespace(expected_revision=1)])),
        ]
        self.assertEqual(read_setup_approvals(connection, project, "workspace"), ["scope"])

    def test_malformed_receipt_has_a_typed_missing_evidence_refusal(self):
        for field, value in [("evidence", None), ("result", {}), ("result", None)]:
            with self.subTest(field=field, value=value):
                connection, project = self.inputs(2)
                connection.receipt.payload[field] = value
                with self.assertRaisesRegex(ManagementFailure, "missing_evidence"):
                    read_setup_approvals(connection, project, "workspace")

    def test_older_approval_cannot_survive_a_later_invalidation(self):
        connection, project = self.inputs(4)
        with self.assertRaisesRegex(ManagementFailure, "missing_evidence"):
            read_setup_approvals(connection, project, "workspace")

    def test_approval_after_invalidation_remains_valid(self):
        connection, project = self.inputs(2)
        self.assertEqual(read_setup_approvals(connection, project, "workspace"), ["scope"])

    def test_existing_approval_serialization_preserves_historical_retry_identity(self):
        body = {
            "expected_revision": 2,
            "step": "scope",
            "draft_sha256": "a" * 64,
            "reason": "Reviewed intent",
        }
        self.assertEqual(SetupStepApprove.model_validate(body).model_dump(mode="json"), body)

    def test_evidence_requires_complete_explicit_pack_and_other_steps_refuse_it(self):
        body = {
            "expected_revision": 2,
            "step": "evidence",
            "draft_sha256": "a" * 64,
            "reason": "Reviewed anchors",
        }
        pin = {
            "pack_id": "anchors",
            "version": "v1",
            "ontology_id": "ontology",
            "assignment_version": "assignment",
        }
        self.assertEqual(
            SetupStepApprove.model_validate({**body, "anchor_pack": pin}).anchor_pack.pack_id,
            "anchors",
        )
        for invalid in [
            body,
            {**body, "step": "scope", "anchor_pack": pin},
            {**body, "anchor_pack": {**pin, "approved": True}},
            *[
                {**body, "anchor_pack": {k: v for k, v in pin.items() if k != field}}
                for field in pin
            ],
        ]:
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                SetupStepApprove.model_validate(invalid)

    def test_evidence_approval_rechecks_exact_receipt_pins_in_each_read(self):
        connection, project = self.inputs(2)
        digest = "b" * 64
        pin = {
            "pack_id": "anchors",
            "version": "v1",
            "ontology_id": "ontology",
            "assignment_version": "assignment",
        }
        project.access_policy["management_setup_approvals"] = {
            "evidence": {"draft_sha256": digest, "receipt_revision": 4}
        }
        connection.receipt.payload["evidence"] = {
            "step": "evidence",
            "draft_sha256": digest,
            "anchor_pack": pin,
            "reason": "Reviewed anchors",
        }
        with patch(
            "labelloop_api.management_setup.evidence_pack_digest", return_value=digest
        ) as verifier:
            self.assertEqual(read_setup_approvals(connection, project, "workspace"), ["evidence"])
        self.assertEqual(verifier.call_args.args[:3], (connection, project, "workspace"))
        self.assertEqual(verifier.call_args.args[3].model_dump(mode="json"), pin)
        self.assertEqual(verifier.call_args.kwargs, {"lock_sources": False})

    def summary_inputs(self):
        from labelloop_api.management_setup import approved_setup_summary_sha256

        approvals = ["scope", "guide", "classes", "sources", "evidence"]
        pins = {step: str(index) * 64 for index, step in enumerate(approvals[:-1], 1)}
        project = SimpleNamespace(
            id="project",
            workspace_id="workspace",
            access_policy={
                "management_setup_approvals": {
                    step: {"draft_sha256": str(index) * 64, "receipt_revision": index + 10}
                    for index, step in enumerate(approvals, 1)
                }
            },
        )
        versions = {"ontology": "2", "ontology_id": "ontology", "assignment": "assignment"}
        return approved_setup_summary_sha256, project, approvals, pins, versions

    def test_summary_requires_all_five_verified_steps(self):
        summary, project, approvals, pins, versions = self.summary_inputs()
        for step in approvals:
            with self.subTest(step=step):
                self.assertIsNone(
                    summary(project, [item for item in approvals if item != step], pins, versions)
                )

    def test_summary_is_deterministic_and_binds_receipts_versions_and_ownership(self):
        from copy import deepcopy

        summary, project, approvals, pins, versions = self.summary_inputs()
        digest = summary(project, approvals, pins, versions)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(
            summary(
                project, list(reversed(approvals)), pins, dict(reversed(list(versions.items())))
            ),
            digest,
        )
        for field in ("id", "workspace_id"):
            changed = deepcopy(project)
            setattr(changed, field, "other")
            self.assertNotEqual(summary(changed, approvals, pins, versions), digest)
        for field in versions:
            self.assertNotEqual(
                summary(project, approvals, pins, {**versions, field: "other"}), digest
            )
        for step in approvals:
            changed = deepcopy(project)
            changed.access_policy["management_setup_approvals"][step]["receipt_revision"] += 1
            self.assertNotEqual(summary(changed, approvals, pins, versions), digest)
        changed = deepcopy(project)
        changed.access_policy["management_setup_approvals"]["evidence"]["draft_sha256"] = "a" * 64
        self.assertNotEqual(summary(changed, approvals, pins, versions), digest)
        self.assertNotIn("setup_summary_sha256", project.access_policy)

    def test_complete_summary_refuses_missing_or_inconsistent_validated_facts(self):
        summary, project, approvals, pins, versions = self.summary_inputs()
        for step in pins:
            for bad in (
                {key: value for key, value in pins.items() if key != step},
                {**pins, step: "a" * 64},
            ):
                with (
                    self.subTest(step=step, bad=bad),
                    self.assertRaisesRegex(ManagementFailure, "missing_evidence"),
                ):
                    summary(project, approvals, bad, versions)
        for field in versions:
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ManagementFailure, "missing_evidence"),
            ):
                summary(project, approvals, pins, {k: v for k, v in versions.items() if k != field})

    def test_evidence_digest_uses_current_verified_pack_not_client_scope_claims(self):
        from labelloop_api.management_setup import evidence_pack_digest

        pin = {
            "pack_id": "anchors",
            "version": "v1",
            "ontology_id": "ontology",
            "assignment_version": "assignment",
        }
        pack = SimpleNamespace(
            scope=SimpleNamespace(
                ontology=SimpleNamespace(ontology_id="ontology"),
                assignment=SimpleNamespace(assignment_version="assignment"),
            )
        )
        connection, project = self.inputs(2)
        with (
            patch(
                "labelloop_api.management_reference_pack.read_current_anchor_pack",
                return_value=pack,
            ) as verifier,
            patch(
                "labelloop_api.management_reference_pack.pack_snapshot", return_value=({}, "c" * 64)
            ),
        ):
            self.assertEqual(
                evidence_pack_digest(connection, project, "workspace", pin, lock_sources=True),
                "c" * 64,
            )
            verifier.assert_called_once_with(
                connection, project, "workspace", "anchors", "v1", lock_sources=True
            )
            with self.assertRaisesRegex(ManagementFailure, "missing_evidence"):
                evidence_pack_digest(
                    connection, project, "workspace", {**pin, "ontology_id": "foreign"}
                )
