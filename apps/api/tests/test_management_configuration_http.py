"""Configuration port wiring only; persisted HTTP acceptance runs in Actions."""

from dataclasses import dataclass, replace
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy import create_engine

from labelloop_api.management_canonical import CanonicalManagementMutations
from labelloop_api.management_configuration import (
    CanonicalConfigurationSnapshots,
    ConfigurationTrainingBinding,
)
from labelloop_api.management_configuration_release import CanonicalLocalConfigurationRelease
from labelloop_api.management_contracts import (
    ConfigurationSnapshotPin,
    ConfigurationSnapshotPublish,
    LocalConfigurationPublish,
    ReleaseFreeze,
)
from labelloop_api.management_operations import CanonicalManagementOperations
from labelloop_api.management_ports import ManagementFailure, MutationContext
from labelloop_api.management_reads import CanonicalManagementReads
from labelloop_api.management_training import ApprovedTrainingProfile


@dataclass
class Actor:
    id: str = "human"
    kind: str = "human"
    role: str = "admin"

    def permits(self, permission):
        return self.kind == "human" and (
            permission == "review" or (permission == "admin" and self.role == "admin")
        )


class ConfigurationHTTPWiringTests(TestCase):
    def setUp(self):
        self.engine = create_engine("postgresql+psycopg://")
        self.addCleanup(self.engine.dispose)
        self.project = uuid4()
        self.profile = ApprovedTrainingProfile(
            self.project, "baseline", 1, "full-letterbox-320-v1", "a" * 64
        )
        self.service = CanonicalManagementOperations(
            self.engine, Mock(), uuid4(), profiles=(self.profile,)
        )
        self.pin = ConfigurationSnapshotPin(version="v1", snapshot_sha256="b" * 64)
        self.actor = Actor()

    def bound(self):
        return replace(
            self.service,
            configurations=(ConfigurationTrainingBinding(self.project, "v1", "b" * 64),),
        )

    def test_snapshot_delegation_uses_only_owned_server_profiles(self):
        payload = ConfigurationSnapshotPublish(
            expected_revision=5,
            version="v1",
            setup_summary_sha256="c" * 64,
            reason="Reviewed snapshot",
        )
        context = MutationContext.bind(
            self.actor, "snapshot", "publish_configuration_snapshot", str(self.project), payload
        )
        with patch.object(
            CanonicalConfigurationSnapshots, "publish", return_value="result"
        ) as call:
            self.assertEqual(
                self.service.publish_configuration_snapshot(self.actor, context, payload), "result"
            )
            call.assert_called_once_with(self.actor, context, payload)
        snapshots = self.service._snapshots(str(self.project))
        self.assertIs(snapshots.mutations, self.service)
        self.assertEqual(snapshots.profiles, (self.profile,))
        with self.assertRaisesRegex(ManagementFailure, "management_unavailable"):
            self.service._snapshots(str(uuid4()))

    def test_local_publication_requires_server_binding_not_request_pin(self):
        payload = LocalConfigurationPublish(
            expected_revision=6, configuration=self.pin, reason="Local preparation"
        )
        context = MutationContext.bind(
            self.actor, "local", "publish_local_configuration", str(self.project), payload
        )
        with self.assertRaisesRegex(ManagementFailure, "management_unavailable"):
            self.service.publish_local_configuration(self.actor, context, payload)
        with patch.object(
            CanonicalLocalConfigurationRelease, "publish", return_value="result"
        ) as call:
            self.assertEqual(
                self.bound().publish_local_configuration(self.actor, context, payload), "result"
            )
            call.assert_called_once_with(self.actor, context, payload)

    def test_freeze_uses_bound_adapter_or_unchanged_canonical_guard(self):
        payload = ReleaseFreeze(
            expected_revision=7,
            ontology_id="ontology",
            assignment_version="assignment",
            annotation_revisions={"a": 1},
        )
        context = MutationContext.bind(
            self.actor, "freeze", "freeze_release", str(self.project), payload
        )
        for service, adapter in (
            (self.service, CanonicalManagementMutations),
            (self.bound(), CanonicalLocalConfigurationRelease),
        ):
            with (
                self.subTest(adapter=adapter),
                patch.object(adapter, "freeze_release", return_value="result") as call,
            ):
                self.assertEqual(service.freeze_release(self.actor, context, payload), "result")
                call.assert_called_once_with(self.actor, context, payload)

    def test_freeze_plan_uses_same_server_binding_without_implicit_clearance(self):
        with patch.object(
            CanonicalManagementReads, "read_freeze_plan", return_value="unbound"
        ) as call:
            self.assertEqual(
                self.service.read_freeze_plan(self.actor, str(self.project)), "unbound"
            )
            call.assert_called_once_with(self.actor, str(self.project))
        with patch.object(
            CanonicalLocalConfigurationRelease, "read_freeze_plan", return_value="bound"
        ) as call:
            self.assertEqual(self.bound().read_freeze_plan(self.actor, str(self.project)), "bound")
            call.assert_called_once_with(self.actor)
        local = self.bound()._local_configuration(str(self.project))
        self.assertEqual(local.pin, self.pin)

    def test_summary_projects_verified_identity_not_internal_snapshot(self):
        snapshots = self.service._snapshots(str(self.project))
        with patch.object(
            CanonicalConfigurationSnapshots,
            "_read",
            return_value=(
                {
                    "setup_summary_sha256": "c" * 64,
                    "training_profiles": [{"id": "baseline"}],
                    "grouping": [{"private": "source lineage"}],
                },
                self.pin,
            ),
        ) as call:
            summary = snapshots.read_summary(self.actor, "v1")
            call.assert_called_once_with(self.actor, version="v1")
        self.assertEqual(
            summary.model_dump(mode="json"),
            {
                "project_id": str(self.project),
                "configuration": self.pin.model_dump(mode="json"),
                "setup_summary_sha256": "c" * 64,
                "training_profile_ids": ["baseline"],
                "runtime_binding": None,
                "qualification": "not_assessed",
            },
        )

    def test_unauthorized_summary_and_freeze_plan_refuse_before_adapter_access(self):
        for actor in (Actor(kind="service", role="ml_worker"), Actor(id="")):
            with (
                self.subTest(actor=actor),
                patch.object(CanonicalConfigurationSnapshots, "_read") as read,
            ):
                with self.assertRaisesRegex(ManagementFailure, "permission_denied"):
                    self.service.read_configuration_snapshot(actor, str(self.project), "v1")
                read.assert_not_called()
                with self.assertRaisesRegex(ManagementFailure, "permission_denied"):
                    self.bound().read_freeze_plan(actor, str(self.project))
