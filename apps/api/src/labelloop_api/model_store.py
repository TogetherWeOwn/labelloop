"""Internal, transaction-bound persistence. No unauthenticated promotion endpoint is exposed.

Callers own the outer transaction and supply an authenticated admin's actor ID for registry
changes. Savepoints keep each operation atomic even if a caller catches a rejected operation.
Only immutable, worker-validated results enter the store; model output is never a command.
"""

import hashlib
import json
from dataclasses import asdict

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from labelloop_api import model_schema as db
from labelloop_api.annotation_audit_store import lock_project, require_no_annotation_veto
from labelloop_api.schema import (
    canonical_dataset_release,
    preprocessing_recipe,
    project,
    yolo_dataset_variant,
)
from labelloop_core.evaluation import ClassMetrics, Coverage, EvaluationReport, Metrics
from labelloop_core.qualification import QualificationContext
from labelloop_core.qualification_wire import dumps as qualification_json
from labelloop_core.registry import ModelRelease, PromotionPolicy, RegistryState
from labelloop_core.training import InferencePolicy, TrainingResult


def record_digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def json_record(value: object) -> dict:
    return json.loads(json.dumps(asdict(value), allow_nan=False))


def decode_release(value: dict) -> ModelRelease:
    return ModelRelease(
        **(
            value
            | {
                "policy": InferencePolicy(**value["policy"]),
                "class_ids": tuple(value["class_ids"]),
                "static_input_signature": tuple(value["static_input_signature"]),
                "evaluation_run_ids": tuple(value["evaluation_run_ids"]),
            }
        )
    )


def decode_report(value: dict) -> EvaluationReport:
    def metrics(item: dict) -> Metrics:
        return Metrics(
            **(
                item
                | {
                    "per_class": {
                        key: ClassMetrics(**row) for key, row in item["per_class"].items()
                    }
                }
            )
        )

    return EvaluationReport(
        **(
            value
            | {
                "policy": InferencePolicy(**value["policy"]),
                "overall": metrics(value["overall"]),
                "slices": {key: metrics(row) for key, row in value["slices"].items()},
                "coverage": tuple(Coverage(**row) for row in value["coverage"]),
            }
        )
    )


def variant_context(connection: sa.Connection, variant_id: object) -> sa.RowMapping:
    return (
        connection.execute(
            sa.select(
                yolo_dataset_variant.c.id,
                yolo_dataset_variant.c.class_map,
                yolo_dataset_variant.c.file_manifest,
                yolo_dataset_variant.c.validated,
                yolo_dataset_variant.c.non_empty_val,
                canonical_dataset_release.c.frozen,
                canonical_dataset_release.c.release_key,
                canonical_dataset_release.c.assignment_version,
                project.c.id.label("project_id"),
                project.c.project_key,
                preprocessing_recipe.c.id.label("recipe_id"),
                preprocessing_recipe.c.recipe_key,
                preprocessing_recipe.c.policy,
                preprocessing_recipe.c.immutable.label("recipe_immutable"),
            )
            .select_from(
                yolo_dataset_variant.join(canonical_dataset_release)
                .join(project)
                .join(preprocessing_recipe)
            )
            .where(yolo_dataset_variant.c.id == variant_id)
        )
        .mappings()
        .one()
    )


