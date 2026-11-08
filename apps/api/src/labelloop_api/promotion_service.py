"""Trusted promotion orchestration; no caller-supplied outcome or policy grants."""

import json
from uuid import uuid4

import sqlalchemy as sa

from labelloop_api import model_store
from labelloop_api.qualification_context_store import (
    AnnotationOutcomeRef,
    load_qualification_context,
    select_final_report,
)
from labelloop_api.qualification_store import lock_project
from labelloop_core.qualification_data import require_count, require_name
from labelloop_core.registry import PromotionPolicy, RegistryState


def promote_candidate(
    connection: sa.Connection,
    *,
    project_key: str,
    recipe_key: str,
    model_key: str,
    annotation_release_key: str,
    canary_release_key: str,
    actor_id: str,
    expected_revision: int,
    require_fixture_policy: bool = False,
) -> RegistryState:
    """Assemble current evidence and consume it in the same project-locked transaction.

    The authorized caller owns candidate/population choice and original execution
    and measurement provenance. This is not an HTTP schema or an authorization
    boundary. Neither scores, report/outcome IDs, lineage, targets nor a qualification
    assertion are accepted. Missing or competing evidence fails closed. The caller
    must retain the outer transaction through commit; no model/provider I/O belongs
    inside it. The registry rechecks current gates, durable vetoes and non-regression.
    The HTTP adapter requires fixture policy until authoritative execution and
    measurement producers are connected; that restriction is not a request field.
    """
    if type(require_fixture_policy) is not bool:
        raise ValueError("fixture policy restriction must be boolean")
    for value in (
        project_key,
        recipe_key,
        model_key,
        annotation_release_key,
        canary_release_key,
        actor_id,
    ):
        require_name(value)
    require_count(expected_revision)
    lock_project(connection, project_key)
    registry = model_store.load_registry(connection, project_key, recipe_key)
    if registry.revision != expected_revision:
        raise ValueError("stale registry revision")
    if registry.champion is not None and registry.champion.release_id == model_key:
        raise ValueError("candidate is already champion")
    context = load_qualification_context(
        connection,
        project_key=project_key,
        recipe_key=recipe_key,
        model_key=model_key,
        qualification_key=str(uuid4()),
        annotation=AnnotationOutcomeRef(annotation_release_key),
        canary=AnnotationOutcomeRef(canary_release_key),
    )
    assessment = next(
        item
        for item in context.current_final_test.assessments
        if item.selection.selection_id == context.evidence.selection_id
    )
    report = model_store.decode_report(json.loads(assessment.report_json))
    targets = context.evidence.targets
    if require_fixture_policy and not targets.fixture_only:
        raise ValueError("HTTP promotion requires explicit fixture-only policy")
    targets.require_configured()
    policy = PromotionPolicy(
        targets.min_class_support, targets.min_map50_95, targets.max_regression
    )
    incumbent = (
        select_final_report(connection, registry.champion, dataset_key=report.dataset_release_id)
        if registry.champion is not None
        else None
    )
    return model_store.change_registry(
        connection,
        project_key=project_key,
        recipe_key=recipe_key,
        actor_id=actor_id,
        expected_revision=expected_revision,
        candidate_key=model_key,
        evaluation_key=report.run_id,
        policy=policy,
        incumbent_evaluation_key=incumbent.run_id if incumbent is not None else None,
        qualification=context,
    )
