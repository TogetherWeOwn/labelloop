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
            access_policy={
                "management_scope_draft": draft,
                "management_setup_approvals": {
                    "scope": {"draft_sha256": digest, "receipt_revision": 4}
                },
            },
        )
        receipt = SimpleNamespace(
            expected_revision=3, payload={"evidence": {"draft_sha256": digest}}
        )
        connection = Mock()
        connection.execute.side_effect = [
            Mock(scalar_one=Mock(return_value=invalidation)),
            Mock(one_or_none=Mock(return_value=receipt)),
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
        connection.execute.side_effect = [
            Mock(scalar_one=Mock(return_value=2)),
            Mock(
                one_or_none=Mock(
                    return_value=SimpleNamespace(
                        expected_revision=3,
                        payload={"evidence": {"draft_sha256": digest, "anchor_pack": pin}},
                    )
                )
            ),
        ]
        with patch(
            "labelloop_api.management_setup.evidence_pack_digest", return_value=digest
        ) as verifier:
            self.assertEqual(read_setup_approvals(connection, project, "workspace"), ["evidence"])
        verifier.assert_called_once_with(connection, project, "workspace", pin, lock_sources=False)

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
