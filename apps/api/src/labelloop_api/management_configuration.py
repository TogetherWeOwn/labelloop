"""Internal attributed configuration snapshots, not executable release clearance.

Only trusted server profiles are accepted. Opt-in training submission can require
current snapshot provenance; no freeze or current-release clearance is granted.
"""

import json
from dataclasses import asdict, dataclass, replace
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID

from pydantic import Field
from sqlalchemy import String, cast, select, text

from labelloop_compiler.compiler import resolve_recipe
from labelloop_core.json_records import canonical_json
from labelloop_core.training import InferencePolicy

from .management_canonical import CanonicalManagementMutations, refuse
from .management_contracts import Contract, Digest, Identifier, Mutation, Resource
from .management_grouping import load_grouping
from .management_ports import MutationContext
from .management_project_reads import project_detail
from .management_reads import CanonicalManagementReads, authorize_reader
from .management_setup import draft_sha256, read_setup_approvals
from .management_training import ApprovedTrainingProfile
from .schema import canonical_event, project


class ConfigurationSnapshotPin(Contract):
    version: Identifier
    snapshot_sha256: Digest


class ConfigurationSnapshotPublish(Mutation):
    version: Identifier
    setup_summary_sha256: Digest
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


def profile_snapshots(profiles, project_id):
    if not isinstance(profiles, tuple) or not 1 <= len(profiles) <= 32:
        raise ValueError("configuration requires a bounded immutable server profile tuple")
    result = []
    for profile in profiles:
        if not isinstance(profile, ApprovedTrainingProfile) or profile.project_id != project_id:
            raise ValueError("configuration profile belongs to another project")
        profile = replace(profile)
        recipe = resolve_recipe(profile.recipe_id)
        policy = profile.inference_policy or InferencePolicy(imgsz=recipe.output_size)
        policy = InferencePolicy(**asdict(policy))
        result.append(
            {
                **profile.read().model_dump(mode="json"),
                "recipe": asdict(recipe),
                "inference_policy": asdict(policy),
                "seed": profile.seed,
                "batch": profile.batch,
            }
        )
    if len({item["id"] for item in result}) != len(result):
        raise ValueError("configuration profile identifiers must be unique")
    return json.loads(canonical_json(sorted(result, key=lambda item: item["id"])))


def prepare_configuration(
    connection, owned, workspace_id, revision, profiles, *, lock_sources=True
):
    # Mutation callers hold the project lock and lock sources before composed reads.
    # SELECT-only callers instead retain one repeatable snapshot throughout.
    required_isolation = "READ COMMITTED" if lock_sources else "REPEATABLE READ"
    if connection.get_isolation_level() != required_isolation:
        refuse("management_unavailable")
    read_setup_approvals(connection, owned, workspace_id, lock_sources=lock_sources)
    detail = project_detail(connection, owned, revision, workspace_id, lock_sources=lock_sources)
    if detail.setup_summary_sha256 is None:
        refuse("missing_evidence")
    assets, by_asset, _, _ = load_grouping(
        connection, owned, detail.versions["assignment"], lock_sources=lock_sources
    )
    if not 1 <= len(assets) <= 1000:
        refuse("management_unavailable")
    grouping = []
    for asset in sorted(assets, key=lambda item: item.asset_key):
        assignment = by_asset[asset.id]
        grouping.append(
            {
                "asset_id": asset.asset_key,
                "sha256": asset.sha256,
                "group_id": assignment.group_id,
                "split": assignment.split,
                "seed": assignment.seed,
                "algorithm": assignment.algorithm,
                "thresholds": assignment.thresholds,
            }
        )
    snapshot = {
        "schema": "project-configuration-snapshot-v1",
        "project_id": str(owned.id),
        "workspace_id": str(workspace_id),
        "setup_summary_sha256": detail.setup_summary_sha256,
        "setup_approvals": owned.access_policy["management_setup_approvals"],
        "versions": detail.versions,
        "scope": owned.access_policy["management_scope_draft"],
        "guide": owned.access_policy["management_guide_draft"],
        "source_policy": owned.access_policy["management_source_policy_draft"],
        "grouping": grouping,
        "training_profiles": profiles,
        "privacy": {
            "source_permitted_use": detail.source_permitted_use,
            "external_llm_providers_allowed": [],
        },
        "autonomous_spend": False,
        "automatic_acceptance": False,
        "qualification": "not_assessed",
        "promotion_targets": None,
        "runtime_binding": None,
    }
    # The existing canonical encoder rejects nonfinite values; snapshots must
    # survive the native JSONB boundary without tuple/array representation drift.
    encoded = canonical_json(snapshot)
    if len(encoded) > 2_000_000:
        refuse("management_unavailable")
    return json.loads(encoded)


