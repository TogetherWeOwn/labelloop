"""Local training intent through the existing canonical transaction and shared queue.

Profiles are immutable trusted server configuration, scoped to a canonical project.
No model is loaded, checkpoint fetched, worker started or route mounted here.
"""

import hashlib
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
from sqlalchemy import Connection, select, text

from labelloop_compiler.compiler import json_bytes, resolve_recipe
from labelloop_core.json_records import canonical_json
from labelloop_core.training import InferencePolicy, TrainingRequest

from .management_canonical import CanonicalManagementMutations, refuse
from .management_compilation import TransactionalEnqueue
from .management_compile_worker import content_key, pinned_release
from .management_contracts import JobAccepted, Resource, TrainingStart
from .management_details import TrainingProfileRead, TrainingProfilesRead
from .management_ports import ManagementPrincipal, MutationContext
from .management_reads import CanonicalManagementReads, authorize_reader
from .schema import (
    canonical_dataset_release,
    canonical_event,
    preprocessing_recipe,
    project,
    yolo_dataset_variant,
)


@dataclass(frozen=True)
class ApprovedTrainingProfile:
    project_id: UUID
    id: str
    revision: int
    recipe_id: str
    initialization_sha256: str
    epochs: int = 1
    batch: int = 2
    seed: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID):
            raise ValueError("training profile requires a canonical project UUID")
        if any(
            type(value) is not int for value in (self.revision, self.epochs, self.batch, self.seed)
        ):
            raise ValueError("training profile counters must be integers")
        self.read()

    def request(self, run_id: str, release_id: str, manifest: dict, manifest_sha256: str):
        recipe = resolve_recipe(self.recipe_id)
        return TrainingRequest(
            run_id=run_id,
            project_id=str(self.project_id),
            recipe_id=self.recipe_id,
            dataset_release_id=release_id,
            dataset_manifest_sha256=manifest_sha256,
            class_map_sha256=manifest["file_hashes"]["class-map.json"],
            split_assignment_version=manifest["split_assignment_version"],
            initialization_sha256=self.initialization_sha256,
            policy=InferencePolicy(imgsz=recipe.output_size),
            epochs=self.epochs,
            batch=self.batch,
            seed=self.seed,
        )

    def read(self) -> TrainingProfileRead:
        request = self.request(
            "profile-validation",
            "profile-validation",
            {"file_hashes": {"class-map.json": "0" * 64}, "split_assignment_version": "profile"},
            "0" * 64,
        )
        return TrainingProfileRead(
            id=self.id,
            revision=self.revision,
            recipe_id=self.recipe_id,
            backend="local_ultralytics",
            initialization_sha256=self.initialization_sha256,
            epochs=self.epochs,
            arguments=request.arguments(),
        )


