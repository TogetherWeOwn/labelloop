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
    EvidenceUse,
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
    state = loads(json.dumps(record), FinalTestState)
    if not isinstance(state, FinalTestState) or (
        state.dataset.project_id,
        state.dataset.release_id,
    ) != (project_key, dataset_key):
        raise ValueError("final-test journal identity mismatch")
    return state


def require_development_uses(
    connection: sa.Connection,
    *,
    project_key: str,
    development_set_key: str,
    calibration_set_key: str | None = None,
) -> None:
    """A supplied role cannot hide an existing final-test reservation.

    An explicitly adapted test may be development/regression data, but never a
    calibration set or a later independent final. Quarantined data is unusable.
    Absence of a journal is not proof of canonical role or group independence;
    those still require the trusted producer and the complete qualification gate.
    """
    project_id = lock_project(connection, project_key)
    uses: list[tuple[str, EvidenceUse]] = [(development_set_key, "checkpoint_selection")]
    if calibration_set_key is not None:
        uses.append((calibration_set_key, "threshold_tuning"))
    for dataset_key, purpose in uses:
        record = connection.execute(
            sa.select(qualification_schema.final_test_event.c.record)
            .where(
                qualification_schema.final_test_event.c.project_id == project_id,
                qualification_schema.final_test_event.c.dataset_key == dataset_key,
            )
            .order_by(qualification_schema.final_test_event.c.revision.desc())
            .limit(1)
        ).scalar_one_or_none()
        if record is None:
            continue
        state = loads(json.dumps(record), FinalTestState)
        if not isinstance(state, FinalTestState) or (
            state.dataset.project_id,
            state.dataset.release_id,
        ) != (project_key, dataset_key):
            raise ValueError("final-test journal identity mismatch")
        if state.quarantine_reasons:
            raise ValueError("quarantined final-test data cannot guide selection or thresholds")
        replace(state.dataset, role=state.effective_role).require_use(purpose)


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
        require_development_uses(
            connection,
            project_key=project_key,
            development_set_key=selection.development_set_id,
            calibration_set_key=selection.calibration_set_id,
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
        selection = loads(json.dumps(record), CandidateSelection)
        if not isinstance(selection, CandidateSelection):
            raise ValueError("invalid persisted candidate selection")
        require_development_uses(
            connection,
            project_key=project_key,
            development_set_key=selection.development_set_id,
            calibration_set_key=selection.calibration_set_id,
        )
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
    """Recheck canonical evaluation groups and replace a historical final snapshot.

    The promotion service must also load authoritative policy, pipeline, audit and
    canary records. This helper alone does not authorize or qualify a promotion.
    Keep the outer transaction alive through the consuming state change.
    """
    from labelloop_api.evaluation_group_store import load_evaluation_set

    dataset = context.evidence.final_test.dataset
    lineage = context.evidence.lineage
    require_development_uses(
        connection,
        project_key=dataset.project_id,
        development_set_key=lineage.development.release_id,
        calibration_set_key=lineage.calibration.release_id if lineage.calibration else None,
    )
    current = load_final_test(
        connection, project_key=dataset.project_id, dataset_key=dataset.release_id
    )
    for expected in (lineage.development, lineage.calibration, dataset):
        if expected is None:
            continue
        canonical = load_evaluation_set(
            connection,
            project_key=dataset.project_id,
            dataset_key=expected.release_id,
            role=expected.role,
        )
        if expected != canonical or (expected is dataset and current.dataset != canonical):
            raise ValueError(
                "qualification evaluation groups differ from current canonical membership"
            )
    return replace(context, current_final_test=current)
