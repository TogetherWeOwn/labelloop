"""Canonical compile intent, not a compiler handler or live route binding.

The trusted composition supplies the existing queue's enqueue_in_transaction.
SQLAlchemy retains connection/transaction ownership; no standalone enqueue is used.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Protocol
from uuid import uuid4

import psycopg
from sqlalchemy import select

from labelloop_compiler.compiler import resolve_recipe

from .management_canonical import CanonicalManagementMutations, refuse
from .management_contracts import JobAccepted, VariantCompile
from .management_ports import ManagementPrincipal, MutationContext
from .management_release_reads import release_detail
from .schema import canonical_dataset_release, canonical_event, project


class TransactionalEnqueue(Protocol):
    def __call__(
        self,
        connection: psycopg.Connection,
        project_key: str,
        operation_key: str,
        kind: str,
        payload: dict,
        *,
        max_attempts: int = 3,
    ) -> str: ...


@dataclass(frozen=True)
class CanonicalManagementCompilation:
    mutations: CanonicalManagementMutations
    enqueue: TransactionalEnqueue

    def compile_variant(
        self, actor: ManagementPrincipal, context: MutationContext, payload: VariantCompile
    ) -> JobAccepted:
        mutations = self.mutations
        mutations.authorize(actor, context, "compile_variant", context.target_id, payload)
        with mutations.engine.begin() as connection:
            mutations.begin_mutation(connection, context)
            release = connection.execute(
                select(canonical_dataset_release)
                .join(project, project.c.id == canonical_dataset_release.c.project_id)
                .where(
                    canonical_dataset_release.c.release_key == context.target_id,
                    project.c.workspace_id == mutations.workspace_id,
                )
            ).one_or_none()
            if release is None:
                refuse("not_found")
            # Shares setup/freeze synchronization, without changing the project's revision.
            owned = mutations.lock_project(connection, str(release.project_id))
            if replay := mutations.replay(connection, context):
                evidence = connection.execute(
                    select(canonical_event.c.payload["evidence"]).where(
                        canonical_event.c.event_type == "management.compile_variant",
                        canonical_event.c.aggregate_id == release.release_key,
                        canonical_event.c.actor_id == actor.id,
                        canonical_event.c.payload["workspace_id"].as_string()
                        == str(mutations.workspace_id),
                        canonical_event.c.payload["idempotency_key"].as_string()
                        == context.idempotency_key,
                    )
                ).scalar_one()
                return JobAccepted(job_id=evidence["job_id"], resource=replay)
            detail = release_detail(connection, release, mutations.workspace_id)
            if payload.expected_revision != detail.resource.revision:
                refuse("revision_conflict")
            # One immutable release/recipe pair has one producer intent, even across actors.
            existing = connection.execute(
                select(canonical_event.c.id).where(
                    canonical_event.c.event_type == "management.compile_variant",
                    canonical_event.c.aggregate_id == release.release_key,
                    canonical_event.c.payload["workspace_id"].as_string()
                    == str(mutations.workspace_id),
                    canonical_event.c.payload["evidence"]["recipe_id"].as_string()
                    == payload.recipe_id,
                )
            ).first()
            if existing is not None:
                refuse("revision_conflict")
            recipe = asdict(resolve_recipe(payload.recipe_id))
            variant_id = str(uuid4())
            inputs = {
                "workspace_id": str(mutations.workspace_id),
                "project_id": str(release.project_id),
                "release_id": release.release_key,
                "release_revision": detail.resource.revision,
                "canonical_snapshot_sha256": release.hashes["canonical_snapshot_sha256"],
                "ontology_id": detail.ontology_id,
                "assignment_version": release.assignment_version,
                "recipe": recipe,
                "variant_id": variant_id,
                "requested_by": actor.id,
            }
            operation_key = (
                "management-compile:"
                + hashlib.sha256(
                    json.dumps(
                        [str(mutations.workspace_id), release.release_key, payload.recipe_id],
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
            )
            driver = connection.connection.driver_connection
            if not isinstance(driver, psycopg.Connection):
                refuse("management_unavailable")
            job_id = self.enqueue(
                driver, owned.project_key, operation_key, "compile_variant", inputs, max_attempts=3
            )
            result = detail.resource.model_copy(
                update={"id": variant_id, "kind": "variant", "state": "queued"}
            )
            mutations.record(
                connection,
                context,
                release.release_key,
                payload.expected_revision,
                result,
                {"job_id": job_id, "recipe_id": payload.recipe_id, "inputs": inputs},
            )
            return JobAccepted(job_id=job_id, resource=result)
