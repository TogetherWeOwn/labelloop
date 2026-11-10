"""Internal attributed configuration snapshots, not executable release clearance.

Only trusted server profiles are accepted. No route, provider, job, freeze gate or
current-release pointer consumes these snapshots yet; quality targets stay null.
"""

import json
from dataclasses import asdict, dataclass, replace
from typing import Annotated
from uuid import UUID

from pydantic import Field
from sqlalchemy import select

from labelloop_compiler.compiler import resolve_recipe
from labelloop_core.json_records import canonical_json
from labelloop_core.training import InferencePolicy

from .management_canonical import CanonicalManagementMutations, refuse
from .management_contracts import Digest, Identifier, Mutation, Resource
from .management_grouping import load_grouping
from .management_project_reads import project_detail
from .management_setup import draft_sha256, read_setup_approvals
from .management_training import ApprovedTrainingProfile
from .schema import canonical_event


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


def prepare_configuration(connection, owned, workspace_id, revision, profiles):
    # Lock canonical source evidence before the composed reads. Human annotation
    # writers synchronize on these rows, while setup/grouping shares the project lock.
    read_setup_approvals(connection, owned, workspace_id, lock_sources=True)
    detail = project_detail(connection, owned, revision, workspace_id)
    if detail.setup_summary_sha256 is None:
        refuse("missing_evidence")
    assets, by_asset, _, _ = load_grouping(
        connection, owned, detail.versions["assignment"], lock_sources=True
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


@dataclass(frozen=True)
class CanonicalConfigurationSnapshots:
    mutations: CanonicalManagementMutations
    project_id: UUID
    profiles: tuple[ApprovedTrainingProfile, ...]

    def __post_init__(self):
        if not isinstance(self.project_id, UUID):
            raise ValueError("configuration needs a canonical project UUID")
        profile_snapshots(self.profiles, self.project_id)

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
