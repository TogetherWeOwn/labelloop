"""Synthetic persisted reference influence; no additional inference/training claim."""

import copy
from dataclasses import replace

import pytest
import sqlalchemy as sa

from labelloop_api import model_schema
from labelloop_api.acceptance_store import require_automatic_acceptance
from labelloop_api.generated_label_store import record_generated_label_origin
from labelloop_api.model_store import change_registry, json_record, load_registry
from labelloop_api.qualification_context_store import load_qualification_context
from labelloop_api.qualification_store import lock_project
from labelloop_api.revision_store import append_review
from labelloop_api.schema import (
    annotation_revision,
    canonical_dataset_release,
    review_event,
    source_asset,
    yolo_dataset_variant,
)
from labelloop_api.training_group_store import model_input_groups
from labelloop_core.registry import PromotionPolicy


def values(row):
    return {key: value for key, value in row.items() if key not in {"id", "created_at"}}


def exercise_recorded_references(
    connection, release, final, loaded, arguments, actor, before, count
):
    from qualification_context_smoke import _proposal_origin_fixture

    scope = dict(project_key=release.project_id, recipe_key=release.recipe_id)
    project_id = lock_project(connection, release.project_id)
    model = (
        connection.execute(
            sa.select(model_schema.model_release).where(
                model_schema.model_release.c.project_id == project_id,
                model_schema.model_release.c.release_key == release.release_id,
            )
        )
        .mappings()
        .one()
    )
    run = (
        connection.execute(
            sa.select(model_schema.training_run).where(
                model_schema.training_run.c.id == model["training_run_id"]
            )
        )
        .mappings()
        .one()
    )
    variant = (
        connection.execute(
            sa.select(yolo_dataset_variant).where(yolo_dataset_variant.c.id == run["variant_id"])
        )
        .mappings()
        .one()
    )
    canonical = (
        connection.execute(
            sa.select(canonical_dataset_release).where(
                canonical_dataset_release.c.id == variant["release_id"]
            )
        )
        .mappings()
        .one()
    )
    source = next(
        sample["asset_id"]
        for sample in run["input_manifest"]["samples"]
        if sample["split"] == "train"
    )
    asset_id, objects = connection.execute(
        sa.select(source_asset.c.id, annotation_revision.c.objects)
        .join(annotation_revision)
        .where(source_asset.c.asset_key == source, annotation_revision.c.revision == 1)
    ).one()
    reference_asset = loaded.evidence.canary_audit.sources[0].asset_id
    reference_group = loaded.evidence.canary_audit.sources[0].group_id
    with connection.begin_nested() as transaction:
        # This completed producer is synthetic; the actual annotation write must
        # atomically journal its run identity even though the proposed label is rejected.
        proposal = _proposal_origin_fixture(
            connection,
            release,
            source,
            1,
            "-references",
            references=(
                dict(
                    reference_id="synthetic-ref",
                    asset_id=reference_asset,
                    class_id=release.class_ids[0],
                    approved=True,
                ),
            ),
        )
        assert (
            append_review(
                connection,
                asset_id=source,
                actor_kind="human",
                actor_id=actor,
                expected_revision=1,
                approval_state="proposed",
                review_tier="unreviewed",
                scope_completeness="unreviewed",
                objects=objects,
                proposal_run_key=proposal,
            )
            == 2
        )
        correction = connection.execute(
            sa.select(review_event.c.correction).where(
                review_event.c.asset_id == asset_id,
                review_event.c.expected_revision == 1,
                review_event.c.decision == "apply_revision",
            )
        ).scalar_one()
        assert correction == {"new_revision": 2, "proposal_run_keys": [proposal]}
        for statement in (
            sa.update(review_event)
            .where(review_event.c.asset_id == asset_id)
            .values(correction={}),
            sa.delete(review_event).where(review_event.c.asset_id == asset_id),
            sa.text("TRUNCATE review_event"),
        ):
            with pytest.raises(sa.exc.DBAPIError, match="review events are immutable"):
                with connection.begin_nested():
                    connection.execute(statement)
        # The existing compiled revision predates the proposal and must not acquire it.
        assert (
            model_input_groups(connection, project_id=project_id, model_id=model["id"]).references
            == ()
        )

        # Persist a synthetic ancestor consuming revision 2. Copied metrics/weights
        # exercise lineage only, never a claim of retraining on these new annotations.
        prefix = release.release_id + "-reference-ancestor"
        membership = dict(canonical["sample_revisions"]) | {source: 2}
        manifest = copy.deepcopy(run["input_manifest"])
        manifest.update(release_id=prefix, fixture_only=True)
        for sample in manifest["samples"]:
            if sample["asset_id"] == source:
                sample["revision"] = 2
        canonical_id = connection.execute(
            sa.insert(canonical_dataset_release).values(
                **(values(canonical) | dict(release_key=prefix, sample_revisions=membership))
            )
        ).inserted_primary_key[0]
        variant_id = connection.execute(
            sa.insert(yolo_dataset_variant).values(
                **(values(variant) | dict(release_id=canonical_id, file_manifest=manifest))
            )
        ).inserted_primary_key[0]
        record = copy.deepcopy(run["record"])
        record["request"].update(run_id=prefix, dataset_release_id=prefix)
        run_id = connection.execute(
            sa.insert(model_schema.training_run).values(
                **(
                    values(run)
                    | dict(
                        run_key=prefix,
                        variant_id=variant_id,
                        input_manifest=manifest,
                        record=record,
                    )
                )
            )
        ).inserted_primary_key[0]
        teacher = replace(release, release_id=prefix)
        connection.execute(
            sa.insert(model_schema.model_release).values(
                **(
                    values(model)
                    | dict(training_run_id=run_id, release_key=prefix, record=json_record(teacher))
                )
            )
        )
        # Existing exact-revision teacher graph brings the ancestor into the closure;
        # a cycle deliberately proves references are retained without recursion loops.
        record_generated_label_origin(
            connection,
            project_key=release.project_id,
            asset_key=source,
            revision=1,
            teacher_model_key=prefix,
            actor_id=actor,
        )
        assert model_input_groups(
            connection, project_id=project_id, model_id=model["id"]
        ).references == (reference_group,)
        for context, message in (
            (loaded, "omits recorded proposal reference groups"),
            (
                replace(
                    loaded,
                    evidence=replace(
                        loaded.evidence,
                        lineage=replace(
                            loaded.evidence.lineage, reference_group_ids=(reference_group,)
                        ),
                    ),
                ),
                "canary groups overlap recorded proposal references",
            ),
        ):
            with pytest.raises(ValueError, match=message):
                load_qualification_context(
                    connection, **(arguments | {"lineage": context.evidence.lineage})
                )
            with pytest.raises(ValueError, match=message):
                require_automatic_acceptance(
                    connection,
                    **scope,
                    evaluation_key=final.run_id,
                    qualification=context,
                    scope=loaded.evidence.scope[0],
                )
            with pytest.raises(ValueError, match=message):
                change_registry(
                    connection,
                    **scope,
                    actor_id=actor,
                    expected_revision=before.revision,
                    candidate_key=release.release_id,
                    evaluation_key=final.run_id,
                    policy=PromotionPolicy(1, 0.0),
                    qualification=context,
                )
            assert load_registry(connection, **scope) == before
            assert (
                connection.execute(
                    sa.select(sa.func.count()).select_from(model_schema.promotion_event)
                ).scalar_one()
                == count
            )
        transaction.rollback()
    assert (
        model_input_groups(connection, project_id=project_id, model_id=model["id"]).references == ()
    )
