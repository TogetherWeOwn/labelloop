"""Canonical, reproducible ordinary-acceptance audit populations.

The authenticated service owns authorization and the approved pipeline/sampling
policy. This port never accepts candidate lists, confidence filters or group IDs
from a request. Sampling is not truth, score calibration or qualification.
"""

import json
from dataclasses import dataclass

import sqlalchemy as sa

from labelloop_api import qualification_schema
from labelloop_api.annotation_audit_store import lock_project, release_membership
from labelloop_api.schema import (
    annotation_revision,
    canonical_dataset_release,
    ontology_release,
    review_event,
    source_asset,
    source_split_assignment,
)
from labelloop_core.audit import AuditCandidate, AuditSample, select_audit_sample
from labelloop_core.domain import AnnotationRevision, DatasetObject
from labelloop_core.geometry import Box
from labelloop_core.qualification_data import record_sha256, require_name
from labelloop_core.qualification_wire import dumps, loads


@dataclass(frozen=True)
class FrozenAuditSample:
    sample: AuditSample
    population_sha256: str
    group_ids: dict[str, str]

    @property
    def independent_groups(self) -> int:
        return len(set(self.group_ids.values()))


def canonical_candidate(
    row, *, pipeline_id: str, prior_reviewers: frozenset[str]
) -> AuditCandidate:
    """Decode the exact frozen revision, not the source's latest revision."""
    objects = tuple(
        DatasetObject(
            object_id=item["object_id"],
            class_id=item["class_id"],
            box=Box(*item["box"]),
            label_state=item.get("label_state", "identified"),
            attributes=item.get("attributes", {}),
            occluded=item.get("occluded", False),
            truncated_at_image_edge=item.get("truncated_at_image_edge", False),
        )
        for item in row["objects"]
    )
    for item in objects:
        item.box.within(row["width"], row["height"])
        if item.class_id not in row["class_map"]:
            raise ValueError("audit object is outside the frozen ontology")
    revision = AnnotationRevision(
        revision=row["revision"],
        objects=objects,
        actor_kind=row["actor_kind"],
        parent_revision=row["parent_revision"],
        approval_state=row["approval_state"],
        review_tier=row["review_tier"],
        scope_completeness=row["scope_completeness"],
        training_eligible=row["training_eligible"],
        gold_eligible=row["gold_eligible"],
    )
    if revision.scope_completeness != "exhaustive_for_scope":
        raise ValueError("audit population requires complete ordinary acceptances")
    return AuditCandidate(
        row["asset_key"], revision, pipeline_id, prior_reviewers | {row["actor_id"]}
    )


def freeze_audit_sample(
    connection: sa.Connection,
    *,
    project_key: str,
    release_key: str,
    sample_key: str,
    actor_id: str,
    pipeline_id: str,
    size: int,
    seed: int,
) -> FrozenAuditSample:
    """Freeze the entire canonical population before any audit result is observed.

    Caller derives pipeline_id, size and seed from approved server policy, never
    model output. Existing blind sampling includes positives and negatives without
    confidence filtering. Correlated assets retain one group identity, not inflated
    independent support. The caller commits before revealing selected audit work.
    """
    for value in (actor_id, sample_key, pipeline_id):
        require_name(value)
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
        if not membership or set(membership) & set(release["excluded_asset_ids"]):
            raise ValueError("audit population must have nonempty, non-excluded frozen membership")
        class_map = connection.execute(
            sa.select(ontology_release.c.class_map).where(
                ontology_release.c.id == release["ontology_id"],
                ontology_release.c.project_id == project_id,
            )
        ).scalar_one()
        rows = (
            connection.execute(
                sa.select(
                    source_asset.c.id.label("asset_uuid"),
                    source_asset.c.asset_key,
                    source_asset.c.width,
                    source_asset.c.height,
                    annotation_revision,
                    source_split_assignment.c.group_id,
                )
                .select_from(
                    source_asset.join(annotation_revision).join(
                        source_split_assignment,
                        source_split_assignment.c.asset_id == source_asset.c.id,
                    )
                )
                .where(
                    source_asset.c.project_id == project_id,
                    source_split_assignment.c.assignment_version == release["assignment_version"],
                    sa.tuple_(source_asset.c.asset_key, annotation_revision.c.revision).in_(
                        list(membership.items())
                    ),
                )
            )
            .mappings()
            .all()
        )
        if len(rows) != len(membership) or {row["asset_key"] for row in rows} != set(membership):
            raise ValueError("audit population has missing canonical revisions or pinned groups")
        prior: dict[object, list[tuple[int, str]]] = {}
        for event in connection.execute(
            sa.select(
                review_event.c.asset_id, review_event.c.expected_revision, review_event.c.actor_id
            ).where(review_event.c.asset_id.in_([row["asset_uuid"] for row in rows]))
        ):
            prior.setdefault(event.asset_id, []).append((event.expected_revision, event.actor_id))
        candidates = []
        groups = {}
        for row in sorted(rows, key=lambda item: item["asset_key"]):
            require_name(row["group_id"])
            groups[row["asset_key"]] = row["group_id"]
            candidates.append(
                canonical_candidate(
                    dict(row) | {"class_map": class_map},
                    pipeline_id=pipeline_id,
                    prior_reviewers=frozenset(
                        actor
                        for revision, actor in prior.get(row["asset_uuid"], ())
                        if revision < row["revision"]
                    ),
                )
            )
        population = AuditSample(sample_key + "-population", tuple(candidates), seed)
        sample = select_audit_sample(tuple(candidates), sample_id=sample_key, size=size, seed=seed)
        snapshot = {"population": json.loads(dumps(population)), "groups": groups}
        digest = record_sha256(snapshot)
        connection.execute(
            sa.insert(qualification_schema.audit_sample).values(
                project_id=project_id,
                release_id=release["id"],
                sample_key=sample_key,
                actor_id=actor_id,
                population_sha256=digest,
                record=snapshot | {"sample": json.loads(dumps(sample))},
            )
        )
        return FrozenAuditSample(
            sample, digest, {item.asset_id: groups[item.asset_id] for item in sample.candidates}
        )


def load_audit_sample(
    connection: sa.Connection, *, project_id: object, sample_key: str
) -> FrozenAuditSample:
    row = (
        connection.execute(
            sa.select(qualification_schema.audit_sample).where(
                qualification_schema.audit_sample.c.project_id == project_id,
                qualification_schema.audit_sample.c.sample_key == sample_key,
            )
        )
        .mappings()
        .one()
    )
    record = row["record"]
    population = loads(json.dumps(record["population"]), AuditSample)
    sample = loads(json.dumps(record["sample"]), AuditSample)
    groups = record["groups"]
    if (
        record_sha256({"population": record["population"], "groups": groups})
        != row["population_sha256"]
        or set(groups) != {item.asset_id for item in population.candidates}
        or sample.sample_id != sample_key
        or sample
        != select_audit_sample(
            population.candidates,
            sample_id=sample_key,
            size=len(sample.candidates),
            seed=population.seed,
        )
    ):
        raise ValueError("durable audit sample does not match its frozen population")
    for value in groups.values():
        require_name(value)
    return FrozenAuditSample(
        sample,
        row["population_sha256"],
        {item.asset_id: groups[item.asset_id] for item in sample.candidates},
    )