def save_training(
    connection: sa.Connection,
    *,
    variant_id: object,
    result: TrainingResult,
    reports: tuple[EvaluationReport, ...],
) -> None:
    context = variant_context(connection, variant_id)
    request = result.request
    if not all(
        context[key] for key in ("frozen", "validated", "non_empty_val", "recipe_immutable")
    ) or (
        request.project_id,
        request.dataset_release_id,
        request.recipe_id,
        request.split_assignment_version,
    ) != (
        context["project_key"],
        context["release_key"],
        context["recipe_key"],
        context["assignment_version"],
    ):
        raise ValueError("training does not describe a validated frozen variant")
    manifest = context["file_manifest"]
    manifest_digest = hashlib.sha256(
        (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    ).hexdigest()
    if (
        manifest_digest != request.dataset_manifest_sha256
        or manifest["file_hashes"]["class-map.json"] != request.class_map_sha256
        or manifest["class_map"] != context["class_map"]
        or manifest["recipe"] != context["policy"]
        or manifest["recipe"]["output_size"] != request.policy.imgsz
    ):
        raise ValueError("persisted variant digests or recipe do not match training")
    if len(reports) != len(result.checkpoints) or {r.run_id for r in reports} != {
        c.evaluation_run_id for c in result.checkpoints
    }:
        raise ValueError("every checkpoint needs exactly one source-frame evaluation")
    for report in reports:
        checkpoint = next(c for c in result.checkpoints if c.evaluation_run_id == report.run_id)
        if (
            report.role != "development"
            or report.dataset_release_id != request.dataset_release_id
            or report.recipe_id != request.recipe_id
            or report.weights_sha256 != checkpoint.sha256
            or report.class_map_sha256 != request.class_map_sha256
            or report.policy != request.policy
            or set(report.overall.per_class) != set(context["class_map"])
            or report.overall.map50_95 != checkpoint.validation_map
        ):
            raise ValueError("checkpoint evaluation lineage mismatch")
    with connection.begin_nested():
        # Keep the checkpoint ancestry snapshot ordered with audits and promotion.
        lock_project(connection, request.project_id)
        run_id = connection.execute(
            sa.insert(db.training_run).values(
                run_key=request.run_id,
                variant_id=variant_id,
                input_manifest=manifest,
                record=json_record(result),
            )
        ).inserted_primary_key[0]
        connection.execute(
            sa.insert(db.evaluation_run),
            [
                {
                    "run_key": report.run_id,
                    "training_run_id": run_id,
                    "variant_id": variant_id,
                    "record": json_record(report),
                }
                for report in reports
            ],
        )


def save_model(
    connection: sa.Connection,
    *,
    training_run_key: str,
    release: ModelRelease,
    export_evidence: dict,
) -> None:
    run = (
        connection.execute(
            sa.select(db.training_run).where(db.training_run.c.run_key == training_run_key)
        )
        .mappings()
        .one()
    )
    context = variant_context(connection, run["variant_id"])
    result = run["record"]
    request = result["request"]
    inputs = run["input_manifest"]
    if (
        context["file_manifest"] != inputs
        or context["policy"] != inputs["recipe"]
        or context["class_map"] != inputs["class_map"]
    ):
        raise ValueError("model inputs changed since persisted training")
    if (
        (release.project_id, release.recipe_id) != (context["project_key"], context["recipe_key"])
        or release.weights_sha256 != result["selected"]["sha256"]
        or release.class_map_sha256 != request["class_map_sha256"]
        or asdict(release.policy) != request["policy"]
        or release.preprocessing_sha256 != record_digest(inputs["recipe"])
        or release.class_ids
        != tuple(sorted(inputs["class_map"], key=inputs["class_map"].__getitem__))
        or release.ultralytics_version != result["ultralytics_version"]
        or release.torch_version != result["torch_version"]
        or result["selected"]["evaluation_run_id"] not in release.evaluation_run_ids
    ):
        raise ValueError("model release disagrees with persisted training lineage")
    if (
        export_evidence.get("weights_sha256") != release.weights_sha256
        or export_evidence.get("export_sha256") != release.export_sha256
        or tuple(export_evidence.get("static_input_signature", ()))
        != release.static_input_signature
        or export_evidence.get("policy") != asdict(release.policy)
        or export_evidence.get("sample_count", 0) < 1
        or len(export_evidence.get("detection_counts", ())) != export_evidence["sample_count"]
        or any(type(count) is not int or count < 0 for count in export_evidence["detection_counts"])
        or sum(export_evidence["detection_counts"]) < 1
        or export_evidence.get("coordinate_atol_px") != 0.01
        or export_evidence.get("confidence_atol") != 1e-5
    ):
        raise ValueError("model release needs matching static export verification")
    evaluations = (
        connection.execute(
            sa.select(db.evaluation_run).where(
                db.evaluation_run.c.run_key.in_(release.evaluation_run_ids),
                db.evaluation_run.c.training_run_id == run["id"],
            )
        )
        .mappings()
        .all()
    )
    if len(evaluations) != len(release.evaluation_run_ids):
        raise ValueError("model release evaluation is not persisted for this run")
    for evaluation in evaluations:
        report = decode_report(evaluation["record"])
        if report.weights_sha256 != release.weights_sha256 or report.policy != release.policy:
            raise ValueError("model release evaluation artifact mismatch")
    with connection.begin_nested():
        model_id = connection.execute(
            sa.insert(db.model_release).values(
                release_key=release.release_id,
                training_run_id=run["id"],
                project_id=context["project_id"],
                recipe_id=context["recipe_id"],
                record=json_record(release),
                export_evidence=export_evidence,
            )
        ).inserted_primary_key[0]
        connection.execute(
            sa.insert(db.model_evaluation),
            [{"model_id": model_id, "evaluation_id": row["id"]} for row in evaluations],
        )


def save_evaluation(
    connection: sa.Connection, *, model_key: str, variant_id: object, report: EvaluationReport
) -> None:
    """Append an evaluation of an existing checkpoint on a separately frozen release.

    This never changes training selection or the immutable initial model payload.
    Additional linked evidence is included when loading the release and in promotion events.
    """
    row = (
        connection.execute(
            sa.select(db.model_release).where(db.model_release.c.release_key == model_key)
        )
        .mappings()
        .one()
    )
    # Selection/exposure snapshots include all linked evaluations. Serialize new
    # links with their project lock so a final assessment cannot race a payload change.
    connection.execute(
        sa.select(project.c.id).where(project.c.id == row["project_id"]).with_for_update()
    ).scalar_one()
    release = decode_release(row["record"])
    context = variant_context(connection, variant_id)
    if (
        not all(
            context[key] for key in ("frozen", "validated", "non_empty_val", "recipe_immutable")
        )
        or (context["project_key"], context["recipe_key"])
        != (release.project_id, release.recipe_id)
        or report.dataset_release_id != context["release_key"]
        or report.recipe_id != release.recipe_id
        or report.weights_sha256 != release.weights_sha256
        or report.class_map_sha256 != release.class_map_sha256
        or context["file_manifest"]["file_hashes"]["class-map.json"] != release.class_map_sha256
        or context["file_manifest"]["recipe"] != context["policy"]
        or record_digest(context["policy"]) != release.preprocessing_sha256
        or tuple(sorted(context["class_map"], key=context["class_map"].__getitem__))
        != release.class_ids
        or report.policy != release.policy
        or set(report.overall.per_class) != set(release.class_ids)
        or report.role not in {"development", "calibration", "locked_final_test"}
    ):
        raise ValueError("independent evaluation does not match the model and frozen release")
    with connection.begin_nested():
        evaluation_id = connection.execute(
            sa.insert(db.evaluation_run).values(
                run_key=report.run_id,
                training_run_id=row["training_run_id"],
                variant_id=variant_id,
                record=json_record(report),
            )
        ).inserted_primary_key[0]
        connection.execute(
            sa.insert(db.model_evaluation).values(model_id=row["id"], evaluation_id=evaluation_id)
        )


def load_release(connection: sa.Connection, model_id: object | None) -> ModelRelease | None:
    if model_id is None:
        return None
    value = connection.execute(
        sa.select(db.model_release.c.record).where(db.model_release.c.id == model_id)
    ).scalar_one()
    linked = (
        connection.execute(
            sa.select(db.evaluation_run.c.run_key)
            .join(db.model_evaluation)
            .where(db.model_evaluation.c.model_id == model_id)
        )
        .scalars()
        .all()
    )
    initial = value["evaluation_run_ids"]
    return decode_release(
        value | {"evaluation_run_ids": initial + sorted(set(linked) - set(initial))}
    )


def load_registry(connection: sa.Connection, project_key: str, recipe_key: str) -> RegistryState:
    row = (
        connection.execute(
            sa.select(db.registry_head)
            .join(project)
            .join(preprocessing_recipe)
            .where(
                project.c.project_key == project_key,
                preprocessing_recipe.c.recipe_key == recipe_key,
            )
        )
        .mappings()
        .one_or_none()
    )
    return (
        RegistryState(project_key, recipe_key)
        if row is None
        else RegistryState(
            project_key,
            recipe_key,
            row["revision"],
            load_release(connection, row["champion_id"]),
            load_release(connection, row["rollback_id"]),
        )
    )


def stored_report(
    connection: sa.Connection, release: ModelRelease, run_key: str
) -> EvaluationReport:
    if run_key not in release.evaluation_run_ids:
        raise ValueError("evaluation is not linked to the model release")
    return decode_report(
        connection.execute(
            sa.select(db.evaluation_run.c.record).where(db.evaluation_run.c.run_key == run_key)
        ).scalar_one()
    )


def change_registry(
    connection: sa.Connection,
    *,
    project_key: str,
    recipe_key: str,
    actor_id: str,
    expected_revision: int,
    candidate_key: str | None = None,
    evaluation_key: str | None = None,
    policy: PromotionPolicy | None = None,
    incumbent_evaluation_key: str | None = None,
    qualification: QualificationContext | None = None,
) -> RegistryState:
    """Lock one recipe, validate persisted evidence, then commit head and event together.

    candidate_key=None requests rollback. The caller must authenticate/authorize the admin;
    this internal store deliberately does not expose principal claims through HTTP.
    Qualification is a trusted read port, not a request body. Until authenticated,
    locked policy/final-test/audit loaders are wired, production callers must omit it
    and promotion fails closed. Synthetic fixtures exercise the port explicitly.
    """
    if not actor_id.strip():
        raise ValueError("registry events require an authenticated actor")
    # Audit writes take the same project lock before registry locks. No clean
    # replacement sample can race an adverse audit into a descendant promotion.
    project_id = lock_project(connection, project_key)
    recipe_id = connection.execute(
        sa.select(preprocessing_recipe.c.id).where(preprocessing_recipe.c.recipe_key == recipe_key)
    ).scalar_one()
    scope = (db.registry_head.c.project_id == project_id, db.registry_head.c.recipe_id == recipe_id)
    with connection.begin_nested():
        connection.execute(
            insert(db.registry_head)
            .values(project_id=project_id, recipe_id=recipe_id)
            .on_conflict_do_nothing()
        )
        row = (
            connection.execute(sa.select(db.registry_head).where(*scope).with_for_update())
            .mappings()
            .one()
        )
        if row["revision"] != expected_revision:
            raise ValueError("stale registry revision")
        state = RegistryState(
            project_key,
            recipe_key,
            row["revision"],
            load_release(connection, row["champion_id"]),
            load_release(connection, row["rollback_id"]),
        )
        if candidate_key is None:
            if row["rollback_id"] is not None:
                require_no_annotation_veto(
                    connection, project_id=project_id, model_id=row["rollback_id"]
                )
            changed = state.roll_back(expected_revision=expected_revision)
            champion_id, rollback_id = row["rollback_id"], row["champion_id"]
        else:
            candidate_row = (
                connection.execute(
                    sa.select(db.model_release).where(
                        db.model_release.c.release_key == candidate_key
                    )
                )
                .mappings()
                .one()
            )
            require_no_annotation_veto(
                connection, project_id=project_id, model_id=candidate_row["id"]
            )
            candidate = load_release(connection, candidate_row["id"])
            assert candidate is not None
            if policy is None or evaluation_key is None:
                raise ValueError("promotion requires explicit gates and persisted evaluation")
            incumbent = (
                stored_report(connection, state.champion, incumbent_evaluation_key)
                if state.champion and incumbent_evaluation_key
                else None
            )
            changed = state.promote(
                candidate,
                stored_report(connection, candidate, evaluation_key),
                policy,
                expected_revision=expected_revision,
                incumbent_report=incumbent,
                qualification=qualification,
            )
            champion_id, rollback_id = candidate_row["id"], row["champion_id"]
        connection.execute(
            sa.update(db.registry_head)
            .where(*scope)
            .values(revision=changed.revision, champion_id=champion_id, rollback_id=rollback_id)
        )
        connection.execute(
            sa.insert(db.promotion_event).values(
                project_id=project_id,
                recipe_id=recipe_id,
                revision=changed.revision,
                actor_id=actor_id,
                action="rollback" if candidate_key is None else "promote",
                record={
                    "before": json_record(state),
                    "after": json_record(changed),
                    "policy": asdict(policy) if policy else None,
                    "evaluation_key": evaluation_key,
                    "incumbent_evaluation_key": incumbent_evaluation_key,
                    "qualification": json.loads(qualification_json(qualification))
                    if qualification
                    else None,
                },
            )
        )
    return changed
