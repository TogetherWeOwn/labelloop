"""Canonical create/ingest/freeze transactions for composition with management ports.

No routes are mounted here. The workspace is trusted deployment configuration,
not request data; the caller must supply the shared authenticated principal.
Only a runtime DB role without event UPDATE/DELETE/TRUNCATE can use this adapter.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import NoReturn
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, func, insert, select, text, update

from labelloop_compiler.ingest import ObjectStore, imaging_toolchain

from .management_contracts import (
    AssetIngest,
    ClassCatalogDraft,
    ClassCatalogPublish,
    Contract,
    GuideDraft,
    ManagementError,
    ProjectCreate,
    ReferenceAccept,
    ReferenceEnroll,
    ReferenceRevalidate,
    ReferenceWithdraw,
    ReleaseFreeze,
    Resource,
    ScopeDraft,
    SetupStepApprove,
    SourcePairResolve,
    SourcePolicyDraft,
)
from .management_ontology import canonical_classes, validate_ontology
from .management_pair_resolution import resolve_pair
from .management_ports import ManagementFailure, ManagementPrincipal, MutationContext
from .management_references import accept_reference as approve_reference
from .management_references import enroll_reference as create_reference
from .management_references import reference_context_sha256, require_retired_references
from .management_references import revalidate_reference as renew_reference
from .management_references import withdraw_reference as retire_reference
from .management_release import freeze_candidate
from .management_setup import draft_sha256, published_catalog_content, read_setup_approvals
from .repository import ingest_canonical_asset
from .schema import canonical_dataset_release, canonical_event, ontology_release, project


def refuse(code: str) -> NoReturn:
    raise ManagementFailure(ManagementError.model_validate({"detail": code}))


def transaction_lock(connection: Connection, scope: list[str]) -> None:
    # Transaction-scoped locks cover missing rows too (creation and first retry).
    digest = hashlib.sha256(json.dumps(scope, separators=(",", ":")).encode()).digest()
    key = int.from_bytes(digest[:8], byteorder="big", signed=True)
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


@dataclass(frozen=True)
class CanonicalManagementMutations:
    engine: Engine
    store: ObjectStore
    workspace_id: UUID

    def __post_init__(self) -> None:
        if self.engine.dialect.name != "postgresql" or not isinstance(self.workspace_id, UUID):
            raise ValueError("canonical management requires Postgres and a trusted workspace UUID")

    def authorize(
        self,
        actor: ManagementPrincipal,
        context: MutationContext,
        operation: str,
        target: str,
        payload: Contract,
    ) -> None:
        if (
            not actor.id
            or actor.kind != "human"
            or actor.role != "admin"
            or not actor.permits("review")
            or not actor.permits("admin")
        ):
            refuse("permission_denied")
        if context != MutationContext.bind(
            actor, context.idempotency_key, operation, target, payload
        ):
            refuse("invalid_request")

    def begin_mutation(self, connection: Connection, context: MutationContext) -> None:
        # Reconciliation must see commits made while waiting for the locks.
        if connection.get_isolation_level() != "READ COMMITTED":
            refuse("management_unavailable")
        # Existing canonical events are the audit/idempotency authority. Refuse
        # owner/superuser or broad runtime grants rather than pretending that
        # application-level append-only conventions protect those records.
        can_rewrite = connection.execute(
            text(
                "SELECT has_table_privilege(current_user, 'canonical_event',"
                " 'UPDATE,DELETE,TRUNCATE')"
            )
        ).scalar_one()
        if can_rewrite:
            refuse("management_unavailable")
        transaction_lock(
            connection,
            [
                "management-idempotency",
                str(self.workspace_id),
                context.actor_id,
                context.idempotency_key,
            ],
        )

    def replay(self, connection: Connection, context: MutationContext) -> Resource | None:
        payload = connection.execute(
            select(canonical_event.c.payload).where(
                canonical_event.c.event_type.like("management.%"),
                canonical_event.c.actor_kind == "human",
                canonical_event.c.actor_id == context.actor_id,
                canonical_event.c.payload["workspace_id"].as_string() == str(self.workspace_id),
                canonical_event.c.payload["idempotency_key"].as_string() == context.idempotency_key,
            )
        ).scalar_one_or_none()
        if payload is None:
            return None
        if payload["fingerprint"] != context.fingerprint:
            refuse("idempotency_conflict")
        return Resource.model_validate(payload["result"])

    def record(
        self,
        connection: Connection,
        context: MutationContext,
        aggregate_id: str,
        expected_revision: int,
        result: Resource,
        evidence: dict,
    ) -> None:
        connection.execute(
            insert(canonical_event).values(
                event_type=f"management.{context.operation}",
                aggregate_id=aggregate_id,
                expected_revision=expected_revision,
                actor_kind="human",
                actor_id=context.actor_id,
                payload={
                    "workspace_id": str(self.workspace_id),
                    "idempotency_key": context.idempotency_key,
                    "fingerprint": context.fingerprint,
                    "result": result.model_dump(mode="json"),
                    "aggregate_revision": expected_revision + 1,
                    "evidence": evidence,
                },
            )
        )

    def create_project(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ProjectCreate
    ) -> Resource:
        self.authorize(actor, context, "create_project", "projects", payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            # project_key is globally unique in the delivered canonical schema.
            transaction_lock(connection, ["management-project-key", payload.project_key])
            if replay := self.replay(connection, context):
                return replay
            if (
                connection.execute(
                    select(project.c.id).where(project.c.project_key == payload.project_key)
                ).scalar_one_or_none()
                is not None
            ):
                refuse("revision_conflict")
            project_id = uuid4()
            connection.execute(
                insert(project).values(
                    id=project_id,
                    project_key=payload.project_key,
                    workspace_id=self.workspace_id,
                    task=payload.task,
                    scope=payload.scope,
                    domain=payload.domain,
                    access_policy={
                        "annotation_guide": payload.annotation_guide,
                        "source_permitted_use": payload.source_permitted_use,
                        "autonomous_spend": False,
                        "external_llm_providers_allowed": [],
                    },
                )
            )
            ontology_id = uuid4()
            connection.execute(
                insert(ontology_release).values(
                    id=ontology_id,
                    ontology_key=str(ontology_id),
                    project_id=project_id,
                    version=1,
                    classes=[item.model_dump(mode="json") for item in payload.classes],
                    class_map={item.stable_id: index for index, item in enumerate(payload.classes)},
                )
            )
            result = Resource(
                id=str(project_id),
                project_id=str(project_id),
                kind="project",
                revision=1,
                state="draft",
            )
            self.record(
                connection, context, str(project_id), 0, result, {"ontology_id": str(ontology_id)}
            )
            return result

    def lock_project(self, connection: Connection, target: str):
        try:
            project_id = UUID(target)
        except ValueError:
            refuse("not_found")
        row = connection.execute(
            select(project)
            .where(project.c.id == project_id, project.c.workspace_id == self.workspace_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            refuse("not_found")
        return row

    def check_project_revision(
        self, connection: Connection, project_id: UUID, expected_revision: int
    ) -> None:
        last_revision = connection.execute(
            select(func.max(canonical_event.c.expected_revision)).where(
                canonical_event.c.aggregate_id == str(project_id),
                canonical_event.c.event_type.like("management.%"),
            )
        ).scalar_one()
        # Existing non-management projects need an explicit adoption path.
        if last_revision is None:
            refuse("management_unavailable")
        if expected_revision != last_revision + 1:
            refuse("revision_conflict")

    def save_scope_draft(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ScopeDraft
    ) -> Resource:
        self.authorize(actor, context, "save_scope_draft", context.target_id, payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            owned = self.lock_project(connection, context.target_id)
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, owned.id, payload.expected_revision)
            draft = payload.model_dump(mode="json", exclude={"expected_revision"})
            policy = dict(owned.access_policy)
            policy["management_scope_draft"] = draft
            policy["source_permitted_use"] = payload.source_permitted_use
            # Scope edits never carry forward an automatic acceptance grant or
            # dependent setup approvals. The audit retains the old evidence.
            policy.pop("auto_accept_qualification", None)
            policy["management_setup_approvals"] = {}
            connection.execute(
                update(project)
                .where(project.c.id == owned.id)
                .values(scope=payload.scope, domain=payload.domain, access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            self.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {
                    "scope_draft": draft,
                    "invalidated_steps": [
                        "scope",
                        "classes",
                        "evidence",
                        "guide",
                        "sources",
                        "publish",
                    ],
                },
            )
            return result

    def save_guide_draft(
        self, actor: ManagementPrincipal, context: MutationContext, payload: GuideDraft
    ) -> Resource:
        self.authorize(actor, context, "save_guide_draft", context.target_id, payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            owned = self.lock_project(connection, context.target_id)
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, owned.id, payload.expected_revision)
            draft = payload.model_dump(mode="json", exclude={"expected_revision"})
            policy = dict(owned.access_policy)
            policy["management_guide_draft"] = draft
            policy["annotation_guide"] = payload.annotation_guide
            # A draft policy is not completeness evidence or a compiler recipe.
            policy.pop("auto_accept_qualification", None)
            policy["management_setup_approvals"] = {}
            connection.execute(
                update(project).where(project.c.id == owned.id).values(access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            self.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {
                    "guide_draft": draft,
                    "invalidated_steps": [
                        "scope",
                        "classes",
                        "evidence",
                        "guide",
                        "sources",
                        "publish",
                    ],
                },
            )
            return result

    def save_class_catalog_draft(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ClassCatalogDraft
    ) -> Resource:
        self.authorize(actor, context, "save_class_catalog_draft", context.target_id, payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            owned = self.lock_project(connection, context.target_id)
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, owned.id, payload.expected_revision)
            ontology_key = connection.execute(
                select(ontology_release.c.ontology_key)
                .where(ontology_release.c.project_id == owned.id)
                .order_by(ontology_release.c.version.desc())
                .limit(1)
            ).scalar_one_or_none()
            if ontology_key is None:
                refuse("missing_evidence")
            if ontology_key != payload.base_ontology_id:
                refuse("revision_conflict")
            draft = payload.model_dump(mode="json", exclude={"expected_revision"})
            policy = dict(owned.access_policy)
            policy["management_class_catalog_draft"] = draft
            # Never edit released identities or export indices in a draft save.
            policy.pop("auto_accept_qualification", None)
            policy["management_setup_approvals"] = {}
            connection.execute(
                update(project).where(project.c.id == owned.id).values(access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            self.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {
                    "class_catalog_draft": draft,
                    "invalidated_steps": [
                        "scope",
                        "classes",
                        "evidence",
                        "guide",
                        "sources",
                        "publish",
                    ],
                },
            )
            return result

    def publish_class_catalog(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ClassCatalogPublish
    ) -> Resource:
        from .management_project_reads import class_catalog_draft

        self.authorize(actor, context, "publish_class_catalog", context.target_id, payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            owned = self.lock_project(connection, context.target_id)
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, owned.id, payload.expected_revision)
            ontology = connection.execute(
                select(ontology_release)
                .where(ontology_release.c.project_id == owned.id)
                .order_by(ontology_release.c.version.desc())
                .limit(1)
            ).one_or_none()
            if ontology is None or ontology.ontology_key != payload.base_ontology_id:
                refuse("revision_conflict")
            validate_ontology(ontology)
            draft = class_catalog_draft(connection, owned, self.workspace_id, ontology.ontology_key)
            if draft is None:
                refuse("missing_evidence")
            catalog = draft.model_dump(mode="json")
            if draft_sha256(catalog) != payload.draft_sha256:
                refuse("revision_conflict")
            proposed_ids = {item.stable_id for item in draft.classes}
            # No implicit deletion, merge or split of an established identity.
            if not ontology.class_map.keys() <= proposed_ids:
                refuse("unsupported_classes")
            read_setup_approvals(connection, owned, self.workspace_id)
            require_retired_references(connection, owned, self.workspace_id)
            mapping = dict(ontology.class_map)
            for identifier in sorted(proposed_ids - mapping.keys()):
                mapping[identifier] = len(mapping)
            ontology_id = str(uuid4())
            connection.execute(
                insert(ontology_release).values(
                    ontology_key=ontology_id,
                    project_id=owned.id,
                    version=ontology.version + 1,
                    classes=canonical_classes(catalog),
                    class_map=mapping,
                )
            )
            policy = dict(owned.access_policy)
            policy.pop("management_class_catalog_draft")
            policy["management_class_catalog_publication"] = {
                "ontology_id": ontology_id,
                "receipt_revision": payload.expected_revision + 1,
            }
            policy["management_setup_approvals"] = {}
            policy.pop("auto_accept_qualification", None)
            connection.execute(
                update(project).where(project.c.id == owned.id).values(access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            self.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {
                    "class_catalog": catalog,
                    "draft_sha256": payload.draft_sha256,
                    "ontology_id": ontology_id,
                    "ontology_version": ontology.version + 1,
                    "class_map": mapping,
                    "reason": payload.reason,
                },
            )
            return result

    def save_source_policy_draft(
        self, actor: ManagementPrincipal, context: MutationContext, payload: SourcePolicyDraft
    ) -> Resource:
        self.authorize(actor, context, "save_source_policy_draft", context.target_id, payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            owned = self.lock_project(connection, context.target_id)
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, owned.id, payload.expected_revision)
            draft = payload.model_dump(mode="json", exclude={"expected_revision"})
            policy = dict(owned.access_policy)
            policy["management_source_policy_draft"] = draft
            # Grouping intent cannot change materialized assignments or retain
            # acceptance qualified against an earlier source policy.
            policy.pop("auto_accept_qualification", None)
            policy["management_setup_approvals"] = {}
            connection.execute(
                update(project).where(project.c.id == owned.id).values(access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            self.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {
                    "source_policy_draft": draft,
                    "invalidated_steps": [
                        "scope",
                        "classes",
                        "evidence",
                        "guide",
                        "sources",
                        "publish",
                    ],
                },
            )
            return result

    def resolve_source_pair(
        self, actor: ManagementPrincipal, context: MutationContext, payload: SourcePairResolve
    ) -> Resource:
        self.authorize(actor, context, "resolve_source_pair", context.target_id, payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            owned = self.lock_project(connection, context.target_id)
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, owned.id, payload.expected_revision)
            evidence = resolve_pair(connection, owned, payload)
            policy = dict(owned.access_policy)
            policy.pop("auto_accept_qualification", None)
            policy["management_setup_approvals"] = {}
            connection.execute(
                update(project).where(project.c.id == owned.id).values(access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            self.record(
                connection, context, str(owned.id), payload.expected_revision, result, evidence
            )
            return result

    def approve_setup_step(
        self, actor: ManagementPrincipal, context: MutationContext, payload: SetupStepApprove
    ) -> Resource:
        # Import at the composition edge: the read projections share this module's
        # fail-closed error helper, and verify draft rows against their receipts.
        from .management_project_reads import guide_draft, scope_draft, source_policy_draft

        self.authorize(actor, context, "approve_setup_step", context.target_id, payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            owned = self.lock_project(connection, context.target_id)
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, owned.id, payload.expected_revision)
            read_setup_approvals(connection, owned, self.workspace_id)
            if payload.step == "classes":
                content = published_catalog_content(connection, owned, self.workspace_id)
            else:
                loader = {
                    "scope": scope_draft,
                    "guide": guide_draft,
                    "sources": source_policy_draft,
                }[payload.step]
                draft = loader(connection, owned, self.workspace_id)
                content = (
                    draft.model_dump(mode="json", exclude={"expected_revision"})
                    if draft is not None
                    else None
                )
            if content is None:
                refuse("missing_evidence")
            digest = draft_sha256(content)
            if digest != payload.draft_sha256:
                refuse("revision_conflict")
            policy = dict(owned.access_policy)
            approvals = dict(policy.get("management_setup_approvals", {}))
            if payload.step in approvals:
                refuse("revision_conflict")
            approvals[payload.step] = {
                "draft_sha256": digest,
                "receipt_revision": payload.expected_revision + 1,
            }
            policy["management_setup_approvals"] = approvals
            policy.pop("auto_accept_qualification", None)
            connection.execute(
                update(project).where(project.c.id == owned.id).values(access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            self.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {"step": payload.step, "draft_sha256": digest, "reason": payload.reason},
            )
            return result

    def enroll_reference(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ReferenceEnroll
    ) -> Resource:
        self.authorize(actor, context, "enroll_reference", context.target_id, payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            owned = self.lock_project(connection, context.target_id)
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, owned.id, payload.expected_revision)
            reference = create_reference(connection, owned, payload)
            policy = dict(owned.access_policy)
            policy.pop("auto_accept_qualification", None)
            policy["management_setup_approvals"] = {}
            connection.execute(
                update(project).where(project.c.id == owned.id).values(access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            self.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {
                    "reference": reference.model_dump(mode="json"),
                    "ontology_id": payload.base_ontology_id,
                    "assignment_version": payload.assignment_version,
                    "invalidated_steps": [
                        "scope",
                        "classes",
                        "evidence",
                        "guide",
                        "sources",
                        "publish",
                    ],
                },
            )
            return result

    def accept_reference(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ReferenceAccept
    ) -> Resource:
        self.authorize(actor, context, "accept_reference", context.target_id, payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            owned = self.lock_project(connection, context.target_id)
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, owned.id, payload.expected_revision)
            reference = approve_reference(connection, owned, self.workspace_id, payload)
            policy = dict(owned.access_policy)
            policy.pop("auto_accept_qualification", None)
            policy["management_setup_approvals"] = {}
            connection.execute(
                update(project).where(project.c.id == owned.id).values(access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            self.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {
                    "reference": reference.model_dump(mode="json"),
                    "ontology_id": payload.base_ontology_id,
                    "assignment_version": payload.assignment_version,
                    "reason": payload.reason,
                    "context_sha256": reference_context_sha256(owned),
                    "invalidated_steps": [
                        "scope",
                        "classes",
                        "evidence",
                        "guide",
                        "sources",
                        "publish",
                    ],
                },
            )
            return result

    def revalidate_reference(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ReferenceRevalidate
    ) -> Resource:
        self.authorize(actor, context, "revalidate_reference", context.target_id, payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            owned = self.lock_project(connection, context.target_id)
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, owned.id, payload.expected_revision)
            reference, pins = renew_reference(connection, owned, self.workspace_id, payload)
            policy = dict(owned.access_policy)
            policy.pop("auto_accept_qualification", None)
            policy["management_setup_approvals"] = {}
            connection.execute(
                update(project).where(project.c.id == owned.id).values(access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            self.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {
                    "reference": reference.model_dump(mode="json"),
                    **pins,
                    "previous_approval_revision": payload.previous_approval_revision,
                    "reason": payload.reason,
                    "invalidated_steps": [
                        "scope",
                        "classes",
                        "evidence",
                        "guide",
                        "sources",
                        "publish",
                    ],
                },
            )
            return result

    def withdraw_reference(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ReferenceWithdraw
    ) -> Resource:
        self.authorize(actor, context, "withdraw_reference", context.target_id, payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            owned = self.lock_project(connection, context.target_id)
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, owned.id, payload.expected_revision)
            reference = retire_reference(connection, owned, self.workspace_id, payload)
            policy = dict(owned.access_policy)
            policy.pop("auto_accept_qualification", None)
            policy["management_setup_approvals"] = {}
            connection.execute(
                update(project).where(project.c.id == owned.id).values(access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            self.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {
                    "reference": reference.model_dump(mode="json"),
                    "reason": payload.reason,
                    "invalidated_steps": [
                        "scope",
                        "classes",
                        "evidence",
                        "guide",
                        "sources",
                        "publish",
                    ],
                },
            )
            return result

    def publish_anchor_pack(self, actor, context, payload) -> Resource:
        from .management_reference_pack import AnchorPackPublish, pack_snapshot, prepare_anchor_pack

        self.authorize(actor, context, "publish_anchor_pack", context.target_id, payload)
        # Internal callers receive the same strict validation as a typed HTTP port;
        # model_copy and hand-built objects are not alternate state authority.
        payload = AnchorPackPublish.model_validate(payload.model_dump(mode="json"))
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            owned = self.lock_project(connection, context.target_id)
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, owned.id, payload.expected_revision)
            receipts = (
                connection.execute(
                    select(canonical_event.c.payload)
                    .where(
                        canonical_event.c.aggregate_id == str(owned.id),
                        canonical_event.c.event_type == "management.publish_anchor_pack",
                    )
                    .limit(1001)
                )
                .scalars()
                .all()
            )
            if len(receipts) >= 1000:
                refuse("management_unavailable")
            if any(
                item["evidence"]["plan"]["pack_id"] == payload.plan.pack_id
                and item["evidence"]["plan"]["version"] == payload.plan.version
                for item in receipts
            ):
                refuse("revision_conflict")
            pack = prepare_anchor_pack(connection, owned, self.workspace_id, payload.plan)
            snapshot, digest = pack_snapshot(pack)
            policy = dict(owned.access_policy)
            policy.pop("auto_accept_qualification", None)
            policy["management_setup_approvals"] = {}
            connection.execute(
                update(project).where(project.c.id == owned.id).values(access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            self.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {
                    "plan": payload.plan.model_dump(mode="json"),
                    "snapshot": snapshot,
                    "snapshot_sha256": digest,
                    "context_sha256": reference_context_sha256(owned),
                    "reason": payload.reason,
                    "invalidated_steps": [
                        "scope",
                        "classes",
                        "evidence",
                        "guide",
                        "sources",
                        "publish",
                    ],
                },
            )
            return result

    def freeze_release(
        self, actor: ManagementPrincipal, context: MutationContext, payload: ReleaseFreeze
    ) -> Resource:
        self.authorize(actor, context, "freeze_release", context.target_id, payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            owned = self.lock_project(connection, context.target_id)
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, owned.id, payload.expected_revision)
            release_id = str(uuid4())
            candidate = freeze_candidate(connection, owned, payload, release_id)
            snapshot_sha256 = hashlib.sha256(
                json.dumps(asdict(candidate), sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            ontology_uuid = connection.execute(
                select(ontology_release.c.id).where(
                    ontology_release.c.project_id == owned.id,
                    ontology_release.c.ontology_key == payload.ontology_id,
                )
            ).scalar_one()
            connection.execute(
                insert(canonical_dataset_release).values(
                    id=UUID(release_id),
                    release_key=release_id,
                    project_id=owned.id,
                    ontology_id=ontology_uuid,
                    assignment_version=payload.assignment_version,
                    sample_revisions=payload.annotation_revisions,
                    ontology_hash=candidate.ontology_hash(),
                    excluded_asset_ids=candidate.excluded_asset_ids,
                    hashes={"canonical_snapshot_sha256": snapshot_sha256},
                    frozen=True,
                )
            )
            result = Resource(
                id=release_id,
                project_id=str(owned.id),
                kind="dataset-release",
                revision=1,
                state="frozen",
            )
            self.record(
                connection,
                context,
                str(owned.id),
                payload.expected_revision,
                result,
                {
                    "canonical_snapshot_sha256": snapshot_sha256,
                    "acceptance_policy_id": "canonical-training-eligibility-v1",
                },
            )
            return result

    def ingest_asset(
        self, actor: ManagementPrincipal, context: MutationContext, payload: AssetIngest
    ) -> Resource:
        self.authorize(actor, context, "ingest_asset", context.target_id, payload)
        with self.engine.begin() as connection:
            self.begin_mutation(connection, context)
            # Authorize the canonical resource before returning even a replay.
            owned = self.lock_project(connection, context.target_id)
            project_id = owned.id
            if replay := self.replay(connection, context):
                return replay
            self.check_project_revision(connection, project_id, payload.expected_revision)
            asset_id = str(uuid4())
            try:
                asset = ingest_canonical_asset(
                    connection,
                    self.store,
                    payload.original_base64,
                    project_id=str(project_id),
                    asset_id=asset_id,
                    source_uri=payload.source_uri,
                    actor_id=actor.id,
                    actor_kind="human",
                )
            except ValueError:
                refuse("invalid_request")
            result = Resource(
                id=asset_id, project_id=str(project_id), kind="asset", revision=1, state="draft"
            )
            self.record(
                connection,
                context,
                str(project_id),
                payload.expected_revision,
                result,
                {
                    "source_group": payload.source_group,
                    "source_metadata": payload.source_metadata,
                    "source_sha256": asset.sha256,
                    "imaging_toolchain": imaging_toolchain(),
                },
            )
            return result