def verified_variant(connection: Connection, row, workspace_id: UUID) -> tuple[dict, str, str]:
    """Require the actual fenced compiler publication, not a hand-inserted variant."""
    receipts = connection.execute(
        select(canonical_event).where(
            canonical_event.c.event_type == "management.variant_compiled",
            canonical_event.c.aggregate_id == str(row.id),
            canonical_event.c.actor_kind == "system",
            canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
        )
    ).all()
    if len(receipts) != 1:
        refuse("missing_evidence")
    evidence = receipts[0].payload
    compiled = connection.execute(
        text(
            "SELECT j.input,j.project_key,j.kind,j.status,j.generation,a.generation AS applied,"
            "s.payload FROM worker_job j JOIN worker_application a ON a.job_id=j.job_id "
            "JOIN worker_stage_artifact s ON s.job_id=j.job_id AND s.stage='compilation' "
            "WHERE j.job_id=:id"
        ),
        {"id": evidence["job_id"]},
    ).one_or_none()
    manifest = row.file_manifest
    manifest_sha256 = hashlib.sha256(json_bytes(manifest)).hexdigest()
    if (
        compiled is None
        or compiled.status != "succeeded"
        or compiled.kind != "compile_variant"
        or compiled.applied != compiled.generation
        or evidence["generation"] != compiled.generation
        or evidence["release_id"] != row.release_key
        or evidence["manifest_sha256"] != manifest_sha256
        or evidence["manifest_key"] != content_key(manifest_sha256)
        or evidence["requested_by"] != compiled.input["requested_by"]
        or evidence["canonical_snapshot_sha256"] != compiled.input["canonical_snapshot_sha256"]
        or compiled.input["variant_id"] != str(row.id)
        or compiled.payload != {"manifest": manifest, "manifest_sha256": manifest_sha256}
        or not row.validated
        or not row.non_empty_val
        or not row.immutable
        or row.class_map != manifest["class_map"]
        or row.policy != manifest["recipe"]
        or row.recipe_key != manifest["recipe"]["recipe_id"]
        or manifest["release_id"] != row.release_key
        or not any(sample["split"] == "val" for sample in manifest["samples"])
        or manifest["file_hashes"]["class-map.json"]
        != hashlib.sha256(json_bytes(row.class_map)).hexdigest()
    ):
        refuse("missing_evidence")
    driver = connection.connection.driver_connection
    if not isinstance(driver, psycopg.Connection):
        refuse("management_unavailable")
    # Reuse exact historical snapshot/receipt rehydration; current labels never replace it.
    lease = SimpleNamespace(
        job_id=evidence["job_id"],
        project_key=compiled.project_key,
        kind=compiled.kind,
        input=compiled.input,
    )
    try:
        _, release, _ = pinned_release(driver, lease, workspace_id)
    except (ValueError, KeyError, TypeError):
        refuse("missing_evidence")
    if (
        manifest["ontology_id"] != release.ontology.ontology_id
        or manifest["ontology_hash"] != release.ontology_hash()
        or manifest["class_map"] != release.ontology.mapping()
        or manifest["split_assignment_version"] != release.assignment.assignment_version
        or canonical_json(manifest["recipe"]) != canonical_json(compiled.input["recipe"])
    ):
        refuse("missing_evidence")
    return manifest, manifest_sha256, evidence["job_id"]


