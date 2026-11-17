"""Pure receipt and contract controls; database acceptance runs on Actions."""

from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from pydantic import ValidationError

from labelloop_api.management_configuration import ConfigurationSnapshotPin
from labelloop_api.management_configuration_release import (
    CanonicalLocalConfigurationRelease,
    LocalConfigurationPublish,
    verify_local_publication,
)
from labelloop_api.management_contracts import Resource
from labelloop_api.management_ports import ManagementFailure, MutationContext


class LocalConfigurationReleaseTests(TestCase):
    def receipt(self):
        owned = SimpleNamespace(id=uuid4(), workspace_id=uuid4())
        pin = ConfigurationSnapshotPin(version="v1", snapshot_sha256="1" * 64)
        request = LocalConfigurationPublish(
            expected_revision=7, configuration=pin, reason="Controlled local publication"
        )
        context = MutationContext.bind(
            SimpleNamespace(id="admin"),
            "local",
            "publish_local_configuration",
            str(owned.id),
            request,
        )
        receipt = SimpleNamespace(
            event_type="management.publish_local_configuration",
            aggregate_id=str(owned.id),
            actor_kind="human",
            actor_id="admin",
            expected_revision=7,
            payload={
                "workspace_id": str(owned.workspace_id),
                "idempotency_key": "local",
                "fingerprint": context.fingerprint,
                "aggregate_revision": 8,
                "result": Resource(
                    id=str(owned.id),
                    project_id=str(owned.id),
                    kind="project",
                    revision=8,
                    state="frozen",
                ).model_dump(mode="json"),
                "evidence": {
                    "configuration": pin.model_dump(mode="json"),
                    "reason": request.reason,
                    "runtime_binding": "local_ultralytics",
                    "qualification": "not_assessed",
                    "automatic_acceptance": False,
                    "promotion_targets": None,
                },
            },
        )
        return receipt, owned, pin

    def test_exact_local_publication_receipt_is_valid(self):
        receipt, owned, pin = self.receipt()
        verify_local_publication(receipt, owned, owned.workspace_id, 8, pin)
        verify_local_publication(receipt, owned, owned.workspace_id, 10, pin)

    def test_local_publication_requires_attributed_owned_revision(self):
        receipt, owned, pin = self.receipt()
        for field, value in (
            ("event_type", "management.publish_configuration_snapshot"),
            ("aggregate_id", str(uuid4())),
            ("actor_kind", "worker"),
            ("actor_id", ""),
            ("actor_id", "other"),
            ("expected_revision", True),
            ("expected_revision", 8),
        ):
            changed = deepcopy(receipt)
            setattr(changed, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(ManagementFailure):
                verify_local_publication(changed, owned, owned.workspace_id, 8, pin)
        with self.assertRaises(ManagementFailure):
            verify_local_publication(receipt, owned, uuid4(), 8, pin)
        with self.assertRaises(ManagementFailure):
            verify_local_publication(receipt, owned, owned.workspace_id, 7, pin)

    def test_local_publication_refuses_runtime_quality_and_rehashed_claims(self):
        receipt, owned, pin = self.receipt()
        for path, value in (
            (("fingerprint",), "f" * 64),
            (("idempotency_key",), ""),
            (("aggregate_revision",), True),
            (("workspace_id",), str(uuid4())),
            (("result", "state"), "draft"),
            (("result", "revision"), 9),
            (("result", "project_id"), str(uuid4())),
            (("evidence", "runtime_binding"), "ultralytics_platform"),
            (("evidence", "qualification"), "qualified"),
            (("evidence", "promotion_targets"), {}),
            (("evidence", "automatic_acceptance"), True),
            (("evidence", "configuration", "version"), "other"),
            (("evidence", "configuration", "snapshot_sha256"), "f" * 64),
        ):
            changed = deepcopy(receipt)
            target = changed.payload
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(ManagementFailure):
                verify_local_publication(changed, owned, owned.workspace_id, 8, pin)
        changed = deepcopy(receipt)
        changed.payload = {}
        with self.assertRaises(ManagementFailure):
            verify_local_publication(changed, owned, owned.workspace_id, 8, pin)

    def test_publication_request_has_no_client_runtime_or_quality_authority(self):
        receipt, _, pin = self.receipt()
        base = dict(
            expected_revision=7, configuration=pin, reason=receipt.payload["evidence"]["reason"]
        )
        for field in (
            "profiles",
            "runtime_binding",
            "qualification",
            "automatic_acceptance",
            "promotion_targets",
            "actor_id",
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                LocalConfigurationPublish(**base, **{field: True})

    def test_latest_publication_is_required_without_fallback_or_duplicate_revision(self):
        receipt, owned, pin = self.receipt()
        release = object.__new__(CanonicalLocalConfigurationRelease)
        object.__setattr__(release, "pin", pin)
        object.__setattr__(
            release,
            "snapshots",
            SimpleNamespace(mutations=SimpleNamespace(workspace_id=owned.workspace_id)),
        )
        connection = Mock()
        later, _, _ = self.receipt()
        later = deepcopy(receipt)
        later.expected_revision = 8
        request = LocalConfigurationPublish(
            expected_revision=8,
            configuration=pin.model_copy(update={"version": "v2"}),
            reason="Later local version",
        )
        later.payload.update(
            aggregate_revision=9,
            result=Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=9,
                state="frozen",
            ).model_dump(mode="json"),
        )
        later.payload["evidence"].update(
            configuration=request.configuration.model_dump(mode="json"), reason=request.reason
        )
        later.payload["fingerprint"] = MutationContext.bind(
            SimpleNamespace(id="admin"),
            "local",
            "publish_local_configuration",
            str(owned.id),
            request,
        ).fingerprint
        with patch.object(
            CanonicalLocalConfigurationRelease, "current_snapshot", return_value={}
        ) as current:
            for rows in ([], [receipt, receipt], [receipt, later], [receipt] * 1001):
                connection.execute.return_value.all.return_value = rows
                with self.subTest(count=len(rows)), self.assertRaises(ManagementFailure):
                    release.verify(connection, owned, 9)
            connection.execute.return_value.all.return_value = [receipt]
            self.assertEqual(release.verify(connection, owned, 9, lock_sources=False), {})
            self.assertEqual(current.call_args.kwargs, {"lock_sources": False})

    def test_current_evidence_failure_precedes_publication_clearance(self):
        receipt, owned, pin = self.receipt()
        release = object.__new__(CanonicalLocalConfigurationRelease)
        object.__setattr__(release, "pin", pin)
        connection = Mock()
        with patch.object(
            CanonicalLocalConfigurationRelease,
            "current_snapshot",
            side_effect=ValueError("stale evidence"),
        ):
            with self.assertRaisesRegex(ValueError, "stale evidence"):
                release.verify(connection, owned, 9)
        connection.execute.assert_not_called()
