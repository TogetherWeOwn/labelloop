"""Receipt-history invalidation contracts; no database or object I/O."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

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