@dataclass(frozen=True)
class CanonicalManagementTraining:
    mutations: CanonicalManagementMutations
    enqueue: TransactionalEnqueue
    profiles: tuple[ApprovedTrainingProfile, ...]

    def __post_init__(self) -> None:
        identities = {(profile.project_id, profile.id) for profile in self.profiles}
        if (
            not isinstance(self.profiles, tuple)
            or len(self.profiles) > 1000
            or len(identities) != len(self.profiles)
        ):
            raise ValueError("training profiles must be bounded and uniquely project-scoped")

    def list_training_profiles(
        self, actor: ManagementPrincipal, project_id: str
    ) -> TrainingProfilesRead:
        authorize_reader(actor)
        CanonicalManagementReads(self.mutations.engine, self.mutations.workspace_id).read(
            actor, "project", project_id
        )
        return TrainingProfilesRead(
            project_id=project_id,
            profiles=[item.read() for item in self.profiles if str(item.project_id) == project_id],
        )

    def start_training(
        self, actor: ManagementPrincipal, context: MutationContext, payload: TrainingStart
    ) -> JobAccepted:
        mutations = self.mutations
        mutations.authorize(actor, context, "start_training", context.target_id, payload)
        try:
            variant_id = UUID(context.target_id)
        except ValueError:
            refuse("not_found")
        with mutations.engine.begin() as connection:
            mutations.begin_mutation(connection, context)
            variant = connection.execute(
                select(
                    yolo_dataset_variant,
                    canonical_dataset_release.c.project_id,
                    canonical_dataset_release.c.release_key,
                    preprocessing_recipe.c.recipe_key,
                    preprocessing_recipe.c.policy,
                    preprocessing_recipe.c.immutable,
                )
                .join(
                    canonical_dataset_release,
                    yolo_dataset_variant.c.release_id == canonical_dataset_release.c.id,
                )
                .join(project, project.c.id == canonical_dataset_release.c.project_id)
                .join(
                    preprocessing_recipe,
                    preprocessing_recipe.c.id == yolo_dataset_variant.c.recipe_id,
                )
                .where(
                    yolo_dataset_variant.c.id == variant_id,
                    project.c.workspace_id == mutations.workspace_id,
                )
            ).one_or_none()
            if variant is None:
                refuse("not_found")
            owned = mutations.lock_project(connection, str(variant.project_id))
            if replay := mutations.replay(connection, context):
                evidence = connection.execute(
                    select(canonical_event.c.payload["evidence"]).where(
                        canonical_event.c.event_type == "management.start_training",
                        canonical_event.c.aggregate_id == str(variant_id),
                        canonical_event.c.actor_id == actor.id,
                        canonical_event.c.payload["workspace_id"].as_string()
                        == str(mutations.workspace_id),
                        canonical_event.c.payload["idempotency_key"].as_string()
                        == context.idempotency_key,
                    )
                ).scalar_one()
                return JobAccepted(job_id=evidence["job_id"], resource=replay)
            if payload.expected_revision != 1:
                refuse("revision_conflict")
            profile = next(
                (
                    item
                    for item in self.profiles
                    if item.project_id == variant.project_id
                    and item.id == payload.training_profile_id
                ),
                None,
            )
            if profile is None:
                refuse("not_found")
            if profile.recipe_id != variant.recipe_key:
                refuse("incompatible_recipe")
            existing = connection.execute(
                select(canonical_event.c.id).where(
                    canonical_event.c.event_type == "management.start_training",
                    canonical_event.c.aggregate_id == str(variant_id),
                    canonical_event.c.payload["workspace_id"].as_string()
                    == str(mutations.workspace_id),
                    canonical_event.c.payload["evidence"]["profile_id"].as_string() == profile.id,
                    canonical_event.c.payload["evidence"]["profile_revision"].as_integer()
                    == profile.revision,
                )
            ).first()
            if existing is not None:
                refuse("revision_conflict")
            try:
                manifest, manifest_sha256, compile_job_id = verified_variant(
                    connection, variant, mutations.workspace_id
                )
            except (KeyError, TypeError, ValueError):
                refuse("missing_evidence")
            run_id = str(uuid4())
            request = profile.request(run_id, variant.release_key, manifest, manifest_sha256)
            inputs = {
                "workspace_id": str(mutations.workspace_id),
                "variant_id": str(variant_id),
                "variant_revision": 1,
                "compile_job_id": compile_job_id,
                "manifest_key": content_key(manifest_sha256),
                "initialization_key": content_key(profile.initialization_sha256),
                "profile": profile.read().model_dump(mode="json"),
                "request": asdict(request),
                "requested_by": actor.id,
            }
            operation_key = (
                "management-train:"
                + hashlib.sha256(
                    canonical_json(
                        [str(mutations.workspace_id), str(variant_id), profile.id, profile.revision]
                    )
                ).hexdigest()
            )
            driver = connection.connection.driver_connection
            if not isinstance(driver, psycopg.Connection):
                refuse("management_unavailable")
            job_id = self.enqueue(
                driver, owned.project_key, operation_key, "train_local", inputs, max_attempts=3
            )
            result = Resource(
                id=run_id,
                project_id=str(variant.project_id),
                kind="training-run",
                revision=1,
                state="queued",
            )
            mutations.record(
                connection,
                context,
                str(variant_id),
                1,
                result,
                {
                    "job_id": job_id,
                    "profile_id": profile.id,
                    "profile_revision": profile.revision,
                    "inputs": inputs,
                },
            )
            return JobAccepted(job_id=job_id, resource=result)
