"""Discovery composition only; persisted shared-session cases run in Actions."""

from dataclasses import dataclass, replace
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy import create_engine

from labelloop_api.management_codegen import contract_app, render_client
from labelloop_api.management_contracts import InitialSourceGrouping
from labelloop_api.management_grouping_publication import CanonicalInitialGrouping
from labelloop_api.management_operations import CanonicalManagementOperations
from labelloop_api.management_ports import ManagementFailure, MutationContext


@dataclass
class Actor:
    id: str = "human"
    kind: str = "human"
    role: str = "admin"

    def permits(self, permission):
        return self.kind == "human" and (
            permission == "review" or (permission == "admin" and self.role == "admin")
        )


class PairHTTPWiringTests(TestCase):
    def setUp(self):
        self.engine = create_engine("postgresql+psycopg://")
        self.addCleanup(self.engine.dispose)
        self.objects = Mock()
        self.service = CanonicalManagementOperations(
            self.engine, Mock(), uuid4(), grouping_objects=self.objects
        )
        self.actor = Actor()
        self.payload = InitialSourceGrouping(
            expected_revision=3,
            ontology_id="ontology",
            source_policy_sha256="a" * 64,
            annotation_revisions={"a": 0, "b": 1},
        )
        self.context = MutationContext.bind(
            self.actor, "discovery", "discover_initial_source_pairs", "p", self.payload
        )

    def test_delegates_to_existing_publisher_with_explicit_reader(self):
        with patch.object(CanonicalInitialGrouping, "discover", return_value="result") as call:
            self.assertEqual(
                self.service.discover_initial_source_pairs(self.actor, self.context, self.payload),
                "result",
            )
            call.assert_called_once_with(self.actor, self.context, self.payload)
        self.objects.read.assert_not_called()

    def test_missing_reader_never_falls_back_to_upload_store(self):
        with self.assertRaisesRegex(ManagementFailure, "management_unavailable"):
            replace(self.service, grouping_objects=None).discover_initial_source_pairs(
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
                with patch.object(CanonicalInitialGrouping, "discover") as call:
                    with self.assertRaises(ManagementFailure):
                        self.service.discover_initial_source_pairs(actor, context, self.payload)
                    call.assert_not_called()
        self.objects.read.assert_not_called()

    def test_schema_and_client_reuse_strict_source_pin_contract(self):
        spec = contract_app().openapi()
        operation = spec["paths"]["/projects/{project_id}/source-pairs/discover"]["post"]
        self.assertEqual(operation["operationId"], "discoverInitialSourcePairs")
        self.assertEqual(
            operation["requestBody"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/InitialSourceGrouping",
        )
        self.assertFalse(
            spec["components"]["schemas"]["InitialSourceGrouping"]["additionalProperties"]
        )
        self.assertIn("discoverInitialSourcePairs(", render_client(spec))
