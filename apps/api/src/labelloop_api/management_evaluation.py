"""Pinned development-evaluation intent, not final-test or qualification authority.

Trusted server protocols select a verified compiled variant and its validation
split. The existing queue owns dispatch; this module runs no inference or I/O to
objects and installs no worker, runtime grants or default application routes.
"""

import hashlib
from dataclasses import asdict, dataclass
from uuid import UUID, uuid4

import psycopg
from sqlalchemy import select, text

from labelloop_core.json_records import canonical_json

from .management_canonical import CanonicalManagementMutations, refuse
from .management_compilation import TransactionalEnqueue
from .management_compile_worker import content_key
from .management_contracts import EvaluationStart, Identifier, JobAccepted, Resource
from .management_model_worker import candidate_release
from .management_ports import ManagementPrincipal, MutationContext
from .management_train_worker import decode_training
from .management_training import verified_variant
from .model_schema import evaluation_run, model_release, training_run
from .model_store import decode_release, record_digest, validate_model, variant_context
from .schema import canonical_dataset_release, canonical_event, preprocessing_recipe, project
from .schema import yolo_dataset_variant as variant_table


@dataclass(frozen=True)
class ApprovedDevelopmentProtocol:
    project_id: UUID
    id: str
    revision: int
    variant_id: UUID

    def __post_init__(self) -> None:
        # Reuse the wire identifier contract for trusted configuration too.
        from pydantic import TypeAdapter

        TypeAdapter(Identifier).validate_python(self.id)
        if (
            not isinstance(self.project_id, UUID)
            or not isinstance(self.variant_id, UUID)
            or type(self.revision) is not int
            or self.revision < 1
        ):
            raise ValueError("development protocol needs project/variant UUIDs and a revision")


def verified_model(connection, row, workspace_id: UUID):
    """Require the existing fenced model publication and shared model-store gate."""
    receipts = (
        connection.execute(
            select(canonical_event.c.payload).where(
                canonical_event.c.event_type == "management.model_exported",
                canonical_event.c.aggregate_id == row.release_key,
                canonical_event.c.actor_kind == "system",
                canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
            )
        )
        .scalars()
        .all()
    )
    if len(receipts) != 1:
        refuse("missing_evidence")
    evidence = receipts[0]
    published = connection.execute(
        text(
            "SELECT j.input,j.project_key,j.kind,j.status,j.generation,a.generation AS applied,"
            "s.payload AS exported,t.payload AS trained FROM worker_job j "
            "JOIN worker_application a ON a.job_id=j.job_id "
            "JOIN worker_stage_artifact s ON s.job_id=j.job_id AND s.stage='export' "
            "JOIN worker_stage_artifact t ON t.job_id=j.job_id AND t.stage='training' "
            "WHERE j.job_id=:id"
        ),
        {"id": evidence["job_id"]},
    ).one_or_none()
    release = decode_release(row.record)
    if release.release_id != row.release_key:
        refuse("missing_evidence")
    run = (
        connection.execute(select(training_run).where(training_run.c.id == row.training_run_id))
        .mappings()
        .one()
    )
    reports = (
        connection.execute(
            select(evaluation_run).where(
                evaluation_run.c.training_run_id == run["id"],
                evaluation_run.c.run_key.in_(release.evaluation_run_ids),
            )
        )
        .mappings()
        .all()
    )
    if (
        published is None
        or published.status != "succeeded"
        or published.kind != "train_local"
        or published.applied != published.generation
        or evidence["generation"] != published.generation
        or evidence["project_id"] != str(row.project_id)
        or evidence["project_key"] != published.project_key
        or evidence["variant_id"] != str(run["variant_id"])
        or evidence["training_run_id"] != run["run_key"]
        or evidence["requested_by"] != published.input["requested_by"]
        or evidence["stage_sha256"]
        != hashlib.sha256(canonical_json(published.exported)).hexdigest()
        or evidence["weights_key"] != content_key(release.weights_sha256)
        or evidence["export_key"] != content_key(release.export_sha256)
        or published.input["workspace_id"] != str(workspace_id)
        or published.input["variant_id"] != str(run["variant_id"])
        or published.input["request"] != published.trained["result"]["request"]
        or published.trained["result"]["request"]["run_id"] != run["run_key"]
        or published.trained["result"]["request"]["project_id"] != str(row.project_id)
        or published.exported["evidence"] != row.export_evidence
        or published.exported["weights_key"] != evidence["weights_key"]
        or published.exported["export_key"] != evidence["export_key"]
        or candidate_release(
            decode_training(published.trained["result"]),
            run["input_manifest"],
            published.project_key,
            row.export_evidence,
        )
        != release
    ):
        refuse("missing_evidence")
    validate_model(
        variant_context(connection, run["variant_id"]), run, release, row.export_evidence, reports
    )
    return release, evidence["job_id"]


