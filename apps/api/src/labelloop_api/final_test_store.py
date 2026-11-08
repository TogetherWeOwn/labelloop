"""Transaction-bound final-test journal; not an HTTP authorization boundary.

The service authorizes the principal and constructs EvaluationSet from canonical
transitive groups before calling this port. A selection commits before final access;
exposure commits before returning any result, even if a later promotion fails.
"""

import json
from dataclasses import replace
from typing import Literal

import sqlalchemy as sa

from labelloop_api import model_schema, qualification_schema
from labelloop_api.annotation_audit_store import lock_project
from labelloop_api.model_store import load_release, stored_report
from labelloop_api.schema import canonical_dataset_release
from labelloop_core.evaluation_lifecycle import (
    CandidateSelection,
    EvaluationSet,
    FinalTestState,
    select_candidate,
)
from labelloop_core.qualification import QualificationContext
from labelloop_core.qualification_wire import dumps, loads


def _actor(actor_id: str) -> None:
    if not actor_id.strip():
        raise ValueError("final-test events require an authenticated actor")


def _model(connection: sa.Connection, project_id: object, model_key: str):
    model_id = connection.execute(
        sa.select(model_schema.model_release.c.id).where(
            model_schema.model_release.c.project_id == project_id,
            model_schema.model_release.c.release_key == model_key,
        )
    ).scalar_one()
    release = load_release(connection, model_id)
    assert release is not None
    return release


def load_final_test(
    connection: sa.Connection, *, project_key: str, dataset_key: str
) -> FinalTestState:
    # All transitions and registry mutations take the same project lock first.
    project_id = lock_project(connection, project_key)
    record = connection.execute(
        sa.select(qualification_schema.final_test_event.c.record)
        .where(
            qualification_schema.final_test_event.c.project_id == project_id,
            qualification_schema.final_test_event.c.dataset_key == dataset_key,
        )
        .order_by(qualification_schema.final_test_event.c.revision.desc())
        .limit(1)
    ).scalar_one()
    state = loads(json.dumps(record))
    if not isinstance(state, FinalTestState) or (
        state.dataset.project_id,
        state.dataset.release_id,
    ) != (project_key, dataset_key):
        raise ValueError("final-test journal identity mismatch")
    return state


def _append(connection, project_id, actor_id, action, state):
    connection.execute(
        sa.insert(qualification_schema.final_test_event).values(
            project_id=project_id,
            dataset_key=state.dataset.release_id,
            revision=state.revision,
            actor_id=actor_id,
            action=action,
            record=json.loads(dumps(state)),
        )
    )


def register_final_test(
    connection: sa.Connection, *, dataset: EvaluationSet, actor_id: str
) -> FinalTestState:
    """Register a trusted canonical membership once; never reset an existing test.

    This low-level port does not derive grouping or establish adjudicated truth.
    Dataset membership/role is not an HTTP body or a model assertion.
    """
    _actor(actor_id)
    state = FinalTestState(dataset)
    with connection.begin_nested():
        project_id = lock_project(connection, dataset.project_id)
        connection.execute(
            sa.select(canonical_dataset_release.c.id).where(
                canonical_dataset_release.c.project_id == project_id,
                canonical_dataset_release.c.release_key == dataset.release_id,
                canonical_dataset_release.c.frozen,
            )
        ).scalar_one()
        _append(connection, project_id, actor_id, "register", state)
    return state


def record_candidate_selection(
    connection: sa.Connection,
    *,
    project_key: str,
    model_key: str,
    selection_key: str,
    development_key: str,
    development_set: EvaluationSet,
    actor_id: str,
    calibration_key: str | None = None,
    calibration_set: EvaluationSet | None = None,
) -> CandidateSelection:
    """Pin persisted model/report payloads before permitting final-test exposure."""
    _actor(actor_id)
    with connection.begin_nested():
        project_id = lock_project(connection, project_key)
        release = _model(connection, project_id, model_key)
        selection = select_candidate(
            release,
            stored_report(connection, release, development_key),
            development_set,
            selection_id=selection_key,
            calibration=stored_report(connection, release, calibration_key)
            if calibration_key is not None
            else None,
            calibration_set=calibration_set,
        )
        connection.execute(
            sa.insert(qualification_schema.candidate_selection).values(
                project_id=project_id,
                selection_key=selection_key,
                actor_id=actor_id,
                record=json.loads(dumps(selection)),
            )
        )
    return selection


def expose_final_test(
    connection: sa.Connection,
    *,
    project_key: str,
    dataset_key: str,
    model_key: str,
    selection_key: str,
    evaluation_key: str,
    expected_revision: int,
    actor_id: str,
) -> FinalTestState:
    _actor(actor_id)
    with connection.begin_nested():
        project_id = lock_project(connection, project_key)
        state = load_final_test(connection, project_key=project_key, dataset_key=dataset_key)
        record = connection.execute(
            sa.select(qualification_schema.candidate_selection.c.record).where(
                qualification_schema.candidate_selection.c.project_id == project_id,
                qualification_schema.candidate_selection.c.selection_key == selection_key,
            )
        ).scalar_one()
        selection = loads(json.dumps(record))
        if not isinstance(selection, CandidateSelection):
            raise ValueError("invalid persisted candidate selection")
        release = _model(connection, project_id, model_key)
        changed = state.expose(
            release,
            selection,
            stored_report(connection, release, evaluation_key),
            expected_revision=expected_revision,
        )
        _append(connection, project_id, actor_id, "expose", changed)
    return changed


def invalidate_final_test(
    connection: sa.Connection,
    *,
    project_key: str,
    dataset_key: str,
    action: Literal["adapt", "quarantine"],
    reason: str,
    expected_revision: int,
    actor_id: str,
) -> FinalTestState:
    _actor(actor_id)
    if action not in {"adapt", "quarantine"}:
        raise ValueError("unknown final-test invalidation")
    with connection.begin_nested():
        project_id = lock_project(connection, project_key)
        state = load_final_test(connection, project_key=project_key, dataset_key=dataset_key)
        transition = state.record_adaptation if action == "adapt" else state.quarantine
        changed = transition(reason, expected_revision=expected_revision)
        _append(connection, project_id, actor_id, action, changed)
    return changed


def current_qualification_context(
    connection: sa.Connection, context: QualificationContext
) -> QualificationContext:
    """Replace a historical final snapshot, never trust a serialized old success.

    The promotion service must also load authoritative policy, pipeline, audit and
    canary records. This helper alone does not authorize or qualify a promotion.
    """
    dataset = context.evidence.final_test.dataset
    return replace(
        context,
        current_final_test=load_final_test(
            connection, project_key=dataset.project_id, dataset_key=dataset.release_id
        ),
    )
