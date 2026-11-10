"""Trusted internal audit writes and durable training-lineage vetoes.

The authenticated service must authorize a human auditor and derive actor_id from
its session. This is not an HTTP body codec or a representative sampling producer.
A clean later audit never clears earlier adverse evidence. Until authenticated
correction/re-tracing exists, all later revisions of a vetoed asset remain held.
"""

from dataclasses import asdict, dataclass, fields

import sqlalchemy as sa

from labelloop_api import model_schema, qualification_schema
from labelloop_api.audit_sample_store import FrozenAuditSample, load_audit_sample
from labelloop_api.generated_label_store import label_teacher_runs
from labelloop_api.qualification_store import lock_project as lock_project
from labelloop_api.qualification_store import release_membership as release_membership
from labelloop_api.schema import (
    annotation_revision,
    canonical_dataset_release,
    review_event,
    source_asset,
    yolo_dataset_variant,
)
from labelloop_core.audit import AuditObservation, AuditVerdict, assess_audit
from labelloop_core.qualification_data import canonical_record, require_name


@dataclass(frozen=True)
class StoredAnnotationAudit:
    frozen: FrozenAuditSample
    observations: tuple[AuditObservation, ...]
    verdict: AuditVerdict


def load_annotation_audit(
    connection: sa.Connection,
    *,
    project_key: str,
    release_key: str,
    audit_key: str,
    require_current_groups: bool = True,
) -> StoredAnnotationAudit:
    """Reload one immutable result, never request-supplied sample or verdict fields.

    Qualification consumers must use the default current-group check in the same
    short transaction as their decision. Historical inspection may disable it to
    retain adverse evidence after a grouping conflict. Partial results stay partial;
    no merging, latest-clean selection, independent truth or quality is inferred.
    Unsampled observations remain veto-only and cannot supply this bound port.
    """
    require_name(audit_key)
    project_id = lock_project(connection, project_key)
    release_id = connection.execute(
        sa.select(canonical_dataset_release.c.id).where(
            canonical_dataset_release.c.project_id == project_id,
            canonical_dataset_release.c.release_key == release_key,
            canonical_dataset_release.c.frozen,
        )
    ).scalar_one()
    row = (
        connection.execute(
            sa.select(qualification_schema.annotation_audit).where(
                qualification_schema.annotation_audit.c.project_id == project_id,
                qualification_schema.annotation_audit.c.release_id == release_id,
                qualification_schema.annotation_audit.c.audit_key == audit_key,
            )
        )
        .mappings()
        .one()
    )
    record = row["record"]
    if not isinstance(record, dict) or set(record) != {"observations", "sample"}:
        raise ValueError("qualification requires a sample-bound audit result")
    binding = record["sample"]
    if not isinstance(binding, dict) or set(binding) != {
        "sample_key",
        "population_sha256",
        "group_ids",
        "verdict",
    }:
        raise ValueError("invalid persisted audit sample binding")
    require_name(binding["sample_key"])
    frozen = load_audit_sample(
        connection,
        project_id=project_id,
        release_id=release_id,
        sample_key=binding["sample_key"],
        require_current_groups=require_current_groups,
    )
    raw = record["observations"]
    names = {field.name for field in fields(AuditObservation)}
    if (
        not isinstance(raw, list)
        or not raw
        or any(not isinstance(item, dict) or set(item) != names for item in raw)
    ):
        raise ValueError("invalid persisted audit observations")
    require_name(row["actor_id"])
    observations = tuple(AuditObservation(**item) for item in raw)
    for item in observations:
        require_name(item.asset_id)
        require_name(item.reviewer_id)
        if item.reviewer_id != row["actor_id"]:
            raise ValueError("persisted audit reviewer differs from authenticated principal")
    verdict = assess_audit(frozen.sample, observations)
    expected = {
        "sample_key": frozen.sample.sample_id,
        "population_sha256": frozen.population_sha256,
        "group_ids": frozen.group_ids,
        "verdict": asdict(verdict),
    }
    vetoes = {
        item.asset_id: item.revision
        for item in observations
        if item.asset_id in verdict.blocking_asset_ids
    }
    if (
        canonical_record(binding) != canonical_record(expected)
        or release_membership(row["vetoed_revisions"]) != vetoes
    ):
        raise ValueError("persisted audit result does not match its frozen sample and observations")
    return StoredAnnotationAudit(frozen, observations, verdict)


