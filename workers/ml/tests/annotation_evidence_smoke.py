"""Synthetic measured outcome persistence, never domain truth or canary qualification."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
import sqlalchemy as sa

from labelloop_api import model_schema, qualification_schema
from labelloop_api.annotation_audit_store import load_annotation_audit, record_annotation_audit
from labelloop_api.annotation_evidence_store import (
    load_annotation_evidence,
    record_annotation_evidence,
)
from labelloop_api.model_store import load_release
from labelloop_api.qualification_store import lock_project
from labelloop_core.qualification import AnnotationEvidence, AnnotationMeasurement
from labelloop_core.qualification_data import ScopeCell, SourceGroup, record_sha256


def exercise_annotation_evidence(engine, request):
    scope = dict(project_key=request.project_id, release_key=request.dataset_release_id)
    actor = "fixture-measurement-inspector"
    with engine.begin() as connection:
        project_id = lock_project(connection, request.project_id)
        complete = load_annotation_audit(connection, **scope, audit_key="sample-complete")
        partial = load_annotation_audit(connection, **scope, audit_key="sample-partial")
        sources = tuple(
            SourceGroup(key, group, "synthetic-source", ("ordinary",))
            for key, group in complete.frozen.group_ids.items()
        )
        # These invented counts exercise storage only. They are NOT gold or a
        # representative quality estimate, and do not authorize any promotion.
        measurement = AnnotationMeasurement(
            ScopeCell("synthetic-class", "synthetic-source", "ordinary"),
            tuple(sorted(set(complete.frozen.group_ids.values()))),
            5,
            4,
            6,
            3,
            0.25,
        )
        evidence = AnnotationEvidence(
            complete.frozen.sample,
            complete.observations,
            sources,
            "constructive_synthetic",
            (measurement,),
        )
        failed = replace(evidence, observations=partial.observations)
        adverse = replace(evidence.observations[0], labels_correct=False)
        record_annotation_audit(
            connection,
            **scope,
            audit_key="measured-adverse-audit",
            actor_id=adverse.reviewer_id,
            sample_key=evidence.sample.sample_id,
            observations=(adverse,),
        )
        adverse_evidence = replace(evidence, observations=(adverse,))
        saved = {}
        for key, audit_key, outcome in (
            ("measured-complete", "sample-complete", evidence),
            ("measured-partial", "sample-partial", failed),
            ("measured-adverse", "measured-adverse-audit", adverse_evidence),
        ):
            saved[key] = (
                audit_key,
                record_annotation_evidence(
                    connection,
                    **scope,
                    audit_key=audit_key,
                    evidence_key=key,
                    actor_id=actor,
                    evidence=outcome,
                ),
            )
        models = {}
        for model_id in connection.execute(
            sa.select(model_schema.model_release.c.id).where(
                model_schema.model_release.c.project_id == project_id
            )
        ).scalars():
            release = load_release(connection, model_id)
            models.setdefault(release.recipe_id, release)
        assert len(models) == 2
        for recipe, release in models.items():
            key = "measured-canary-" + recipe
            saved[key] = (
                "sample-complete",
                record_annotation_evidence(
                    connection,
                    **scope,
                    audit_key="sample-complete",
                    evidence_key=key,
                    actor_id=actor,
                    evidence=evidence,
                    canary_model_key=release.release_id,
                ),
            )
            assert saved[key][1].canary_release_sha256 == record_sha256(release)
        with pytest.raises(ValueError, match="differs from"):
            record_annotation_evidence(
                connection,
                **scope,
                audit_key="sample-complete",
                evidence_key="forged-measured",
                actor_id=actor,
                evidence=replace(evidence, observations=failed.observations),
            )
        with pytest.raises(sa.exc.NoResultFound):
            record_annotation_evidence(
                connection,
                **scope,
                audit_key="sample-complete",
                evidence_key="foreign-canary",
                actor_id=actor,
                evidence=evidence,
                canary_model_key="unknown-candidate",
            )

    # A new connection must recover every original count, source, truth marker,
    # observation and complete model-release binding, including failed outcomes.
    with engine.begin() as connection:
        for key, (audit_key, stored) in saved.items():
            release = models.get(key.removeprefix("measured-canary-"))
            args = dict(
                **scope,
                audit_key=audit_key,
                evidence_key=key,
                canary_model_key=release.release_id if release else None,
            )
            assert load_annotation_evidence(connection, **args) == stored
            if release:
                with pytest.raises(ValueError, match="exact current model release"):
                    load_annotation_evidence(connection, **(args | {"canary_model_key": None}))
                other = next(item for item in models.values() if item != release)
                with pytest.raises(ValueError, match="exact current model release"):
                    load_annotation_evidence(
                        connection, **(args | {"canary_model_key": other.release_id})
                    )
        for changes in (
            {"project_key": "foreign-project"},
            {"release_key": request.dataset_release_id + "-identical-membership"},
            {"audit_key": "sample-partial"},
            {"evidence_key": "unknown-outcome"},
        ):
            with pytest.raises(sa.exc.NoResultFound):
                load_annotation_evidence(
                    connection,
                    **(
                        dict(**scope, audit_key="sample-complete", evidence_key="measured-complete")
                        | changes
                    ),
                )
        for statement in (
            sa.update(qualification_schema.annotation_evidence).values(actor_id="replacement"),
            sa.delete(qualification_schema.annotation_evidence),
            sa.text("TRUNCATE qualification_annotation_evidence"),
        ):
            with connection.begin_nested() as transaction:
                with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                    connection.execute(statement)
                transaction.rollback()

        def fail(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.startswith("INSERT INTO qualification_annotation_evidence"):
                raise RuntimeError("injected measured outcome failure")

        sa.event.listen(connection, "before_cursor_execute", fail)
        try:
            with pytest.raises(RuntimeError, match="measured outcome failure"):
                record_annotation_evidence(
                    connection,
                    **scope,
                    audit_key="sample-complete",
                    evidence_key="failed-measured",
                    actor_id=actor,
                    evidence=evidence,
                )
        finally:
            sa.event.remove(connection, "before_cursor_execute", fail)
        assert (
            connection.execute(
                sa.select(sa.func.count())
                .select_from(qualification_schema.annotation_evidence)
                .where(
                    qualification_schema.annotation_evidence.c.evidence_key.in_(
                        ["failed-measured", "forged-measured", "foreign-canary"]
                    )
                )
            ).scalar_one()
            == 0
        )

    def compete(_index):
        try:
            with engine.begin() as connection:
                record_annotation_evidence(
                    connection,
                    **scope,
                    audit_key="sample-complete",
                    evidence_key="racing-measured",
                    actor_id=actor,
                    evidence=evidence,
                )
            return "saved"
        except sa.exc.IntegrityError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(compete, range(2))) == ["duplicate", "saved"]
    return {
        "scope": "synthetic measured outcomes only, not fresh canary or domain quality",
        "complete_partial_adverse_reconnect_and_exact_both_recipe_model_bindings": True,
        "foreign_sample_audit_project_and_model_substitutions_rejected": True,
        "immutable_update_delete_truncate_race_and_failed_append": True,
    }
