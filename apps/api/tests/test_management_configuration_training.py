"""Configuration consumption contracts; real transaction refusals run in Actions."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import psycopg

from labelloop_api.management_configuration import (
    ConfigurationTrainingBinding,
    training_configuration,
)
from labelloop_api.management_contracts import TrainingStart
from labelloop_api.management_ports import ManagementFailure, MutationContext
from labelloop_api.management_training import ApprovedTrainingProfile, CanonicalManagementTraining


class ConfigurationTrainingTests(TestCase):
    def setUp(self):
        self.project_id = uuid4()
        self.profile = ApprovedTrainingProfile(
            self.project_id, "baseline", 1, "full-letterbox-320-v1", "1" * 64
        )
        self.binding = ConfigurationTrainingBinding(self.project_id, "v1", "2" * 64)
        self.service = MagicMock()
        self.training = CanonicalManagementTraining(
            self.service, Mock(return_value="job"), (self.profile,), (self.binding,)
        )
        self.manifest = {
            "ontology_id": "ontology-1",
            "split_assignment_version": "assignment-1",
            "file_hashes": {"class-map.json": "3" * 64},
            "samples": [
                {"asset_id": "source", "source_sha256": "4" * 64, "group_id": "g", "split": "val"}
            ],
        }

    def test_bindings_are_immutable_and_validate_explicit_identity_and_digest(self):
        with self.assertRaises(FrozenInstanceError):
            self.binding.version = "changed"
        for changes in ({"project_id": "bad"}, {"version": ""}, {"snapshot_sha256": "bad"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.binding, **changes)
        pin = self.binding.pin()
        with self.assertRaises(ValueError):
            pin.version = "changed"
        pin = pin.model_copy(update={"version": "changed"})
        self.assertEqual(pin.version, "changed")
        self.assertEqual(self.binding.pin().version, "v1")

    def test_configuration_mode_requires_one_immutable_binding_per_profile_project(self):
        for bindings in (
            [],
            None,
            (object(),),
            (self.binding, self.binding),
            (replace(self.binding, project_id=uuid4()),),
            (self.binding,) * 1001,
        ):
            with self.subTest(bindings=type(bindings)), self.assertRaises(ValueError):
                replace(self.training, configurations=bindings)
        with self.assertRaises(ValueError):
            replace(self.training, profiles=())
        self.assertEqual(replace(self.training, configurations=()).configurations, ())

    def consume(self, manifest):
        from labelloop_api.management_reads import CanonicalManagementReads

        connection = Mock()
        connection.execute.return_value.scalar_one.return_value = 12
        owned = SimpleNamespace(id=self.project_id)
        snapshot = {
            "versions": {"ontology_id": "ontology-1", "assignment": "assignment-1"},
            "grouping": [
                {"asset_id": "source", "sha256": "4" * 64, "group_id": "g", "split": "val"}
            ],
            "setup_summary_sha256": "5" * 64,
        }
        with (
            patch.object(CanonicalManagementReads, "__post_init__", return_value=None),
            patch(
                "labelloop_api.management_configuration.read_current_configuration",
                return_value=snapshot,
            ) as verify,
        ):
            result = training_configuration(
                connection, owned, uuid4(), (self.profile,), self.binding, manifest
            )
            self.assertEqual(verify.call_args.kwargs, {"lock_sources": True})
            self.assertEqual(verify.call_args.args[3], 12)
            self.assertEqual(verify.call_args.args[5], self.binding.pin())
        return result

    def test_locked_consumer_returns_only_verified_version_and_setup_provenance(self):
        self.assertEqual(
            self.consume(self.manifest),
            {
                "project_id": str(self.project_id),
                "version": "v1",
                "snapshot_sha256": "2" * 64,
                "setup_summary_sha256": "5" * 64,
            },
        )

    def test_locked_consumer_refuses_foreign_or_changed_compiler_membership(self):
        for path, value in (
            (("ontology_id",), "other"),
            (("split_assignment_version",), "other"),
            (("samples",), []),
            (("samples", 0, "asset_id"), "other"),
            (("samples", 0, "source_sha256"), "6" * 64),
            (("samples", 0, "group_id"), "other"),
            (("samples", 0, "split"), "test"),
        ):
            manifest = deepcopy(self.manifest)
            target = manifest
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(ManagementFailure, "missing_evidence"),
            ):
                self.consume(manifest)
        with self.assertRaisesRegex(ManagementFailure, "missing_evidence"):
            training_configuration(
                Mock(), SimpleNamespace(id=uuid4()), uuid4(), (), self.binding, {}
            )

    def submit(self, *, refusal=False):
        actor = SimpleNamespace(id="admin")
        variant_id = str(uuid4())
        payload = TrainingStart(expected_revision=1, training_profile_id="baseline")
        context = MutationContext.bind(actor, "train", "start_training", variant_id, payload)
        connection = self.service.engine.begin.return_value.__enter__.return_value
        connection.connection.driver_connection = Mock(spec=psycopg.Connection)
        variant = SimpleNamespace(
            project_id=self.project_id, recipe_key=self.profile.recipe_id, release_key="r1"
        )
        connection.execute.side_effect = [
            Mock(one_or_none=lambda: variant),
            Mock(first=lambda: None),
        ]
        self.service.lock_project.return_value = SimpleNamespace(
            id=self.project_id, project_key="p"
        )
        self.service.replay.return_value = None
        provenance = {"project_id": str(self.project_id), **self.binding.pin().model_dump()}
        with (
            patch(
                "labelloop_api.management_training.verified_variant",
                return_value=(self.manifest, "7" * 64, "compile"),
            ),
            patch(
                "labelloop_api.management_configuration.training_configuration",
                return_value=provenance,
            ) as verify,
        ):
            if refusal:
                from labelloop_api.management_canonical import refuse

                verify.side_effect = lambda *args: refuse("missing_evidence")
                with self.assertRaisesRegex(ManagementFailure, "missing_evidence"):
                    self.training.start_training(actor, context, payload)
                self.training.enqueue.assert_not_called()
                self.service.record.assert_not_called()
            else:

                def enqueue(*args, **kwargs):
                    verify.assert_called_once()
                    self.assertEqual(args[4]["configuration"], provenance)
                    return "job"

                self.training.enqueue.side_effect = enqueue
                result = self.training.start_training(actor, context, payload)
                self.assertEqual(result.job_id, "job")
                self.assertEqual(
                    self.service.record.call_args.args[5]["inputs"]["configuration"], provenance
                )
                self.assertIs(verify.call_args.args[0], connection)
                self.assertIs(verify.call_args.args[1], self.service.lock_project.return_value)
                self.assertEqual(verify.call_args.args[4], self.binding)

    def test_submission_checks_locked_configuration_before_enqueue_and_records_provenance(self):
        self.submit()

    def test_stale_configuration_refuses_before_queue_or_receipt_mutation(self):
        self.submit(refusal=True)

    def test_opt_in_facade_forwards_bindings_without_database_or_object_io(self):
        from sqlalchemy import create_engine

        from labelloop_api.management_operations import CanonicalManagementOperations

        engine = create_engine("postgresql+psycopg://")
        try:
            facade = CanonicalManagementOperations(
                engine, Mock(), uuid4(), profiles=(self.profile,), configurations=(self.binding,)
            )
            self.assertEqual(facade._training().configurations, (self.binding,))
        finally:
            engine.dispose()