def verify_configuration_receipt(receipt, owned, workspace_id, revision, pin, snapshot):
    """Stored hashes are claims; compare the complete receipt with current evidence."""
    try:
        payload = receipt.payload
        evidence = payload["evidence"]
        request = ConfigurationSnapshotPublish(
            expected_revision=receipt.expected_revision,
            version=evidence["version"],
            setup_summary_sha256=snapshot["setup_summary_sha256"],
            reason=evidence["reason"],
        )
        context = MutationContext.bind(
            SimpleNamespace(id=receipt.actor_id),
            payload["idempotency_key"],
            "publish_configuration_snapshot",
            str(owned.id),
            request,
        )
        result = Resource.model_validate(payload["result"])
        saved = canonical_json(evidence["snapshot"])
        if (
            receipt.event_type != "management.publish_configuration_snapshot"
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
            or result.id != str(owned.id)
            or result.project_id != str(owned.id)
            or result.kind != "project"
            or result.state != "draft"
            or result.revision != payload["aggregate_revision"]
            or evidence["version"] != pin.version
            or len(saved) > 2_000_000
            or evidence["snapshot_sha256"] != pin.snapshot_sha256
            or draft_sha256(evidence["snapshot"]) != pin.snapshot_sha256
            or draft_sha256(snapshot) != pin.snapshot_sha256
            or saved != canonical_json(snapshot)
        ):
            refuse("missing_evidence")
    except (KeyError, TypeError, ValueError, AttributeError):
        refuse("missing_evidence")


def read_current_configuration(
    connection, owned, workspace_id, revision, profiles, pin, *, lock_sources=False
):
    """Verify current evidence in a read snapshot or the caller's locked mutation."""
    if str(owned.workspace_id) != str(workspace_id):
        refuse("not_found")
    required_isolation = "READ COMMITTED" if lock_sources else "REPEATABLE READ"
    if connection.get_isolation_level() != required_isolation:
        refuse("management_unavailable")
    receipts = connection.execute(
        select(canonical_event)
        .where(
            canonical_event.c.aggregate_id == str(owned.id),
            canonical_event.c.event_type == "management.publish_configuration_snapshot",
        )
        .limit(1001)
    ).all()
    if len(receipts) > 1000:
        refuse("management_unavailable")
    try:
        versions = [
            ConfigurationSnapshotPin(
                version=row.payload["evidence"]["version"],
                snapshot_sha256=row.payload["evidence"]["snapshot_sha256"],
            ).version
            for row in receipts
        ]
        if len(set(versions)) != len(versions) or versions.count(pin.version) != 1:
            refuse("missing_evidence")
        receipt = receipts[versions.index(pin.version)]
        snapshot = prepare_configuration(
            connection, owned, workspace_id, revision, profiles, lock_sources=lock_sources
        )
        verify_configuration_receipt(receipt, owned, workspace_id, revision, pin, snapshot)
        return snapshot
    except (KeyError, TypeError, ValueError):
        refuse("missing_evidence")


@dataclass(frozen=True)
class ConfigurationTrainingBinding:
    project_id: UUID
    version: str
    snapshot_sha256: str

    def __post_init__(self):
        if not isinstance(self.project_id, UUID):
            raise ValueError("configuration binding requires a canonical project UUID")
        self.pin()

    def pin(self):
        return ConfigurationSnapshotPin(version=self.version, snapshot_sha256=self.snapshot_sha256)


