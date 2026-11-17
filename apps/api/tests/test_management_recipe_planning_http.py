"""Recipe-plan read composition only; persisted read cases run in Actions."""

from dataclasses import dataclass
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy import create_engine

from labelloop_api.management_codegen import contract_app, render_client
from labelloop_api.management_operations import CanonicalManagementOperations
from labelloop_api.management_ports import ManagementFailure
from labelloop_api.management_reads import CanonicalManagementReads


@dataclass
class Actor:
    id: str = "human"
    kind: str = "human"
    role: str = "reviewer"

    def permits(self, permission):
        return self.kind == "human" and (
            permission == "review" or (permission == "admin" and self.role == "admin")
        )


ARGUMENTS = ("p", "a", "full-letterbox-320-v1", "plan-1", None, None, None, None)


class RecipePlanHTTPWiringTests(TestCase):
    def setUp(self):
        self.engine = create_engine("postgresql+psycopg://")
        self.addCleanup(self.engine.dispose)
        self.service = CanonicalManagementOperations(self.engine, Mock(), uuid4())
        self.actor = Actor()

    def test_delegates_to_existing_canonical_reads(self):
        with patch.object(
            CanonicalManagementReads, "read_recipe_plan", return_value="result"
        ) as call:
            self.assertEqual(self.service.read_recipe_plan(self.actor, *ARGUMENTS), "result")
            call.assert_called_once_with(self.actor, *ARGUMENTS)

    def test_partial_crop_and_nonhuman_refuse_before_database_io(self):
        reads = CanonicalManagementReads(self.engine, uuid4())
        with self.assertRaises(ManagementFailure) as caught:
            reads.read_recipe_plan(
                self.actor, "p", "a", "full-letterbox-320-v1", "plan-1", 0, None, None, None
            )
        self.assertEqual(caught.exception.error.detail, "invalid_request")
        with self.assertRaises(ManagementFailure) as caught:
            reads.read_recipe_plan(
                self.actor, "p", "a", "full-letterbox-320-v1", "plan-1", -1, 0, 10, 10
            )
        self.assertEqual(caught.exception.error.detail, "invalid_request")
        with self.assertRaises(ManagementFailure) as caught:
            reads.read_recipe_plan(Actor(kind="service"), *ARGUMENTS)
        self.assertEqual(caught.exception.error.detail, "permission_denied")

    def test_schema_and_client_expose_only_the_write_free_read(self):
        spec = contract_app().openapi()
        path = spec["paths"]["/projects/{project_id}/assets/{asset_id}/recipe-plans/{recipe_id}"]
        self.assertEqual(set(path), {"get"})
        self.assertEqual(path["get"]["operationId"], "readRecipePlan")
        parameters = {item["name"] for item in path["get"]["parameters"]}
        self.assertEqual(
            parameters,
            {
                "project_id",
                "asset_id",
                "recipe_id",
                "plan_id",
                "crop_x",
                "crop_y",
                "crop_width",
                "crop_height",
            },
        )
        schema = spec["components"]["schemas"]["RecipePlanRead"]
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("plan_sha256", schema["properties"])
        self.assertIn("readRecipePlan(", render_client(spec))
