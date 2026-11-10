"""Bounded canonical summaries; no secrets, provider metadata or live route binding."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine, String, case, cast, func, literal, select, text

from .management_canonical import refuse
from .management_contracts import ExportRead, Resource, ResourceKind, ResourcePage
from .management_details import (
    EvaluationDetail,
    FreezePlanRead,
    ProjectDetail,
    RecipePreviewRead,
    ReferenceLifecycleList,
    ReleaseDetail,
    TrainingDetail,
)
from .management_freeze_plan import freeze_plan
from .management_ports import ManagementPrincipal
from .management_project_reads import project_detail
from .management_references import reference_lifecycle
from .management_release_reads import release_detail
from .schema import (
    annotation_revision,
    canonical_dataset_release,
    canonical_event,
    project,
    source_asset,
)


def authorize_reader(actor: ManagementPrincipal) -> None:
    if (
        not actor.id
        or actor.kind != "human"
        or actor.role not in {"admin", "reviewer"}
        or not actor.permits("review")
    ):
        refuse("permission_denied")


def page_position(workspace_id: UUID, target: str, kind: str, cursor: str | None, limit: int):
    if type(limit) is not int or not 1 <= limit <= 100:
        refuse("invalid_request")
    # A cursor is an untrusted keyset position, not a permission or a snapshot.
    # Its scope tag prevents accidental cross-project/kind/workspace reuse;
    # every query independently applies ownership and authority checks.
    scope = hashlib.sha256(
        json.dumps([str(workspace_id), target, kind], separators=(",", ":")).encode()
    ).hexdigest()[:32]
    if cursor is None:
        return scope, None
    if not isinstance(cursor, str) or len(cursor) != 65 or cursor[:33] != scope + ".":
        refuse("invalid_request")
    try:
        position = UUID(hex=cursor[33:])
    except ValueError:
        refuse("invalid_request")
    if position.hex != cursor[33:]:
        refuse("invalid_request")
    return scope, position


@dataclass(frozen=True)
class CanonicalManagementReads:
    engine: Engine
    workspace_id: UUID

    def __post_init__(self) -> None:
        if self.engine.dialect.name != "postgresql" or not isinstance(self.workspace_id, UUID):
            raise ValueError("canonical management requires Postgres and a trusted workspace UUID")

    def project_rows(self):
        revisions = (
            select(func.max(canonical_event.c.expected_revision) + 1)
            .where(
                canonical_event.c.aggregate_id == cast(project.c.id, String),
                canonical_event.c.event_type.like("management.%"),
                canonical_event.c.payload["workspace_id"].as_string() == str(self.workspace_id),
            )
            .correlate(project)
            .scalar_subquery()
        )
        return (
            select(project.c.id, revisions.label("revision"))
            .where(project.c.workspace_id == self.workspace_id, revisions.is_not(None))
            .subquery()
        )

    def rows(self, kind: ResourceKind):
        from .management_resource_reads import PUBLISHED_KINDS, published_rows

        owned = self.project_rows()
        if kind in PUBLISHED_KINDS:
            return published_rows(owned, kind, self.workspace_id)
        if kind == "project":
            return select(
                owned.c.id.label("cursor_id"),
                cast(owned.c.id, String).label("id"),
                cast(owned.c.id, String).label("project_id"),
                owned.c.revision,
                literal("draft").label("state"),
            )
        if kind == "asset":
            # The summary revision is the current annotation revision, not the
            # project's write revision or a historical ingest receipt.
            latest = (
                select(func.max(annotation_revision.c.revision))
                .where(annotation_revision.c.asset_id == source_asset.c.id)
                .correlate(source_asset)
                .scalar_subquery()
            )
            return (
                select(
                    source_asset.c.id.label("cursor_id"),
                    source_asset.c.asset_key.label("id"),
                    cast(owned.c.id, String).label("project_id"),
                    latest.label("revision"),
                    literal("draft").label("state"),
                )
                .join(owned, source_asset.c.project_id == owned.c.id)
                .where(latest.is_not(None))
            )
        if kind == "dataset-release":
            return select(
                canonical_dataset_release.c.id.label("cursor_id"),
                canonical_dataset_release.c.release_key.label("id"),
                cast(owned.c.id, String).label("project_id"),
                literal(1).label("revision"),
                case((canonical_dataset_release.c.frozen, "frozen"), else_="draft").label("state"),
            ).join(owned, canonical_dataset_release.c.project_id == owned.c.id)
        refuse("management_unavailable")

    def list_projects(
        self, actor: ManagementPrincipal, cursor: str | None, limit: int
    ) -> ResourcePage:
        authorize_reader(actor)
        return self.page("project", None, cursor, limit)

    def list_resources(
        self,
        actor: ManagementPrincipal,
        project_id: str,
        kind: ResourceKind,
        cursor: str | None,
        limit: int,
    ) -> ResourcePage:
        authorize_reader(actor)
        # Resolve before reporting an unsupported producer or malformed cursor;
        # inaccessible project identifiers all have the same not-found result.
        self.read(actor, "project", project_id)
        return self.page(kind, project_id, cursor, limit)

    def page(
        self, kind: ResourceKind, project_id: str | None, cursor: str | None, limit: int
    ) -> ResourcePage:
        scope, position = page_position(
            self.workspace_id, project_id or "projects", kind, cursor, limit
        )
        rows = self.rows(kind).subquery()
        query = select(rows).order_by(rows.c.cursor_id).limit(limit + 1)
        if project_id is not None:
            query = query.where(rows.c.project_id == project_id)
        if position is not None:
            query = query.where(rows.c.cursor_id > position)
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                found = connection.execute(query).all()
                return ResourcePage(
                    items=[self.resource(connection, kind, row) for row in found[:limit]],
                    next_cursor=f"{scope}.{found[limit - 1].cursor_id.hex}"
                    if len(found) > limit
                    else None,
                )

    def read(self, actor: ManagementPrincipal, kind: ResourceKind, resource_id: str) -> Resource:
        authorize_reader(actor)
        rows = self.rows(kind).subquery()
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                row = connection.execute(select(rows).where(rows.c.id == resource_id)).one_or_none()
                if row is None:
                    refuse("not_found")
                return self.resource(connection, kind, row)

    def read_project(self, actor: ManagementPrincipal, project_id: str) -> ProjectDetail:
        authorize_reader(actor)
        owned = self.project_rows()
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                row = connection.execute(
                    select(project, owned.c.revision.label("management_revision"))
                    .join(owned, project.c.id == owned.c.id)
                    .where(cast(project.c.id, String) == project_id)
                ).one_or_none()
                if row is None:
                    refuse("not_found")
                try:
                    return project_detail(
                        connection, row, row.management_revision, self.workspace_id
                    )
                except (KeyError, TypeError, ValueError):
                    refuse("missing_evidence")

    def read_reference_lifecycle(
        self, actor: ManagementPrincipal, project_id: str
    ) -> ReferenceLifecycleList:
        authorize_reader(actor)
        owned = self.project_rows()
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                row = connection.execute(
                    select(project, owned.c.revision.label("management_revision"))
                    .join(owned, project.c.id == owned.c.id)
                    .where(cast(project.c.id, String) == project_id)
                ).one_or_none()
                if row is None:
                    refuse("not_found")
                try:
                    return reference_lifecycle(
                        connection, row, row.management_revision, self.workspace_id
                    )
                except (KeyError, TypeError, ValueError):
                    refuse("missing_evidence")

    def read_freeze_plan(self, actor: ManagementPrincipal, project_id: str) -> FreezePlanRead:
        authorize_reader(actor)
        owned = self.project_rows()
        # One snapshot for ownership, revision, labels, completeness and grouping.
        # This read must not take mutation locks or require UPDATE/INSERT grants.
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                row = connection.execute(
                    select(project, owned.c.revision.label("management_revision"))
                    .join(owned, project.c.id == owned.c.id)
                    .where(cast(project.c.id, String) == project_id)
                ).one_or_none()
                if row is None:
                    refuse("not_found")
                return freeze_plan(connection, row, row.management_revision)

    def read_release(self, actor: ManagementPrincipal, release_id: str) -> ReleaseDetail:
        authorize_reader(actor)
        owned = self.project_rows()
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                row = connection.execute(
                    select(canonical_dataset_release)
                    .join(owned, canonical_dataset_release.c.project_id == owned.c.id)
                    .where(canonical_dataset_release.c.release_key == release_id)
                ).one_or_none()
                if row is None:
                    refuse("not_found")
                try:
                    return release_detail(connection, row, self.workspace_id)
                except (KeyError, TypeError, ValueError):
                    refuse("missing_evidence")

    def read_recipe_preview(
        self, actor: ManagementPrincipal, release_id: str, recipe_id: str, asset_id: str
    ) -> RecipePreviewRead:
        from .management_recipe_reads import recipe_preview
        from .model_store import variant_context
        from .schema import preprocessing_recipe, yolo_dataset_variant

        authorize_reader(actor)
        owned = self.project_rows()
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                release = connection.execute(
                    select(canonical_dataset_release)
                    .join(owned, canonical_dataset_release.c.project_id == owned.c.id)
                    .where(canonical_dataset_release.c.release_key == release_id)
                ).one_or_none()
                if release is None:
                    refuse("not_found")
                variant_id = connection.execute(
                    select(yolo_dataset_variant.c.id)
                    .join(preprocessing_recipe)
                    .where(
                        yolo_dataset_variant.c.release_id == release.id,
                        preprocessing_recipe.c.recipe_key == recipe_id,
                    )
                ).scalar_one_or_none()
                if variant_id is None:
                    refuse("missing_evidence")
                try:
                    return recipe_preview(
                        connection,
                        variant_context(connection, variant_id),
                        self.workspace_id,
                        asset_id,
                    )
                except (KeyError, TypeError, ValueError):
                    refuse("missing_evidence")

    def read_evaluation(self, actor: ManagementPrincipal, evaluation_id: str) -> "EvaluationDetail":
        from .management_evaluation_reads import evaluation_detail
        from .model_schema import evaluation_run
        from .schema import yolo_dataset_variant

        authorize_reader(actor)
        owned = self.project_rows()
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                row = connection.execute(
                    select(evaluation_run, owned.c.id.label("project_id"))
                    .select_from(evaluation_run)
                    .join(
                        yolo_dataset_variant,
                        evaluation_run.c.variant_id == yolo_dataset_variant.c.id,
                    )
                    .join(
                        canonical_dataset_release,
                        yolo_dataset_variant.c.release_id == canonical_dataset_release.c.id,
                    )
                    .join(owned, canonical_dataset_release.c.project_id == owned.c.id)
                    .where(evaluation_run.c.run_key == evaluation_id)
                ).one_or_none()
                if row is None:
                    refuse("not_found")
                try:
                    return evaluation_detail(connection, row, self.workspace_id)
                except (KeyError, TypeError, ValueError):
                    refuse("missing_evidence")

    def read_training(self, actor: ManagementPrincipal, run_id: str) -> TrainingDetail:
        from .management_training_reads import training_detail
        from .model_schema import training_run
        from .schema import yolo_dataset_variant

        authorize_reader(actor)
        owned = self.project_rows()
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                row = connection.execute(
                    select(training_run, owned.c.id.label("project_id"))
                    .select_from(training_run)
                    .join(
                        yolo_dataset_variant,
                        training_run.c.variant_id == yolo_dataset_variant.c.id,
                    )
                    .join(
                        canonical_dataset_release,
                        yolo_dataset_variant.c.release_id == canonical_dataset_release.c.id,
                    )
                    .join(owned, canonical_dataset_release.c.project_id == owned.c.id)
                    .where(training_run.c.run_key == run_id)
                ).one_or_none()
                if row is None:
                    refuse("not_found")
                try:
                    return training_detail(connection, row, self.workspace_id)
                except (KeyError, TypeError, ValueError):
                    refuse("missing_evidence")

    def read_export(self, actor: ManagementPrincipal, model_id: str) -> ExportRead:
        from .management_resource_reads import export_detail
        from .model_schema import model_release

        authorize_reader(actor)
        owned = self.project_rows()
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                row = connection.execute(
                    select(model_release)
                    .join(owned, model_release.c.project_id == owned.c.id)
                    .where(model_release.c.release_key == model_id)
                ).one_or_none()
                if row is None:
                    refuse("not_found")
                try:
                    return export_detail(connection, row, self.workspace_id)
                except (KeyError, TypeError, ValueError):
                    refuse("missing_evidence")

    def resource(self, connection, kind: ResourceKind, row) -> Resource:
        from .management_resource_reads import PUBLISHED_KINDS, published_resource

        if kind in PUBLISHED_KINDS:
            return published_resource(connection, kind, row, self.workspace_id)
        return Resource(
            id=row.id, project_id=row.project_id, kind=kind, revision=row.revision, state=row.state
        )
