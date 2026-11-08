"""Persisted assembly on explicitly invented canonical groups/counts, not domain gold."""

import hashlib
from dataclasses import replace

import pytest
import sqlalchemy as sa

from labelloop_api import model_schema
from labelloop_api.acceptance_store import require_automatic_acceptance
from labelloop_api.annotation_audit_store import record_annotation_audit
from labelloop_api.annotation_evidence_store import record_annotation_evidence
from labelloop_api.audit_sample_store import freeze_audit_sample
from labelloop_api.final_test_store import register_final_test
from labelloop_api.model_store import change_registry, load_registry
from labelloop_api.qualification_context_store import (
    AnnotationOutcomeRef,
    load_qualification_context,
)
from labelloop_api.qualification_store import lock_project
from labelloop_api.schema import (
    annotation_revision,
    canonical_dataset_release,
    source_asset,
    source_split_assignment,
)
from labelloop_core.audit import AuditObservation
from labelloop_core.qualification import AnnotationEvidence, AnnotationMeasurement
from labelloop_core.qualification_data import SourceGroup
from labelloop_core.registry import PromotionPolicy


def _outcome(connection, release, context, actor, *, canary):
    """Seed separate synthetic canonical sources; never relabel actual CPU fixtures."""
    prefix = release.release_id + ("-assembly-canary" if canary else "-assembly-audit")
    project_id = lock_project(connection, release.project_id)
    original = (
        connection.execute(
            sa.select(canonical_dataset_release)
            .where(canonical_dataset_release.c.project_id == project_id)
            .limit(1)
        )
        .mappings()
        .one()
    )
    membership = {}
    group = prefix + "-group"
    for positive in (True, False):
        key = prefix + ("-positive" if positive else "-negative")
        digest = hashlib.sha256(key.encode()).hexdigest()
        asset_id = connection.execute(
            sa.insert(source_asset)
            .values(
                project_id=project_id,
                asset_key=key,
                sha256=digest,
                object_key=f"sha256/{digest[:2]}/{digest}",
                width=32,
                height=32,
                exif_orientation=1,
                source_uri="fixture://context-assembly",
            )
            .returning(source_asset.c.id)
        ).scalar_one()
        connection.execute(
            sa.insert(annotation_revision).values(
                asset_id=asset_id,
                revision=1,
                actor_kind="system",
                actor_id="synthetic-teacher",
                objects=[
                    {"object_id": value, "class_id": value, "box": [0, 0, 8, 8]}
                    for value in release.class_ids
                ]
                if positive
                else [],
                approval_state="auto_accepted",
                review_tier="machine_verified_no_human_review",
                scope_completeness="exhaustive_for_scope",
                training_eligible=True,
                gold_eligible=False,
            )
        )
        connection.execute(
            sa.insert(source_split_assignment).values(
                assignment_version=original["assignment_version"],
                asset_id=asset_id,
                group_id=group,
                split="train",
                seed=7,
                algorithm="explicit-synthetic",
                thresholds={},
                reason="fixture-only context assembly",
                locked=True,
            )
        )
        membership[key] = 1
    connection.execute(
        sa.insert(canonical_dataset_release).values(
            **{
                key: value
                for key, value in original.items()
                if key
                not in {"id", "created_at", "release_key", "sample_revisions", "excluded_asset_ids"}
            },
            release_key=prefix,
            sample_revisions=membership,
            excluded_asset_ids=[],
        )
    )
    frozen = freeze_audit_sample(
        connection,
        project_key=release.project_id,
        release_key=prefix,
        sample_key=prefix,
        actor_id=actor,
        pipeline_id=context.current_pipeline.pipeline_id,
        size=2,
        seed=7,
    )
    observations = tuple(
        AuditObservation(item.asset_id, 1, actor, True, True, True)
        for item in frozen.sample.candidates
    )
    record_annotation_audit(
        connection,
        project_key=release.project_id,
        release_key=prefix,
        audit_key=prefix,
        actor_id=actor,
        sample_key=prefix,
        observations=observations,
    )
    cells = context.evidence.scope
    evidence = AnnotationEvidence(
        frozen.sample,
        observations,
        tuple(
            SourceGroup(
                key, group, cells[0].source_id, tuple(sorted({cell.condition for cell in cells}))
            )
            for key in membership
        ),
        "constructive_synthetic",
        tuple(AnnotationMeasurement(cell, (group,), 1, 1, 1, 1, 0.0) for cell in cells),
    )
    record_annotation_evidence(
        connection,
        project_key=release.project_id,
        release_key=prefix,
        audit_key=prefix,
        evidence_key=prefix,
        actor_id=actor,
        evidence=evidence,
        canary_model_key=release.release_id if canary else None,
    )
    if canary:
        ordinary = prefix + "-ordinary"
        record_annotation_audit(
            connection,
            project_key=release.project_id,
            release_key=prefix,
            audit_key=ordinary,
            actor_id=actor,
            sample_key=prefix,
            observations=observations,
        )
        record_annotation_evidence(
            connection,
            project_key=release.project_id,
            release_key=prefix,
            audit_key=ordinary,
            evidence_key=ordinary,
            actor_id=actor,
            evidence=evidence,
        )
        partial = prefix + "-partial"
        record_annotation_audit(
            connection,
            project_key=release.project_id,
            release_key=prefix,
            audit_key=partial,
            actor_id=actor,
            sample_key=prefix,
            observations=observations[:1],
        )
        record_annotation_evidence(
            connection,
            project_key=release.project_id,
            release_key=prefix,
            audit_key=partial,
            evidence_key=partial,
            actor_id=actor,
            evidence=replace(evidence, observations=observations[:1]),
            canary_model_key=release.release_id,
        )
    return AnnotationOutcomeRef(prefix, prefix, prefix)