def training_configuration(connection, owned, workspace_id, profiles, binding, manifest):
    """Called only after the training producer locks the owned project, before enqueue."""
    if owned.id != binding.project_id:
        refuse("missing_evidence")
    rows = CanonicalManagementReads(connection.engine, workspace_id).project_rows()
    revision = connection.execute(select(rows.c.revision).where(rows.c.id == owned.id)).scalar_one()
    snapshot = read_current_configuration(
        connection,
        owned,
        workspace_id,
        revision,
        profile_snapshots(tuple(p for p in profiles if p.project_id == owned.id), owned.id),
        binding.pin(),
        lock_sources=True,
    )
    try:
        groups = {item["asset_id"]: item for item in snapshot["grouping"]}
        if (
            manifest["ontology_id"] != snapshot["versions"]["ontology_id"]
            or manifest["split_assignment_version"] != snapshot["versions"]["assignment"]
            or not manifest["samples"]
            or any(
                sample["source_sha256"] != groups[sample["asset_id"]]["sha256"]
                or sample["group_id"] != groups[sample["asset_id"]]["group_id"]
                or sample["split"] != groups[sample["asset_id"]]["split"]
                for sample in manifest["samples"]
            )
        ):
            refuse("missing_evidence")
    except (KeyError, TypeError):
        refuse("missing_evidence")
    return {
        "project_id": str(owned.id),
        **binding.pin().model_dump(mode="json"),
        "setup_summary_sha256": snapshot["setup_summary_sha256"],
    }


@dataclass(frozen=True)
class CanonicalConfigurationSnapshots:
    mutations: CanonicalManagementMutations
    project_id: UUID
    profiles: tuple[ApprovedTrainingProfile, ...]

    def __post_init__(self):
        if not isinstance(self.project_id, UUID):
            raise ValueError("configuration needs a canonical project UUID")
        profile_snapshots(self.profiles, self.project_id)

    def read_current(self, actor, pin):
        authorize_reader(actor)
        pin = ConfigurationSnapshotPin.model_validate(pin.model_dump(mode="json"))
        service = self.mutations
        owned = CanonicalManagementReads(service.engine, service.workspace_id).project_rows()
        with service.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                row = connection.execute(
                    select(project, owned.c.revision.label("management_revision"))
                    .join(owned, project.c.id == owned.c.id)
                    .where(cast(project.c.id, String) == str(self.project_id))
                ).one_or_none()
                if row is None:
                    refuse("not_found")
                return read_current_configuration(
                    connection,
                    row,
                    service.workspace_id,
                    row.management_revision,
                    profile_snapshots(self.profiles, self.project_id),
                    pin,
                )

    def publish(self, actor, context, payload):
        service = self.mutations
        service.authorize(
            actor, context, "publish_configuration_snapshot", context.target_id, payload
        )
        payload = ConfigurationSnapshotPublish.model_validate(payload.model_dump(mode="json"))
        if context.target_id != str(self.project_id):
            refuse("not_found")
        with service.engine.begin() as connection:
            service.begin_mutation(connection, context)
            owned = service.lock_project(connection, context.target_id)
            if replay := service.replay(connection, context):
                return replay
            service.check_project_revision(connection, owned.id, payload.expected_revision)
            prior = connection.execute(
                select(canonical_event.c.id, canonical_event.c.payload)
                .where(
                    canonical_event.c.aggregate_id == str(owned.id),
                    canonical_event.c.event_type == "management.publish_configuration_snapshot",
                )
                .limit(1001)
            ).all()
            if len(prior) >= 1000:
                refuse("management_unavailable")
            if any(row.payload["evidence"]["version"] == payload.version for row in prior):
                refuse("revision_conflict")
            snapshot = prepare_configuration(
                connection,
                owned,
                service.workspace_id,
                payload.expected_revision,
                profile_snapshots(self.profiles, self.project_id),
            )
            if snapshot["setup_summary_sha256"] != payload.setup_summary_sha256:
                refuse("revision_conflict")
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            service.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {
                    "version": payload.version,
                    "snapshot": snapshot,
                    "snapshot_sha256": draft_sha256(snapshot),
                    "reason": payload.reason,
                },
            )
            return result
