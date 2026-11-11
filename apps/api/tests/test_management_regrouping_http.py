"""Regrouping composition only; persisted shared-session cases run in Actions."""

from dataclasses import dataclass, replace
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy import create_engine

from labelloop_api.management_codegen import contract_app, render_client
from labelloop_api.management_contracts import SourceRegrouping
from labelloop_api.management_operations import CanonicalManagementOperations
from labelloop_api.management_ports import ManagementFailure, MutationContext
from labelloop_api.management_regrouping import CanonicalSourceRegrouping


@dataclass
class Actor:
    id: str = "human"
    kind: str = "human"
    role: str = "admin"

    def permits(self, permission):
        return self.kind == "human" and (
            permission == "review" or (permission == "admin" and self.role == "admin")
        )


class RegroupingHTTPWiringTests(TestCase):
    def setUp(self):
        self.engine = create_engine("postgresql+psycopg://")
        self.addCleanup(self.engine.dispose)
        self.objects = Mock()
        self.service = CanonicalManagementOperations(
            self.engine, Mock(), uuid4(), grouping_objects=self.objects
        )
        self.actor = Actor()
        self.payload = SourceRegrouping(
            expected_revision=3,
            ontology_id="ontology",
            source_policy_sha256="a" * 64,
            annotation_revisions={"a": 0, "b": 1},
            expected_assignment_version="assignment-1",
        )
        self.context = MutationContext.bind(
            self.actor, "regroup", "publish_source_regrouping", "p", self.payload
        )

    def test_delegates_to_existing_regrouping_adapter_with_explicit_reader(self):
        with patch.object(CanonicalSourceRegrouping, "publish", return_value="result") as call:
            self.assertEqual(
                self.service.publish_source_regrouping(self.actor, self.context, self.payload),
                "result",
            )
            call.assert_called_once_with(self.actor, self.context, self.payload)
        self.objects.read.assert_not_called()

    def test_missing_reader_never_falls_back_to_upload_store(self):
        with self.assertRaisesRegex(ManagementFailure, "management_unavailable"):
            replace(self.service, grouping_objects=None).publish_source_regrouping(
                self.actor, self.context, self.payload
            )
        self.service.store.read.assert_not_called()

    def test_context_and_principal_refuse_before_adapter_or_object_io(self):
        for actor, context in (
            (Actor(role="reviewer"), self.context),
            (Actor(kind="service"), self.context),
            (self.actor, replace(self.context, target_id="foreign")),
            (self.actor, replace(self.context, operation="publish_initial_grouping")),
        ):
            with self.subTest(actor=actor, context=context):
                with patch.object(CanonicalSourceRegrouping, "publish") as call:
                    with self.assertRaises(ManagementFailure):
                        self.service.publish_source_regrouping(actor, context, self.payload)
                    call.assert_not_called()
        self.objects.read.assert_not_called()

    def test_schema_and_client_reuse_strict_regrouping_contract(self):
        spec = contract_app().openapi()
        operation = spec["paths"]["/projects/{project_id}/source-assignments/regroup"]["post"]
        self.assertEqual(operation["operationId"], "publishSourceRegrouping")
        self.assertEqual(
            operation["requestBody"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/SourceRegrouping",
        )
        schema = spec["components"]["schemas"]["SourceRegrouping"]
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("expected_assignment_version", schema["properties"])
        self.assertIn("publishSourceRegrouping(", render_client(spec))
