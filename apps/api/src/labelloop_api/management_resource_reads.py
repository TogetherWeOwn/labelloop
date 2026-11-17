"""Completed resource indices reuse detail authority within the caller's read snapshot."""

import hashlib
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import String, cast, literal, select, text

from labelloop_core.json_records import canonical_json

from .management_canonical import refuse
from .management_contracts import ExportRead, Resource
from .management_evaluation import verified_model
from .management_evaluation_reads import evaluation_detail
from .management_training import verified_variant
from .management_training_reads import training_detail
from .model_schema import evaluation_run, model_release, training_run
from .model_store import variant_context
from .schema import canonical_dataset_release, canonical_event, yolo_dataset_variant

PUBLISHED_KINDS = {"variant", "training-run", "evaluation", "model"}


def published_rows(owned, kind, workspace_id):
    if kind == "model":
        table, public_id = model_release, model_release.c.release_key
        source = table.join(owned, table.c.project_id == owned.c.id)
    elif kind == "variant":
        table, public_id = yolo_dataset_variant, cast(yolo_dataset_variant.c.id, String)
        source = table.join(canonical_dataset_release).join(
            owned, canonical_dataset_release.c.project_id == owned.c.id
        )
    else:
        table = training_run if kind == "training-run" else evaluation_run
        public_id = table.c.run_key
        source = (
            table.join(yolo_dataset_variant, table.c.variant_id == yolo_dataset_variant.c.id)
            .join(canonical_dataset_release)
            .join(owned, canonical_dataset_release.c.project_id == owned.c.id)
        )
    query = select(
        table.c.id.label("cursor_id"),
        public_id.label("id"),
        cast(owned.c.id, String).label("project_id"),
        literal(1).label("revision"),
        literal("completed").label("state"),
    ).select_from(source)
    if kind == "evaluation":
        # Checkpoint-development reports share this table. They are already
        # exposed inside training details, not standalone evaluation resources.
        query = query.where(
            select(canonical_event.c.id)
            .where(
                canonical_event.c.event_type == "management.start_evaluation",
                canonical_event.c.actor_kind == "human",
                canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
                canonical_event.c.payload["result"]["id"].as_string() == public_id,
            )
            .exists()
        )
    return query


def export_detail(connection, row, workspace_id):
    release, job_id = verified_model(connection, row, workspace_id)
    run = connection.execute(
        select(training_run, literal(row.project_id).label("project_id")).where(
            training_run.c.id == row.training_run_id
        )
    ).one_or_none()
    if run is None:
        refuse("missing_evidence")
    # Reuse training's intent, compiler and saved-stage checks in this same
    # snapshot; an export receipt alone is not lineage.
    detail = training_detail(connection, run, workspace_id)
    stage = connection.execute(
        text(
            "SELECT payload,sha256 FROM worker_stage_artifact WHERE job_id=:id AND stage='export'"
        ),
        {"id": job_id},
    ).one_or_none()
    if (
        stage is None
        or stage.sha256 != hashlib.sha256(canonical_json(stage.payload)).hexdigest()
        or detail.resource.project_id != str(row.project_id)
        or detail.recipe_id != release.recipe_id
    ):
        refuse("missing_evidence")
    return ExportRead(
        model_id=release.release_id,
        recipe_id=release.recipe_id,
        weights_sha256=release.weights_sha256,
        artifact_sha256=release.export_sha256,
        format="onnx",
        input_hw=release.static_input_signature[2:],
        artifact_id=release.export_sha256,
    )


def published_resource(connection, kind, row, workspace_id):
    try:
        if kind == "variant":
            context = variant_context(connection, row.cursor_id)
            verified_variant(
                connection,
                SimpleNamespace(**context, immutable=context["recipe_immutable"]),
                workspace_id,
            )
        elif kind == "model":
            model = connection.execute(
                select(model_release).where(model_release.c.id == row.cursor_id)
            ).one()
            export_detail(connection, model, workspace_id)
        else:
            table, validate = (
                (training_run, training_detail)
                if kind == "training-run"
                else (evaluation_run, evaluation_detail)
            )
            record = connection.execute(
                select(table, literal(UUID(row.project_id)).label("project_id")).where(
                    table.c.id == row.cursor_id
                )
            ).one()
            return validate(connection, record, workspace_id).resource
        return Resource(
            id=row.id, project_id=row.project_id, kind=kind, revision=1, state="completed"
        )
    except (KeyError, TypeError, ValueError):
        refuse("missing_evidence")
