"""Explicit local configuration publication and bound canonical freeze.

This internal adapter grants only local dataset preparation. It never mounts a
route, selects a provider, qualifies a model or enables automatic acceptance.
"""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Annotated

from pydantic import Field
from sqlalchemy import select

from .management_canonical import refuse
from .management_configuration import (
    CanonicalConfigurationSnapshots,
    ConfigurationSnapshotPin,
    profile_snapshots,
    read_current_configuration,
)
from .management_contracts import Mutation, ReleaseFreeze, Resource
from .management_ports import MutationContext
from .schema import canonical_event


class LocalConfigurationPublish(Mutation):
    configuration: ConfigurationSnapshotPin
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


def verify_local_publication(receipt, owned, workspace_id, revision, pin):
    try:
        payload = receipt.payload
        evidence = payload["evidence"]
        request = LocalConfigurationPublish(
            expected_revision=receipt.expected_revision,
            configuration=ConfigurationSnapshotPin.model_validate(evidence["configuration"]),
            reason=evidence["reason"],
        )
        context = MutationContext.bind(
            SimpleNamespace(id=receipt.actor_id),
            payload["idempotency_key"],
            "publish_local_configuration",
            str(owned.id),
            request,
        )
        result = Resource.model_validate(payload["result"])
        if (
            receipt.event_type != "management.publish_local_configuration"
            or receipt.aggregate_id != str(owned.id)
            or receipt.actor_kind != "human"
            or not isinstance(receipt.actor_id, str)
            or not receipt.actor_id
            or str(owned.workspace_id) != str(workspace_id)
            or payload["workspace_id"] != str(workspace_id)
            or not isinstance(payload["idempotency_key"], str)
            or not payload["idempotency_key"]
            or payload["fingerprint"] != context.fingerprint
            or type(receipt.expected_revision) is not int
            or receipt.expected_revision < 1
            or type(payload["aggregate_revision"]) is not int
            or payload["aggregate_revision"] != receipt.expected_revision + 1
            or type(revision) is not int
            or revision < payload["aggregate_revision"]
            or request.configuration != pin
            or evidence["runtime_binding"] != "local_ultralytics"
            or evidence["qualification"] != "not_assessed"
            or evidence["automatic_acceptance"] is not False
            or evidence["promotion_targets"] is not None
            or result
            != Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload["aggregate_revision"],
                state="frozen",
            )
        ):
            refuse("missing_evidence")
    except (KeyError, TypeError, ValueError, AttributeError):
        refuse("missing_evidence")


@dataclass(frozen=True)
class CanonicalLocalConfigurationRelease:
    snapshots: CanonicalConfigurationSnapshots
    pin: ConfigurationSnapshotPin

    def __post_init__(self):
        if not isinstance(self.snapshots, CanonicalConfigurationSnapshots):
            raise ValueError("local publication requires canonical snapshots")
        ConfigurationSnapshotPin.model_validate(self.pin.model_dump(mode="json"))

    def current_snapshot(self, connection, owned, revision, *, lock_sources=True):
        if owned.id != self.snapshots.project_id:
            refuse("not_found")
        return read_current_configuration(
            connection,
            owned,
            self.snapshots.mutations.workspace_id,
            revision,
            profile_snapshots(self.snapshots.profiles, self.snapshots.project_id),
            self.pin,
            lock_sources=lock_sources,
        )

    def verify(self, connection, owned, revision, *, lock_sources=True):
        snapshot = self.current_snapshot(connection, owned, revision, lock_sources=lock_sources)
        receipts = connection.execute(
            select(canonical_event)
            .where(
                canonical_event.c.aggregate_id == str(owned.id),
                canonical_event.c.event_type == "management.publish_local_configuration",
            )
            .limit(1001)
        ).all()
        if len(receipts) > 1000:
            refuse("management_unavailable")
        # No fallback to an older activation after a newer publication is present.
        if not receipts:
            refuse("missing_evidence")
        versions = []
        for receipt in receipts:
            try:
                saved_pin = ConfigurationSnapshotPin.model_validate(
                    receipt.payload["evidence"]["configuration"]
                )
            except (KeyError, TypeError, ValueError):
                refuse("missing_evidence")
            verify_local_publication(
                receipt, owned, self.snapshots.mutations.workspace_id, revision, saved_pin
            )
            versions.append(saved_pin.version)
        revisions = [row.payload["aggregate_revision"] for row in receipts]
        if len(set(revisions)) != len(revisions) or len(set(versions)) != len(versions):
            refuse("missing_evidence")
        latest = receipts[revisions.index(max(revisions))]
        verify_local_publication(
            latest, owned, self.snapshots.mutations.workspace_id, revision, self.pin
        )
        return snapshot

    def publish(self, actor, context, payload):
        service = self.snapshots.mutations
        service.authorize(actor, context, "publish_local_configuration", context.target_id, payload)
        payload = LocalConfigurationPublish.model_validate(payload.model_dump(mode="json"))
        if context.target_id != str(self.snapshots.project_id):
            refuse("not_found")
        if payload.configuration != self.pin:
            refuse("revision_conflict")
        with service.engine.begin() as connection:
            service.begin_mutation(connection, context)
            owned = service.lock_project(connection, context.target_id)
            if replay := service.replay(connection, context):
                return replay
            service.check_project_revision(connection, owned.id, payload.expected_revision)
            self.current_snapshot(connection, owned, payload.expected_revision)
            prior = connection.execute(
                select(canonical_event)
                .where(
                    canonical_event.c.aggregate_id == str(owned.id),
                    canonical_event.c.event_type == "management.publish_local_configuration",
                )
                .limit(1001)
            ).all()
            if len(prior) >= 1000:
                refuse("management_unavailable")
            for receipt in prior:
                try:
                    saved_pin = ConfigurationSnapshotPin.model_validate(
                        receipt.payload["evidence"]["configuration"]
                    )
                except (KeyError, TypeError, ValueError):
                    refuse("missing_evidence")
                verify_local_publication(
                    receipt, owned, service.workspace_id, payload.expected_revision, saved_pin
                )
                if saved_pin.version == self.pin.version:
                    refuse("revision_conflict")
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="frozen",
            )
            service.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {
                    "configuration": self.pin.model_dump(mode="json"),
                    "reason": payload.reason,
                    "runtime_binding": "local_ultralytics",
                    "qualification": "not_assessed",
                    "automatic_acceptance": False,
                    "promotion_targets": None,
                },
            )
            return result

    def freeze_release(self, actor, context, payload):
        service = self.snapshots.mutations
        service.authorize(actor, context, "freeze_release", context.target_id, payload)
        payload = ReleaseFreeze.model_validate(payload.model_dump(mode="json"))
        if context.target_id != str(self.snapshots.project_id):
            refuse("not_found")
        with service.engine.begin() as connection:
            service.begin_mutation(connection, context)
            owned = service.lock_project(connection, context.target_id)
            if replay := service.replay(connection, context):
                return replay
            service.check_project_revision(connection, owned.id, payload.expected_revision)
            return service._freeze_release(
                connection, owned, context, payload, configuration_release=self
            )
