"""Completed training details from persisted intent, stage and application authority."""

import hashlib
from dataclasses import replace
from datetime import UTC
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select, text

from labelloop_core.json_records import canonical_json

from .management_canonical import refuse
from .management_compile_worker import content_key
from .management_contracts import Resource
from .management_details import RetentionRead, TrainingDetail
from .management_train_worker import decode_training, validate_runtime_evidence
from .management_training import ApprovedTrainingProfile, verified_variant
from .model_schema import evaluation_run
from .model_store import decode_report, json_record, validate_training, variant_context
from .schema import canonical_event


def training_detail(connection, row, workspace_id: UUID) -> TrainingDetail:
    receipts = connection.execute(
        select(canonical_event.c.payload, canonical_event.c.created_at).where(
            canonical_event.c.event_type == "management.training_completed",
            canonical_event.c.aggregate_id == row.run_key,
            canonical_event.c.actor_kind == "system",
            canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
        )
    ).all()
    if len(receipts) != 1:
        refuse("missing_evidence")
    evidence, occurred_at = receipts[0]
    saved = connection.execute(
        text(
            "SELECT j.input,j.project_key,j.kind,j.status,j.generation,"
            "a.generation AS applied,s.payload,s.sha256 "
            "FROM worker_job j JOIN worker_application a ON a.job_id=j.job_id "
            "JOIN worker_stage_artifact s ON s.job_id=j.job_id AND s.stage='training' "
            "WHERE j.job_id=:id"
        ),
        {"id": evidence["job_id"]},
    ).one_or_none()
    if saved is None:
        refuse("missing_evidence")
    inputs, record = saved.input, saved.payload
    intent = (
        connection.execute(
            select(canonical_event.c.payload).where(
                canonical_event.c.event_type == "management.start_training",
                canonical_event.c.aggregate_id == str(row.variant_id),
                canonical_event.c.actor_kind == "human",
                canonical_event.c.actor_id == inputs["requested_by"],
                canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
                canonical_event.c.payload["evidence"]["job_id"].as_string() == evidence["job_id"],
            )
        )
        .scalars()
        .all()
    )
    context = variant_context(connection, row.variant_id)
    manifest, digest, compile_job_id = verified_variant(
        connection, SimpleNamespace(**context, immutable=context["recipe_immutable"]), workspace_id
    )
    result = decode_training(record["result"])
    request = result.request
    profile = inputs["profile"]
    expected = ApprovedTrainingProfile(
        row.project_id,
        profile["id"],
        profile["revision"],
        profile["recipe_id"],
        profile["initialization_sha256"],
        profile["epochs"],
        request.batch,
        request.seed,
        request.policy,
    )
    stored = replace(result, request=replace(request, project_id=context["project_key"]))
    reports = tuple(decode_report(item) for item in record["reports"])
    persisted_reports = connection.execute(
        select(evaluation_run.c.run_key, evaluation_run.c.record).where(
            evaluation_run.c.training_run_id == row.id,
            evaluation_run.c.variant_id == row.variant_id,
            evaluation_run.c.run_key.in_([item.run_id for item in reports]),
        )
    ).all()
    stage_digest = hashlib.sha256(canonical_json(record)).hexdigest()
    if (
        len(intent) != 1
        or saved.kind != "train_local"
        or saved.status != "succeeded"
        or saved.generation != saved.applied
        or evidence["generation"] != saved.generation
        or evidence["project_id"] != str(row.project_id)
        or evidence["project_key"] != saved.project_key
        or saved.project_key != context["project_key"]
        or evidence["variant_id"] != str(row.variant_id)
        or evidence["requested_by"] != inputs["requested_by"]
        or evidence["stage_sha256"] != stage_digest
        or saved.sha256 != stage_digest
        or evidence["checkpoint_keys"] != record["checkpoint_keys"]
        or record["checkpoint_keys"]
        != {item.sha256: content_key(item.sha256) for item in result.checkpoints}
        or canonical_json(row.record) != canonical_json(json_record(stored))
        or canonical_json(row.input_manifest) != canonical_json(manifest)
        or canonical_json(inputs["request"]) != canonical_json(record["result"]["request"])
        or canonical_json(intent[0]["evidence"]["inputs"]) != canonical_json(inputs)
        or intent[0]["result"]["id"] != row.run_key
        or inputs["workspace_id"] != str(workspace_id)
        or inputs["variant_id"] != str(row.variant_id)
        or inputs["variant_revision"] != 1
        or inputs["compile_job_id"] != compile_job_id
        or inputs["manifest_key"] != content_key(digest)
        or inputs["initialization_key"] != content_key(request.initialization_sha256)
        or request.project_id != str(row.project_id)
        or request.run_id != row.run_key
        or expected.request(row.run_key, context["release_key"], manifest, digest) != request
        or expected.read().model_dump(mode="json") != profile
        or len(result.checkpoints) != request.epochs
        or {item.epoch for item in result.checkpoints} != set(range(request.epochs))
        or {key: value for key, value in persisted_reports}
        != {item["run_id"]: item for item in record["reports"]}
    ):
        refuse("missing_evidence")
    validate_training(context, stored, reports)
    validate_runtime_evidence(
        request, manifest, record["tensor_shapes"], record["target_retention"]
    )
    # The validated baseline permits no drops; only pinned arguments cross the port.
    arguments = request.arguments()
    return TrainingDetail(
        resource=Resource(
            id=row.run_key,
            project_id=str(row.project_id),
            kind="training-run",
            revision=1,
            state="completed",
        ),
        recipe_id=request.recipe_id,
        dataset_release_id=request.dataset_release_id,
        variant_id=str(row.variant_id),
        training_profile_id=profile["id"],
        training_profile_revision=profile["revision"],
        initialization_sha256=request.initialization_sha256,
        epoch=request.epochs,
        epochs=request.epochs,
        images=sum(item["split"] == "train" for item in manifest["samples"]),
        reason=None,
        updated_at=occurred_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        requested_arguments=arguments,
        resolved_arguments={key: result.resolved_arguments[key] for key in arguments},
        checkpoints=list(result.checkpoints),
        retention=RetentionRead(
            before=sum(item["before"] for item in record["target_retention"]),
            after=sum(item["after"] for item in record["target_retention"]),
            dropped_object_ids=[],
        ),
        mismatches=[],
    )
