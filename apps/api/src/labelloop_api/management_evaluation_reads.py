"""Completed development reports from canonical lineage and native application evidence."""

import hashlib
from dataclasses import asdict
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select, text

from labelloop_core.json_records import canonical_json

from .management_canonical import refuse
from .management_compile_worker import content_key
from .management_contracts import Resource
from .management_details import CheckRead, EvaluationDetail
from .management_evaluation import verified_model
from .management_training import verified_variant
from .model_schema import model_evaluation, model_release
from .model_store import decode_report, record_digest, validate_evaluation, variant_context
from .schema import canonical_event


def evaluation_detail(connection, row, workspace_id: UUID) -> EvaluationDetail:
    receipts = (
        connection.execute(
            select(canonical_event.c.payload).where(
                canonical_event.c.event_type == "management.evaluation_completed",
                canonical_event.c.aggregate_id == row.run_key,
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
    saved = connection.execute(
        text(
            "SELECT j.input,j.project_key,j.kind,j.status,j.generation,"
            "a.generation AS applied,s.payload,s.sha256 "
            "FROM worker_job j JOIN worker_application a ON a.job_id=j.job_id "
            "JOIN worker_stage_artifact s ON s.job_id=j.job_id AND s.stage='evaluation' "
            "WHERE j.job_id=:id"
        ),
        {"id": evidence["job_id"]},
    ).one_or_none()
    if saved is None:
        refuse("missing_evidence")
    inputs = saved.input
    intent = (
        connection.execute(
            select(canonical_event.c.payload).where(
                canonical_event.c.event_type == "management.start_evaluation",
                canonical_event.c.aggregate_id == evidence["model_id"],
                canonical_event.c.actor_kind == "human",
                canonical_event.c.actor_id == inputs["requested_by"],
                canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
                canonical_event.c.payload["evidence"]["job_id"].as_string() == evidence["job_id"],
            )
        )
        .scalars()
        .all()
    )
    model = connection.execute(
        select(model_release)
        .join(model_evaluation, model_evaluation.c.model_id == model_release.c.id)
        .where(
            model_evaluation.c.evaluation_id == row.id,
            model_release.c.release_key == evidence["model_id"],
            model_release.c.project_id == row.project_id,
            model_release.c.training_run_id == row.training_run_id,
        )
    ).one_or_none()
    if model is None or len(intent) != 1:
        refuse("missing_evidence")
    release, model_job_id = verified_model(connection, model, workspace_id)
    context = variant_context(connection, row.variant_id)
    variant = SimpleNamespace(**context, immutable=context["recipe_immutable"])
    _, manifest_digest, compile_job_id = verified_variant(connection, variant, workspace_id)
    report = decode_report(row.record)
    validate_evaluation(context, release, report)
    digest = hashlib.sha256(canonical_json(saved.payload)).hexdigest()
    if (
        saved.kind != "evaluate_local"
        or saved.status != "succeeded"
        or saved.applied != saved.generation
        or evidence["generation"] != saved.generation
        or evidence["project_id"] != str(row.project_id)
        or evidence["project_key"] != saved.project_key
        or saved.project_key != release.project_id
        or evidence["variant_id"] != str(row.variant_id)
        or evidence["model_id"] != inputs["model_id"]
        or evidence["requested_by"] != inputs["requested_by"]
        or evidence["protocol"] != inputs["protocol"]
        or evidence["stage_sha256"] != digest
        or saved.sha256 != digest
        or canonical_json(saved.payload["report"]) != canonical_json(row.record)
        or canonical_json(intent[0]["evidence"]["inputs"]) != canonical_json(inputs)
        or intent[0]["result"]["id"] != row.run_key
        or inputs["run_id"] != row.run_key
        or inputs["project_id"] != str(row.project_id)
        or inputs["workspace_id"] != str(workspace_id)
        or inputs["variant_id"] != str(row.variant_id)
        or inputs["dataset_release_id"] != report.dataset_release_id
        or inputs["model_sha256"] != record_digest(model.record)
        or inputs["model_job_id"] != model_job_id
        or inputs["compile_job_id"] != compile_job_id
        or inputs["manifest_sha256"] != manifest_digest
        or inputs["manifest_key"] != content_key(manifest_digest)
        or inputs["weights_key"] != content_key(release.weights_sha256)
        or inputs["protocol"]["role"] != "development"
        or inputs["protocol"]["split"] != "val"
        or report.role != "development"
        or report.run_id != row.run_key
        or inputs["policy"] != asdict(report.policy)
    ):
        refuse("missing_evidence")
    return EvaluationDetail(
        resource=Resource(
            id=row.run_key,
            project_id=str(row.project_id),
            kind="evaluation",
            revision=1,
            state="completed",
        ),
        candidate_id=model.release_key,
        incumbent_id=None,
        report=report,
        incumbent_report=None,
        checks=[
            CheckRead(
                code="publication_integrity",
                passed=True,
                reason="Verified development report, lineage and fenced application",
            )
        ],
        qualification_id=None,
        qualification="unqualified",
        reason="Development evidence only; no qualification or incumbent comparison was performed",
        minimum_class_support=None,
        opened_final_test_count=0,
    )
