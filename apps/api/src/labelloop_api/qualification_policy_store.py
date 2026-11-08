"""Current qualification configuration, serialized with registry and acceptance writes.

Only an authorized service may append configuration; actor strings are not credentials.
A stored pipeline identity is configuration, not evidence it produced any labels.
"""

import json
from dataclasses import dataclass, replace

import sqlalchemy as sa

from labelloop_api import qualification_schema
from labelloop_api.qualification_store import lock_project
from labelloop_api.schema import preprocessing_recipe
from labelloop_core.qualification import QualificationContext, QualificationPolicy
from labelloop_core.qualification_data import require_count, require_name
from labelloop_core.qualification_wire import dumps, loads


@dataclass(frozen=True, slots=True)
class StoredQualificationPolicy:
    revision: int
    policy: QualificationPolicy


def _scope(connection, project_key, recipe_key):
    project_id = lock_project(connection, project_key)
    recipe_id = connection.execute(
        sa.select(preprocessing_recipe.c.id).where(preprocessing_recipe.c.recipe_key == recipe_key)
    ).scalar_one()
    return project_id, recipe_id


def _latest(connection, project_id, recipe_id):
    return (
        connection.execute(
            sa.select(qualification_schema.policy_event)
            .where(
                qualification_schema.policy_event.c.project_id == project_id,
                qualification_schema.policy_event.c.recipe_id == recipe_id,
            )
            .order_by(qualification_schema.policy_event.c.revision.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )


def _decode(row, project_key, recipe_key):
    require_count(row["revision"], minimum=1)
    require_name(row["actor_id"])
    policy = loads(json.dumps(row["record"]), QualificationPolicy)
    if (policy.pipeline.project_id, policy.pipeline.recipe_id) != (project_key, recipe_key):
        raise ValueError("qualification policy project or recipe mismatch")
    return StoredQualificationPolicy(row["revision"], policy)


def load_qualification_policy(
    connection: sa.Connection, *, project_key: str, recipe_key: str
) -> StoredQualificationPolicy:
    project_id, recipe_id = _scope(connection, project_key, recipe_key)
    row = _latest(connection, project_id, recipe_id)
    if row is None:
        raise ValueError("missing current qualification policy retains the incumbent")
    return _decode(row, project_key, recipe_key)


def append_qualification_policy(
    connection: sa.Connection,
    *,
    project_key: str,
    recipe_key: str,
    actor_id: str,
    expected_revision: int,
    policy: QualificationPolicy,
) -> StoredQualificationPolicy:
    """Append a service-owned version; null targets are allowed but never qualify.

    A material policy, pipeline or scope change invalidates old qualifications.
    Authorization and canonical provenance checks belong to the calling service.
    """
    require_name(actor_id)
    require_count(expected_revision)
    if (policy.pipeline.project_id, policy.pipeline.recipe_id) != (project_key, recipe_key):
        raise ValueError("qualification policy cannot cross project or recipe boundaries")
    record = json.loads(dumps(policy))
    with connection.begin_nested():
        project_id, recipe_id = _scope(connection, project_key, recipe_key)
        row = _latest(connection, project_id, recipe_id)
        current = _decode(row, project_key, recipe_key).revision if row is not None else 0
        if current != expected_revision:
            raise ValueError("stale qualification policy revision")
        revision = current + 1
        connection.execute(
            sa.insert(qualification_schema.policy_event).values(
                project_id=project_id,
                recipe_id=recipe_id,
                revision=revision,
                actor_id=actor_id,
                record=record,
            )
        )
    return StoredQualificationPolicy(revision, policy)


def current_policy_context(
    connection: sa.Connection, context: QualificationContext
) -> QualificationContext:
    """Reject substituted/historical configuration before any qualification gate."""
    evidence = context.evidence
    current = load_qualification_policy(
        connection,
        project_key=evidence.pipeline.project_id,
        recipe_key=evidence.pipeline.recipe_id,
    ).policy
    if (evidence.pipeline, evidence.scope, evidence.targets) != (
        current.pipeline,
        current.scope,
        current.targets,
    ):
        raise ValueError("stale or substituted qualification policy, pipeline or scope")
    return replace(context, current_pipeline=current.pipeline)
