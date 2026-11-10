"""Rehydrate compiler inputs from restored canonical records, never fixture source paths."""

import sqlalchemy as sa

from labelloop_api import schema as db
from labelloop_core.domain import (
    AnnotationRevision,
    CanonicalDatasetRelease,
    ClassDefinition,
    DatasetObject,
    OntologyRelease,
    ReleasedSample,
    SourceAsset,
    SourceSplitAssignment,
)
from labelloop_core.geometry import Box


def load_canonical_release(connection: sa.Connection, release_key: str) -> CanonicalDatasetRelease:
    release = (
        connection.execute(
            sa.select(db.canonical_dataset_release).where(
                db.canonical_dataset_release.c.release_key == release_key
            )
        )
        .mappings()
        .one()
    )
    project_key = connection.execute(
        sa.select(db.project.c.project_key).where(db.project.c.id == release["project_id"])
    ).scalar_one()
    ontology = (
        connection.execute(
            sa.select(db.ontology_release).where(db.ontology_release.c.id == release["ontology_id"])
        )
        .mappings()
        .one()
    )
    if ontology["project_id"] != release["project_id"]:
        raise ValueError("restored ontology belongs to another project")
    splits = (
        connection.execute(
            sa.select(db.source_split_assignment, db.source_asset.c.asset_key)
            .join(db.source_asset)
            .where(
                db.source_split_assignment.c.assignment_version == release["assignment_version"],
                db.source_asset.c.project_id == release["project_id"],
            )
        )
        .mappings()
        .all()
    )
    if not splits:
        raise ValueError("release has no restored source split assignment")
    if len({(row["seed"], row["algorithm"], row["locked"]) for row in splits}) != 1:
        raise ValueError("inconsistent restored split policy")
    # Split reasons and splits are group-level identity: every asset in a group
    # must agree, and the rehydrated assignment must key reasons by group.
    group_facts: dict[str, tuple[str, str]] = {}
    for row in splits:
        fact = (row["split"], row["reason"])
        if group_facts.setdefault(row["group_id"], fact) != fact:
            raise ValueError("inconsistent restored split policy within a group")
    assignment = SourceSplitAssignment(
        assignment_version=release["assignment_version"],
        asset_splits={r["asset_key"]: r["split"] for r in splits},
        group_splits={group: fact[0] for group, fact in group_facts.items()},
        group_ids={r["asset_key"]: r["group_id"] for r in splits},
        seed=splits[0]["seed"],
        algorithm=splits[0]["algorithm"],
        reasons={group: fact[1] for group, fact in group_facts.items()},
        locked=splits[0]["locked"],
    )
    samples = []
    for asset_key, revision_number in sorted(release["sample_revisions"].items()):
        asset = (
            connection.execute(
                sa.select(db.source_asset).where(db.source_asset.c.asset_key == asset_key)
            )
            .mappings()
            .one()
        )
        if asset["project_id"] != release["project_id"]:
            raise ValueError("restored source belongs to another project")
        revision = (
            connection.execute(
                sa.select(db.annotation_revision).where(
                    db.annotation_revision.c.asset_id == asset["id"],
                    db.annotation_revision.c.revision == revision_number,
                )
            )
            .mappings()
            .one()
        )
        objects = []
        for item in revision["objects"]:
            box = Box(**item["box"]) if isinstance(item["box"], dict) else Box(*item["box"])
            objects.append(DatasetObject(**(item | {"box": box})))
        samples.append(
            ReleasedSample(
                SourceAsset(
                    asset_id=asset_key,
                    project_id=project_key,
                    sha256=asset["sha256"],
                    object_key=asset["object_key"],
                    width=asset["width"],
                    height=asset["height"],
                    exif_orientation=asset["exif_orientation"],
                    source_uri=asset["source_uri"],
                ),
                AnnotationRevision(
                    objects=tuple(objects),
                    **{
                        key: revision[key]
                        for key in (
                            "revision",
                            "parent_revision",
                            "actor_kind",
                            "approval_state",
                            "review_tier",
                            "scope_completeness",
                            "training_eligible",
                            "gold_eligible",
                        )
                    },
                ),
                assignment.group_ids[asset_key],
                assignment.asset_splits[asset_key],
            )
        )
    result = CanonicalDatasetRelease(
        release_id=release_key,
        project_id=project_key,
        ontology=OntologyRelease(
            ontology_id=ontology["ontology_key"],
            project_id=project_key,
            version=ontology["version"],
            classes=tuple(ClassDefinition(**item) for item in ontology["classes"]),
            class_map=ontology["class_map"],
        ),
        assignment=assignment,
        samples=tuple(samples),
        excluded_asset_ids=release["excluded_asset_ids"],
        frozen=release["frozen"],
    )
    if result.ontology_hash() != release["ontology_hash"]:
        raise ValueError("restored ontology digest mismatch")
    return result
