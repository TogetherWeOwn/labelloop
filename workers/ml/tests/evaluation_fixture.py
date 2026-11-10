"""Invented canonical evaluation populations, never compiled images or model gold."""

import hashlib
from dataclasses import replace

import sqlalchemy as sa

from labelloop_api.schema import (
    annotation_revision,
    canonical_dataset_release,
    source_asset,
    source_split_assignment,
    yolo_dataset_variant,
)


def compiled_training_lineage(connection, release, lineage):
    """Retain synthetic audit lineage plus every actual stored compiled ancestor."""
    from labelloop_api import model_schema
    from labelloop_api.annotation_audit_store import lock_project
    from labelloop_api.training_group_store import model_training_groups

    project_id = lock_project(connection, release.project_id)
    model_id = connection.execute(
        sa.select(model_schema.model_release.c.id).where(
            model_schema.model_release.c.project_id == project_id,
            model_schema.model_release.c.release_key == release.release_id,
        )
    ).scalar_one()
    groups = model_training_groups(connection, project_id=project_id, model_id=model_id)
    return replace(
        lineage, training_group_ids=tuple(sorted(set(lineage.training_group_ids) | set(groups)))
    )


def independent_evaluation_variant(connection, source_variant_id, dataset_key):
    """Reuse recipe/class metadata, not training membership or compiled sample claims."""
    source = (
        connection.execute(
            sa.select(yolo_dataset_variant).where(yolo_dataset_variant.c.id == source_variant_id)
        )
        .mappings()
        .one()
    )
    canonical = (
        connection.execute(
            sa.select(canonical_dataset_release).where(
                canonical_dataset_release.c.id == source["release_id"]
            )
        )
        .mappings()
        .one()
    )
    membership = {}
    digest = hashlib.sha256(dataset_key.encode()).hexdigest()
    for positive in (True, False):
        key = dataset_key + ("-positive" if positive else "-negative")
        asset_id = connection.execute(
            sa.insert(source_asset)
            .values(
                project_id=canonical["project_id"],
                asset_key=key,
                sha256=digest,
                object_key=f"sha256/{digest[:2]}/{digest}",
                width=32,
                height=32,
                exif_orientation=1,
                source_uri="fixture://synthetic-evaluation",
            )
            .returning(source_asset.c.id)
        ).scalar_one()
        connection.execute(
            sa.insert(annotation_revision).values(
                asset_id=asset_id,
                revision=1,
                actor_kind="system",
                actor_id="synthetic-evaluation-fixture",
                objects=[
                    {"object_id": value, "class_id": value, "box": [0, 0, 8, 8]}
                    for value in source["class_map"]
                ]
                if positive
                else [],
                approval_state="auto_accepted",
                review_tier="machine_verified_no_human_review",
                scope_completeness="exhaustive_for_scope",
                training_eligible=False,
                gold_eligible=False,
            )
        )
        connection.execute(
            sa.insert(source_split_assignment).values(
                assignment_version=canonical["assignment_version"],
                asset_id=asset_id,
                group_id=dataset_key + "-group",
                split="test",
                seed=7,
                algorithm="explicit-synthetic",
                thresholds={},
                reason="fixture-only independent evaluation population",
                locked=True,
            )
        )
        membership[key] = 1
    values = {key: value for key, value in canonical.items() if key not in {"id", "created_at"}}
    values.update(
        release_key=dataset_key, sample_revisions=membership, excluded_asset_ids=[], hashes={}
    )
    release_id = connection.execute(
        sa.insert(canonical_dataset_release).values(**values)
    ).inserted_primary_key[0]
    # Copied scores only exercise persistence. No image compilation or independent
    # measurement occurred for these sources; do not retain the CPU sample manifest.
    manifest = {
        "release_id": dataset_key,
        "recipe": source["file_manifest"]["recipe"],
        "file_hashes": {"class-map.json": source["file_manifest"]["file_hashes"]["class-map.json"]},
        "samples": [],
        "fixture_only": True,
    }
    return connection.execute(
        sa.insert(yolo_dataset_variant).values(
            release_id=release_id,
            recipe_id=source["recipe_id"],
            class_map=source["class_map"],
            file_manifest=manifest,
            validated=True,
            non_empty_val=True,
        )
    ).inserted_primary_key[0]