def exercise_context_assembly(engine, release, final, context, actor):
    scope = dict(project_key=release.project_id, recipe_key=release.recipe_id)
    with engine.begin() as connection:
        annotation = _outcome(connection, release, context, actor, canary=False)
        canary = _outcome(connection, release, context, actor, canary=True)
        before = load_registry(connection, **scope)
        count = connection.execute(
            sa.select(sa.func.count()).select_from(model_schema.promotion_event)
        ).scalar_one()
    arguments = dict(
        **scope,
        model_key=release.release_id,
        evaluation_key=final.run_id,
        qualification_key=release.release_id + "-assembled",
        annotation=annotation,
        canary=canary,
        lineage=context.evidence.lineage,
    )
    # Reconnect: model/report, configuration, journal and both measured outcomes
    # must come from PostgreSQL, then pass the existing pure gates without a bypass.
    with engine.begin() as connection:
        loaded = load_qualification_context(connection, **arguments)
        assert loaded.evidence.annotation_audit != context.evidence.annotation_audit
        assert loaded.evidence.canary_audit != context.evidence.canary_audit
        require_automatic_acceptance(
            connection,
            **scope,
            evaluation_key=final.run_id,
            qualification=loaded,
            scope=context.evidence.scope[0],
        )
        with connection.begin_nested() as transaction:
            # The context was assembled before this new reservation. Neither a new
            # assembly nor replay of that older success may hide the locked identity.
            register_final_test(
                connection,
                dataset=replace(loaded.evidence.lineage.development, role="locked_final_test"),
                actor_id=actor,
            )
            with pytest.raises(ValueError, match="role forbids"):
                load_qualification_context(connection, **arguments)
            with pytest.raises(ValueError, match="role forbids"):
                require_automatic_acceptance(
                    connection,
                    **scope,
                    evaluation_key=final.run_id,
                    qualification=loaded,
                    scope=context.evidence.scope[0],
                )
            with pytest.raises(ValueError, match="role forbids"):
                change_registry(
                    connection,
                    **scope,
                    actor_id=actor,
                    expected_revision=before.revision,
                    candidate_key=release.release_id,
                    evaluation_key=final.run_id,
                    policy=PromotionPolicy(1, 0.0),
                    qualification=loaded,
                )
            assert load_registry(connection, **scope) == before
            assert (
                connection.execute(
                    sa.select(sa.func.count()).select_from(model_schema.promotion_event)
                ).scalar_one()
                == count
            )
            transaction.rollback()
        partial = AnnotationOutcomeRef(
            canary.release_key, canary.audit_key + "-partial", canary.evidence_key + "-partial"
        )
        with pytest.raises(ValueError, match="adverse or missing observations"):
            load_qualification_context(connection, **(arguments | {"canary": partial}))
        # Even a valid, committed model-bound outcome cannot reuse prior audit groups.
        with pytest.raises(ValueError, match="canary groups must be fresh"):
            load_qualification_context(
                connection,
                **(
                    arguments
                    | {
                        "annotation": AnnotationOutcomeRef(
                            canary.release_key,
                            canary.audit_key + "-ordinary",
                            canary.evidence_key + "-ordinary",
                        )
                    }
                ),
            )
        assert load_registry(connection, **scope) == before
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(model_schema.promotion_event)
            ).scalar_one()
            == count
        )
    return {
        "scope": "synthetic canonical assembly/acceptance mechanics, never domain quality or HTTP",
        "reconnected_model_report_policy_final_and_both_measured_outcomes": True,
        "complete_fresh_canary_passes_and_partial_outcome_fails": True,
        "prior_audit_group_reuse_rejected_with_registry_unchanged": True,
        "locked_development_identity_rejected_by_assembly_acceptance_and_registry": True,
    }