def record_annotation_audit(
    connection: sa.Connection,
    *,
    project_key: str,
    release_key: str,
    audit_key: str,
    actor_id: str,
    observations: tuple[AuditObservation, ...],
    sample_key: str | None = None,
) -> None:
    """Append actual observations, loading membership and prior actors from canonical rows.

    Sample-bound writes additionally check the committed selection and save its
    fingerprint and membership verdict. Partial/adverse results are retained, never
    counted as complete clean evidence. Unsampled writes remain veto-only evidence;
    neither path qualifies a release, proves representative truth or infers scores.
    Callers commit this evidence before attempting promotion in a separate transaction.
    """
    if sample_key is not None:
        require_name(sample_key)
    if not actor_id.strip() or not audit_key.strip() or not observations:
        raise ValueError("audit requires an authenticated actor, identity and observations")
    if len({item.asset_id for item in observations}) != len(observations):
        raise ValueError("audit observations must name unique assets")
    with connection.begin_nested():
        project_id = lock_project(connection, project_key)
        release = (
            connection.execute(
                sa.select(canonical_dataset_release).where(
                    canonical_dataset_release.c.project_id == project_id,
                    canonical_dataset_release.c.release_key == release_key,
                    canonical_dataset_release.c.frozen,
                )
            )
            .mappings()
            .one()
        )
        membership = release_membership(release["sample_revisions"])
        record = {"observations": [asdict(item) for item in observations]}
        if sample_key is not None:
            frozen = load_audit_sample(
                connection, project_id=project_id, sample_key=sample_key, release_id=release["id"]
            )
            verdict = assess_audit(frozen.sample, observations)
            record["sample"] = {
                "sample_key": sample_key,
                "population_sha256": frozen.population_sha256,
                "group_ids": frozen.group_ids,
                "verdict": asdict(verdict),
            }
        vetoes: dict[str, int] = {}
        for observation in observations:
            if membership.get(observation.asset_id) != observation.revision:
                raise ValueError("audit observation is outside frozen annotation membership")
            if observation.reviewer_id != actor_id:
                raise ValueError("audit reviewer must be the authenticated principal")
            prior = (
                connection.execute(
                    sa.select(annotation_revision.c.actor_id, annotation_revision.c.approval_state)
                    .join(source_asset)
                    .where(
                        source_asset.c.project_id == project_id,
                        source_asset.c.asset_key == observation.asset_id,
                        annotation_revision.c.revision == observation.revision,
                    )
                )
                .mappings()
                .one()
            )
            if prior["approval_state"] not in {"human_accepted", "auto_accepted"}:
                raise ValueError("audit must inspect ordinary accepted labels")
            prior_actors = set(
                connection.execute(
                    sa.select(review_event.c.actor_id)
                    .join(source_asset)
                    .where(
                        source_asset.c.project_id == project_id,
                        source_asset.c.asset_key == observation.asset_id,
                        review_event.c.expected_revision < observation.revision,
                    )
                ).scalars()
            ) | {prior["actor_id"]}
            if actor_id.strip().casefold() in {value.strip().casefold() for value in prior_actors}:
                raise ValueError("audit actor must be independent of the original decision")
            if (
                not observation.blind_full_image_inspected
                or not observation.labels_correct
                or not observation.scope_complete
                or observation.output_truncated
            ):
                vetoes[observation.asset_id] = observation.revision
        connection.execute(
            sa.insert(qualification_schema.annotation_audit).values(
                project_id=project_id,
                release_id=release["id"],
                audit_key=audit_key,
                actor_id=actor_id,
                record=record,
                vetoed_revisions=vetoes,
            )
        )


def training_ancestor_runs(
    runs: dict[object, tuple[dict, dict[str, int]]],
    run_id: object,
    teachers: dict[str, list[tuple[int, object]]] | None = None,
) -> set[object]:
    """Trace checkpoint parents and recorded model-generated label influence.

    Equal checkpoint digests conservatively include every matching run. Visited run
    IDs bound duplicate paths and cycles; a clean child never clears an ancestor.
    Teacher influence survives later revisions until authenticated correction and
    re-tracing exist. Future revisions do not influence earlier frozen membership.
    Unmatched initialization digests are external base checkpoints, not local runs;
    an explicitly recorded teacher outside this project fails closed instead.
    """
    if run_id not in runs:
        raise ValueError("model training lineage is outside its project")
    by_checkpoint: dict[str, set[object]] = {}
    for key, (record, _) in runs.items():
        for checkpoint in record["checkpoints"]:
            by_checkpoint.setdefault(checkpoint["sha256"], set()).add(key)
    pending = [run_id]
    visited: set[object] = set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        if key not in runs:
            raise ValueError("model training lineage is outside its project")
        visited.add(key)
        record, membership = runs[key]
        for asset, revision in membership.items():
            pending.extend(
                teacher
                for origin_revision, teacher in (teachers or {}).get(asset, ())
                if origin_revision <= revision
            )
        pending.extend(by_checkpoint.get(record["request"]["initialization_sha256"], ()))
    return visited


def training_ancestor_assets(
    runs: dict[object, tuple[dict, dict[str, int]]],
    run_id: object,
    teachers: dict[str, list[tuple[int, object]]] | None = None,
) -> set[str]:
    """Keep the veto's conservative full-release membership over the shared graph."""
    return {
        asset
        for ancestor in training_ancestor_runs(runs, run_id, teachers)
        for asset in runs[ancestor][1]
    }


def require_no_annotation_veto(
    connection: sa.Connection, *, project_id: object, model_id: object
) -> None:
    """Caller holds the project lock; inspect training and checkpoint ancestors."""
    run_id = connection.execute(
        sa.select(model_schema.model_release.c.training_run_id).where(
            model_schema.model_release.c.id == model_id,
            model_schema.model_release.c.project_id == project_id,
        )
    ).scalar_one()
    rows = connection.execute(
        sa.select(
            model_schema.training_run.c.id,
            model_schema.training_run.c.record,
            canonical_dataset_release.c.sample_revisions,
        )
        .select_from(
            model_schema.training_run.join(yolo_dataset_variant).join(canonical_dataset_release)
        )
        .where(canonical_dataset_release.c.project_id == project_id)
    ).mappings()
    assets = training_ancestor_assets(
        {row["id"]: (row["record"], release_membership(row["sample_revisions"])) for row in rows},
        run_id,
        label_teacher_runs(connection, project_id),
    )
    vetoes = connection.execute(
        sa.select(qualification_schema.annotation_audit.c.vetoed_revisions).where(
            qualification_schema.annotation_audit.c.project_id == project_id
        )
    ).scalars()
    if any(set(release_membership(item)) & assets for item in vetoes):
        raise ValueError(
            "annotation audit veto: affected training lineage requires correction/re-tracing"
        )