@dataclass(frozen=True)
class CanonicalManagementEvaluation:
    mutations: CanonicalManagementMutations
    enqueue: TransactionalEnqueue
    protocols: tuple[ApprovedDevelopmentProtocol, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.protocols, tuple)
            or len(self.protocols) > 1000
            or len({(item.project_id, item.id) for item in self.protocols}) != len(self.protocols)
        ):
            raise ValueError("evaluation protocols must be bounded and uniquely project-scoped")

    def start_evaluation(
        self, actor: ManagementPrincipal, context: MutationContext, payload: EvaluationStart
    ) -> JobAccepted:
        mutations = self.mutations
        mutations.authorize(actor, context, "start_evaluation", context.target_id, payload)
        with mutations.engine.begin() as connection:
            mutations.begin_mutation(connection, context)
            model = connection.execute(
                select(model_release)
                .join(project, project.c.id == model_release.c.project_id)
                .where(
                    model_release.c.release_key == context.target_id,
                    project.c.workspace_id == mutations.workspace_id,
                )
            ).one_or_none()
            if model is None:
                refuse("not_found")
            owned = mutations.lock_project(connection, str(model.project_id))
            if replay := mutations.replay(connection, context):
                evidence = connection.execute(
                    select(canonical_event.c.payload["evidence"]).where(
                        canonical_event.c.event_type == "management.start_evaluation",
                        canonical_event.c.aggregate_id == context.target_id,
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
            protocol = next(
                (
                    item
                    for item in self.protocols
                    if item.project_id == model.project_id and item.id == payload.protocol_id
                ),
                None,
            )
            if protocol is None:
                refuse("not_found")
            variant = connection.execute(
                select(
                    variant_table,
                    canonical_dataset_release.c.release_key,
                    preprocessing_recipe.c.recipe_key,
                    preprocessing_recipe.c.policy,
                    preprocessing_recipe.c.immutable,
                )
                .join(
                    canonical_dataset_release,
                    variant_table.c.release_id == canonical_dataset_release.c.id,
                )
                .join(preprocessing_recipe, variant_table.c.recipe_id == preprocessing_recipe.c.id)
                .where(
                    variant_table.c.id == protocol.variant_id,
                    canonical_dataset_release.c.project_id == model.project_id,
                    canonical_dataset_release.c.release_key == payload.dataset_release_id,
                )
            ).one_or_none()
            if variant is None:
                refuse("not_found")
            existing = connection.execute(
                select(canonical_event.c.id).where(
                    canonical_event.c.event_type == "management.start_evaluation",
                    canonical_event.c.aggregate_id == context.target_id,
                    canonical_event.c.payload["workspace_id"].as_string()
                    == str(mutations.workspace_id),
                    canonical_event.c.payload["evidence"]["protocol_id"].as_string() == protocol.id,
                    canonical_event.c.payload["evidence"]["protocol_revision"].as_integer()
                    == protocol.revision,
                )
            ).first()
            if existing is not None:
                refuse("revision_conflict")
            try:
                release, model_job_id = verified_model(connection, model, mutations.workspace_id)
                manifest, digest, compile_job_id = verified_variant(
                    connection, variant, mutations.workspace_id
                )
            except (KeyError, TypeError, ValueError):
                refuse("missing_evidence")
            if (
                release.project_id != owned.project_key
                or release.recipe_id != variant.recipe_key
                or release.class_map_sha256 != manifest["file_hashes"]["class-map.json"]
                or release.preprocessing_sha256 != record_digest(manifest["recipe"])
                or release.class_ids
                != tuple(sorted(manifest["class_map"], key=manifest["class_map"].__getitem__))
            ):
                refuse("incompatible_recipe")
            run_id = str(uuid4())
            inputs = {
                "workspace_id": str(mutations.workspace_id),
                "project_id": str(model.project_id),
                "model_id": release.release_id,
                "model_sha256": record_digest(model.record),
                "model_job_id": model_job_id,
                "variant_id": str(variant.id),
                "compile_job_id": compile_job_id,
                "dataset_release_id": variant.release_key,
                "manifest_sha256": digest,
                "manifest_key": content_key(digest),
                "weights_key": content_key(release.weights_sha256),
                "protocol": {
                    "id": protocol.id,
                    "revision": protocol.revision,
                    "role": "development",
                    "split": "val",
                },
                "policy": asdict(release.policy),
                "run_id": run_id,
                "requested_by": actor.id,
            }
            operation_key = (
                "management-evaluate:"
                + hashlib.sha256(
                    canonical_json(
                        [
                            str(mutations.workspace_id),
                            release.release_id,
                            protocol.id,
                            protocol.revision,
                        ]
                    )
                ).hexdigest()
            )
            driver = connection.connection.driver_connection
            if not isinstance(driver, psycopg.Connection):
                refuse("management_unavailable")
            job_id = self.enqueue(
                driver, owned.project_key, operation_key, "evaluate_local", inputs, max_attempts=3
            )
            result = Resource(
                id=run_id,
                project_id=str(model.project_id),
                kind="evaluation",
                revision=1,
                state="queued",
            )
            mutations.record(
                connection,
                context,
                context.target_id,
                1,
                result,
                {
                    "job_id": job_id,
                    "protocol_id": protocol.id,
                    "protocol_revision": protocol.revision,
                    "inputs": inputs,
                },
            )
            return JobAccepted(job_id=job_id, resource=result)
