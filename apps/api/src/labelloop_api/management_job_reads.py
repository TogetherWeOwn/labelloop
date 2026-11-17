"""Owned management intents and native queue observations, never artifact authority."""

from sqlalchemy import String, cast, column, select, table, text

from labelloop_core.json_records import canonical_json

from .management_canonical import refuse
from .management_contracts import Resource
from .schema import canonical_event, project

OPERATIONS = {
    "management.compile_variant": ("compile_variant", "variant"),
    "management.start_training": ("train_local", "training-run"),
    "management.start_evaluation": ("evaluate_local", "evaluation"),
}


def intent_predicates(workspace_id):
    return (
        canonical_event.c.event_type.in_(OPERATIONS),
        canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
    )


def job_rows(owned, workspace_id):
    jobs = table("worker_job", column("job_id", String), column("project_key", String))
    # One stable UUID cursor per intent, not a UUID cast of an arbitrary queue ID.
    # Duplicate receipts remain an integrity failure in job_resource, not extra jobs.
    first_receipt = (
        select(canonical_event.c.id)
        .where(
            *intent_predicates(workspace_id),
            canonical_event.c.payload["evidence"]["job_id"].as_string() == jobs.c.job_id,
        )
        .order_by(canonical_event.c.id)
        .limit(1)
        .correlate(jobs)
        .scalar_subquery()
    )
    return (
        select(
            canonical_event.c.id.label("cursor_id"),
            jobs.c.job_id.label("id"),
            cast(owned.c.id, String).label("project_id"),
        )
        .select_from(jobs)
        .join(project, project.c.project_key == jobs.c.project_key)
        .join(owned, owned.c.id == project.c.id)
        .join(canonical_event, canonical_event.c.id == first_receipt)
        .where(
            canonical_event.c.payload["result"]["project_id"].as_string()
            == cast(owned.c.id, String)
        )
    )


def job_resource(connection, row, workspace_id):
    receipts = connection.execute(
        select(canonical_event)
        .where(
            *intent_predicates(workspace_id),
            canonical_event.c.payload["evidence"]["job_id"].as_string() == row.id,
        )
        .limit(2)
    ).all()
    if len(receipts) != 1:
        refuse("missing_evidence")
    receipt = receipts[0]
    payload = receipt.payload
    evidence = payload["evidence"]
    inputs = evidence["inputs"]
    accepted = Resource.model_validate(payload["result"])
    job = connection.execute(
        text(
            "SELECT j.kind,j.input,j.status,j.generation,p.id AS project_id, "
            "a.generation AS applied_generation FROM worker_job j "
            "JOIN project p ON p.project_key=j.project_key "
            "LEFT JOIN worker_application a ON a.job_id=j.job_id WHERE j.job_id=:id"
        ),
        {"id": row.id},
    ).one()
    kind, resource_kind = OPERATIONS[receipt.event_type]
    if (
        receipt.actor_kind != "human"
        or not receipt.actor_id
        or inputs["requested_by"] != receipt.actor_id
        or inputs["workspace_id"] != str(workspace_id)
        or canonical_json(inputs) != canonical_json(job.input)
        or job.kind != kind
        or str(job.project_id) != row.project_id
        or accepted.project_id != row.project_id
        or accepted.kind != resource_kind
        or accepted.state != "queued"
        or accepted.revision != 1
        or type(job.generation) is not int
        or job.generation < 0
        or (job.status == "succeeded" and job.applied_generation != job.generation)
        or (job.status != "succeeded" and job.applied_generation is not None)
    ):
        refuse("missing_evidence")
    if kind == "compile_variant":
        identity, target = inputs["variant_id"], inputs["release_id"]
        if inputs["project_id"] != row.project_id:
            refuse("missing_evidence")
    elif kind == "train_local":
        identity, target = inputs["request"]["run_id"], inputs["variant_id"]
        if inputs["request"]["project_id"] != row.project_id:
            refuse("missing_evidence")
    else:
        identity, target = inputs["run_id"], inputs["model_id"]
        if inputs["project_id"] != row.project_id:
            refuse("missing_evidence")
    if accepted.id != identity or receipt.aggregate_id != target:
        refuse("missing_evidence")
    return Resource(
        id=row.id,
        project_id=row.project_id,
        kind="job",
        # This is the immutable accepted-intent revision, not a lifecycle write token.
        revision=accepted.revision,
        state="completed" if job.status == "succeeded" else job.status,
    )
