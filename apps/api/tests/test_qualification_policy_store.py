"""Current configuration is mandatory; PostgreSQL concurrency runs in Actions smoke."""

import io
import json
import runpy
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from qualification_fixture import qualification_context, synthetic_final
from test_m5_integration_contract import make_release, make_report

from labelloop_api import qualification_policy_store as store
from labelloop_api import qualification_schema
from labelloop_core.qualification import QualificationPolicy
from labelloop_core.qualification_data import record_sha256
from labelloop_core.qualification_wire import dumps
from labelloop_core.registry import PromotionPolicy


@pytest.fixture
def context():
    release = make_release(320, "challenger")
    final = synthetic_final(make_report(release), run_id="fixture-final", dataset_id="final-set")
    release = replace(release, evaluation_run_ids=(*release.evaluation_run_ids, final.run_id))
    return qualification_context(release, final, PromotionPolicy(1, 0.0))


def policy_for(context):
    evidence = context.evidence
    return QualificationPolicy(evidence.pipeline, evidence.scope, evidence.targets)


@pytest.mark.parametrize(
    "mutation", ["valid", "missing", "project", "recipe", "root", "revision", "actor"]
)
def test_policy_loader_rejects_missing_malformed_and_foreign_records(context, mutation):
    policy = policy_for(context)
    if mutation in {"project", "recipe"}:
        field = "project_id" if mutation == "project" else "recipe_id"
        policy = replace(policy, pipeline=replace(policy.pipeline, **{field: "foreign"}))
    row = {"revision": 1, "actor_id": "admin", "record": json.loads(dumps(policy))}
    if mutation == "missing":
        row = None
    elif mutation == "root":
        row["record"] = json.loads(dumps(context))
    elif mutation == "revision":
        row["revision"] = True
    elif mutation == "actor":
        row["actor_id"] = " "
    connection = MagicMock()
    with (
        patch.object(store, "_scope", return_value=("project-id", "recipe-id")),
        patch.object(store, "_latest", return_value=row),
    ):
        args = dict(
            project_key=context.evidence.pipeline.project_id,
            recipe_key=context.evidence.pipeline.recipe_id,
        )
        if mutation == "valid":
            assert store.load_qualification_policy(connection, **args).policy == policy
        else:
            with pytest.raises(ValueError):
                store.load_qualification_policy(connection, **args)


@pytest.mark.parametrize("mutation", ["current", "targets", "scope", "pipeline", "missing"])
def test_current_context_cannot_select_older_or_substituted_configuration(context, mutation):
    policy = policy_for(context)
    if mutation == "targets":
        targets = replace(policy.targets, min_class_support=999)
        policy = replace(
            policy,
            targets=targets,
            pipeline=replace(policy.pipeline, acceptance_policy_sha256=record_sha256(targets)),
        )
    elif mutation == "scope":
        policy = replace(policy, scope=(replace(policy.scope[0], condition="changed"),))
    elif mutation == "pipeline":
        policy = replace(policy, pipeline=replace(policy.pipeline, teacher_sha256="a" * 64))
    with patch.object(
        store, "load_qualification_policy", return_value=store.StoredQualificationPolicy(7, policy)
    ) as loader:
        if mutation == "missing":
            loader.side_effect = ValueError("missing current qualification policy")
        if mutation == "current":
            forged = replace(
                context, current_pipeline=replace(context.current_pipeline, prompt_sha256="e" * 64)
            )
            assert store.current_policy_context(None, forged) == context
        else:
            with pytest.raises(ValueError, match="missing current|stale or substituted"):
                store.current_policy_context(None, context)


@pytest.mark.parametrize("mutation", ["actor", "revision", "project", "recipe"])
def test_invalid_append_is_rejected_before_database_access(context, mutation):
    policy = policy_for(context)
    args = dict(
        project_key=policy.pipeline.project_id,
        recipe_key=policy.pipeline.recipe_id,
        actor_id="admin",
        expected_revision=0,
        policy=policy,
    )
    args[
        {
            "actor": "actor_id",
            "revision": "expected_revision",
            "project": "project_key",
            "recipe": "recipe_key",
        }[mutation]
    ] = {"actor": " ", "revision": True, "project": "foreign", "recipe": "foreign"}[mutation]
    with pytest.raises(ValueError):
        store.append_qualification_policy(None, **args)


def test_policy_migration_is_frozen_and_immutable():
    migration = runpy.run_path(
        str(Path(__file__).parents[1] / "migrations/versions/qualification_0005_policy.py")
    )
    table = qualification_schema.policy_event
    future = sa.Column("future_column", sa.String)
    table.append_column(future)
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    try:
        with Operations.context(context):
            migration["upgrade"]()
            migration["downgrade"]()
    finally:
        table._columns.remove(future)
    sql = output.getvalue()
    assert "future_column" not in sql
    assert "PRIMARY KEY (project_id, recipe_id, revision)" in sql
    assert "BEFORE UPDATE OR DELETE ON qualification_policy_event" in sql
    assert "BEFORE TRUNCATE ON qualification_policy_event" in sql
    assert "DROP TABLE qualification_policy_event" in sql
    assert migration["down_revision"] == "qualification_0004"
