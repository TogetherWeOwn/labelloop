"""Trusted internal audit writes and durable training-lineage vetoes.

The authenticated service must authorize a human auditor and derive actor_id from
its session. This is not an HTTP body codec or a representative sampling producer.
A clean later audit never clears earlier adverse evidence. Until authenticated
correction/re-tracing exists, all later revisions of a vetoed asset remain held.
"""

from dataclasses import asdict

import sqlalchemy as sa

from labelloop_api import model_schema, qualification_schema
from labelloop_api.schema import (
    annotation_revision,
    canonical_dataset_release,
    project,
    review_event,
    source_asset,
    yolo_dataset_variant,
)
from labelloop_core.audit import AuditObservation


def lock_project(connection: sa.Connection, project_key: str) -> object:
    """Shared lock order for audit evidence and every recipe's registry mutation."""
    return connection.execute(
        sa.select(project.c.id).where(project.c.project_key == project_key).with_for_update()
    ).scalar_one()


def release_membership(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not key.strip() or type(revision) is not int or revision < 1
        for key, revision in value.items()
    ):
        raise ValueError("frozen annotation membership must map asset keys to revisions")
    return value


def record_annotation_audit(
    connection: sa.Connection,
    *,
    project_key: str,
    release_key: str,
    audit_key: str,
    actor_id: str,
    observations: tuple[AuditObservation, ...],
) -> None:
    """Append actual observations, loading membership and prior actors from canonical rows.

    Observations are independent human audit results, never model confidence prose.
    The eventual sampling producer owns frozen-population coverage and statistics;
    this conservative veto port neither qualifies a clean sample nor infers scores.
    Callers commit this evidence before attempting promotion in a separate transaction.
    """
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
                record={"observations": [asdict(item) for item in observations]},
                vetoed_revisions=vetoes,
            )
        )


def require_no_annotation_veto(
    connection: sa.Connection, *, project_id: object, model_id: object
) -> None:
    """Caller holds the project lock; inspect actual training, not final-test membership."""
    membership = release_membership(
        connection.execute(
            sa.select(canonical_dataset_release.c.sample_revisions)
            .select_from(
                model_schema.model_release.join(model_schema.training_run)
                .join(yolo_dataset_variant)
                .join(canonical_dataset_release)
            )
            .where(
                model_schema.model_release.c.id == model_id,
                model_schema.model_release.c.project_id == project_id,
                canonical_dataset_release.c.project_id == project_id,
            )
        ).scalar_one()
    )
    vetoes = connection.execute(
        sa.select(qualification_schema.annotation_audit.c.vetoed_revisions).where(
            qualification_schema.annotation_audit.c.project_id == project_id
        )
    ).scalars()
    if any(set(release_membership(item)) & set(membership) for item in vetoes):
        raise ValueError(
            "annotation audit veto: affected training lineage requires correction/re-tracing"
        )
