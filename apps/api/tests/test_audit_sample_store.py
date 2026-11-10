"""Canonical decoder and tamper guards; PostgreSQL sampling runs in Actions smoke."""

import io
import json
import runpy
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from labelloop_api import annotation_audit_store, audit_sample_store, qualification_schema
from labelloop_api.audit_sample_store import (
    canonical_candidate,
    freeze_audit_sample,
    load_audit_sample,
)
from labelloop_core.audit import AuditObservation, AuditSample, select_audit_sample
from labelloop_core.qualification_data import record_sha256
from labelloop_core.qualification_wire import dumps, loads


def canonical_row(**changes):
    return (
        dict(
            asset_key="ordinary",
            width=32,
            height=32,
            revision=2,
            parent_revision=1,
            actor_kind="system",
            actor_id="original",
            objects=[],
            class_map={"class-a": 0},
            approval_state="auto_accepted",
            review_tier="machine_verified_no_human_review",
            scope_completeness="exhaustive_for_scope",
            training_eligible=True,
            gold_eligible=False,
        )
        | changes
    )


def candidate(**changes):
    return canonical_candidate(
        canonical_row(**changes),
        pipeline_id="server-policy-pipeline",
        prior_reviewers=frozenset({"earlier-human"}),
    )


def test_canonical_candidate_keeps_exact_revision_negative_and_prior_actors():
    value = candidate()
    assert value.revision.revision == 2
    assert value.revision.objects == ()
    assert value.prior_reviewer_ids == {"original", "earlier-human"}
    assert loads(dumps(AuditSample("sample", (value,), 3)), AuditSample) == AuditSample(
        "sample", (value,), 3
    )


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"scope_completeness": "partial", "training_eligible": False}, "complete ordinary"),
        ({"approval_state": "draft", "training_eligible": False}, "ordinary accepted"),
        (
            {"objects": [{"object_id": "a", "class_id": "foreign", "box": [0, 0, 8, 8]}]},
            "frozen ontology",
        ),
        (
            {"objects": [{"object_id": "a", "class_id": "class-a", "box": [0, 0, 80, 8]}]},
            "inside image dimensions",
        ),
    ],
)
def test_invalid_canonical_population_fails_closed(changes, message):
    with pytest.raises(ValueError, match=message):
        candidate(**changes)


@pytest.mark.parametrize(
    "field,value", [("actor_id", ""), ("sample_key", " "), ("pipeline_id", "")]
)
def test_invalid_sample_identity_fails_before_transaction(field, value):
    arguments = dict(
        project_key="p",
        release_key="r",
        actor_id="admin",
        sample_key="audit",
        pipeline_id="pipeline",
        size=1,
        seed=7,
    )
    with pytest.raises(ValueError):
        freeze_audit_sample(None, **(arguments | {field: value}))


class StoredSample:
    def __init__(self, record):
        self.record = record

    def execute(self, statement):
        return self

    def mappings(self):
        return self

    def one(self):
        return self.record


def stored_sample():
    population = AuditSample("audit-population", (candidate(), candidate(asset_key="second")), 17)
    sample = select_audit_sample(population.candidates, sample_id="audit", size=1, seed=17)
    snapshot = {
        "population": json.loads(dumps(population)),
        "groups": {"ordinary": "shared", "second": "shared"},
    }
    return {
        "population_sha256": record_sha256(snapshot),
        "record": snapshot | {"sample": json.loads(dumps(sample))},
    }


def test_recovery_reproduces_selection_and_counts_correlated_sources_once():
    value = stored_sample()
    # Select both sources to prove two images do not inflate independent group support.
    population = loads(json.dumps(value["record"]["population"]), AuditSample)
    value["record"]["sample"] = json.loads(
        dumps(select_audit_sample(population.candidates, sample_id="audit", size=2, seed=17))
    )
    recovered = load_audit_sample(StoredSample(value), project_id="project", sample_key="audit")
    assert len(recovered.sample.candidates) == 2
    assert recovered.independent_groups == 1


@pytest.mark.parametrize("tamper", ["population", "groups", "selection", "identity", "root"])
def test_recovery_rejects_population_and_selection_substitution(tamper):
    value = deepcopy(stored_sample())
    record = value["record"]
    if tamper == "population":
        record["population"]["payload"]["candidates"][0]["revision"]["revision"] = 3
    elif tamper == "groups":
        record["groups"]["ordinary"] = "invented-independent-group"
    elif tamper == "selection":
        sample = loads(json.dumps(record["sample"]), AuditSample)
        chosen = sample.candidates[0]
        record["sample"] = json.loads(
            dumps(replace(sample, candidates=(replace(chosen, pipeline_id="forged"),)))
        )
    elif tamper == "identity":
        record["sample"]["payload"]["sample_id"] = "other"
    else:
        record["sample"]["record_type"] = "QualificationContext"
    with pytest.raises(ValueError):
        load_audit_sample(StoredSample(value), project_id="project", sample_key="audit")


def test_bound_sample_lookup_includes_project_release_and_sample_identity():
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.one.return_value = stored_sample()
    load_audit_sample(connection, project_id="project", release_id="release", sample_key="audit")
    statement = connection.execute.call_args.args[0]
    assert statement.compile().params == {
        "project_id_1": "project",
        "sample_key_1": "audit",
        "release_id_1": "release",
    }


@pytest.mark.parametrize("change", ["none", "membership", "exclusion", "groups", "relation"])
def test_current_sample_rechecks_entire_population_not_only_selection(monkeypatch, change):
    sample_row = stored_sample() | {"release_id": "release"}
    membership = {"ordinary": 2, "second": 2}
    release_row = {
        "sample_revisions": membership | ({"second": 3} if change == "membership" else {}),
        "excluded_asset_ids": ["second"] if change == "exclusion" else [],
        "assignment_version": "pinned-version",
    }
    connection = MagicMock()
    first, second = MagicMock(), MagicMock()
    first.mappings.return_value.one.return_value = sample_row
    second.mappings.return_value.one.return_value = release_row
    connection.execute.side_effect = [first, second]
    checker = MagicMock(return_value=sample_row["record"]["groups"])
    if change == "groups":
        checker.return_value = {"ordinary": "changed", "second": "changed"}
    elif change == "relation":
        checker.side_effect = ValueError("current source relationships cross pinned splits")
    monkeypatch.setattr(audit_sample_store, "current_release_groups", checker)
    arguments = dict(project_id="project", sample_key="audit", require_current_groups=True)
    if change == "none":
        assert load_audit_sample(connection, **arguments) == load_audit_sample(
            StoredSample(sample_row), project_id="project", sample_key="audit"
        )
        checker.assert_called_once_with(
            connection,
            project_id="project",
            assignment_version="pinned-version",
            asset_keys=frozenset(membership),
        )
    else:
        with pytest.raises(ValueError):
            load_audit_sample(connection, **arguments)
        if change in {"membership", "exclusion"}:
            checker.assert_not_called()


@pytest.mark.parametrize("kind", ["clean", "partial", "adverse", "truncated", "unsampled"])
def test_audit_write_derives_sample_binding_and_verdict(monkeypatch, kind):
    stored = stored_sample()
    if kind == "partial":
        population = loads(json.dumps(stored["record"]["population"]), AuditSample)
        stored["record"]["sample"] = json.loads(
            dumps(
                select_audit_sample(
                    population.candidates, sample_id="audit", size=2, seed=population.seed
                )
            )
        )
    frozen = load_audit_sample(StoredSample(stored), project_id="project", sample_key="audit")
    selected = frozen.sample.candidates[0]
    observation = AuditObservation(
        selected.asset_id, 2, "independent", True, kind != "adverse", True, kind == "truncated"
    )
    connection = MagicMock()
    release = MagicMock()
    release.mappings.return_value.one.return_value = {
        "id": "release",
        "sample_revisions": {
            item.asset_id: item.revision.revision for item in frozen.sample.candidates
        },
    }
    prior = MagicMock()
    prior.mappings.return_value.one.return_value = {
        "actor_id": "original",
        "approval_state": "auto_accepted",
    }
    actors = MagicMock()
    actors.scalars.return_value = []
    connection.execute.side_effect = [release, prior, actors, MagicMock()]
    monkeypatch.setattr(annotation_audit_store, "lock_project", lambda *args: "project")
    loader = MagicMock(return_value=frozen)
    monkeypatch.setattr(annotation_audit_store, "load_audit_sample", loader)
    annotation_audit_store.record_annotation_audit(
        connection,
        project_key="p",
        release_key="r",
        audit_key="result",
        actor_id="independent",
        sample_key=None if kind == "unsampled" else "audit",
        observations=(observation,),
    )
    saved = connection.execute.call_args.args[0].compile().params
    assert saved["vetoed_revisions"] == (
        {selected.asset_id: 2} if kind in {"adverse", "truncated"} else {}
    )
    if kind == "unsampled":
        loader.assert_not_called()
        assert "sample" not in saved["record"]
    else:
        loader.assert_called_once_with(
            connection, project_id="project", sample_key="audit", release_id="release"
        )
        binding = saved["record"]["sample"]
        assert binding["population_sha256"] == frozen.population_sha256
        assert binding["group_ids"] == frozen.group_ids
        assert binding["verdict"]["passed"] == (kind == "clean")
        assert binding["verdict"]["pending_asset_ids"] == (
            (frozen.sample.candidates[1].asset_id,) if kind == "partial" else ()
        )


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"asset_id": "unselected"}, "outside the frozen sample"),
        ({"revision": 3}, "stale source revision"),
        ({"reviewer_id": " ORIGINAL "}, "independent"),
    ],
)
def test_sample_binding_rejects_substitution_before_any_audit_insert(monkeypatch, changes, message):
    frozen = load_audit_sample(
        StoredSample(stored_sample()), project_id="project", sample_key="audit"
    )
    selected = frozen.sample.candidates[0]
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.one.return_value = {
        "id": "release",
        "sample_revisions": {selected.asset_id: 2, "unselected": 2},
    }
    monkeypatch.setattr(annotation_audit_store, "lock_project", lambda *args: "project")
    monkeypatch.setattr(annotation_audit_store, "load_audit_sample", lambda *args, **kwargs: frozen)
    observation = replace(
        AuditObservation(selected.asset_id, 2, "independent", True, True, True), **changes
    )
    with pytest.raises(ValueError, match=message):
        annotation_audit_store.record_annotation_audit(
            connection,
            project_key="p",
            release_key="r",
            audit_key="rejected",
            actor_id=observation.reviewer_id,
            sample_key="audit",
            observations=(observation,),
        )
    assert connection.execute.call_count == 1  # Only release read, no append.


def test_sampling_migration_is_frozen_append_only_and_after_joined_histories():
    migration = runpy.run_path(
        str(Path(__file__).parents[1] / "migrations/versions/qualification_0004_audit_sample.py")
    )
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    future = sa.Column("future_column", sa.String)
    qualification_schema.audit_sample.append_column(future)
    try:
        with Operations.context(context):
            migration["upgrade"]()
            migration["downgrade"]()
    finally:
        qualification_schema.audit_sample._columns.remove(future)
    sql = output.getvalue()
    assert "future_column" not in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "BEFORE TRUNCATE" in sql
    assert "CREATE TABLE qualification_audit_sample" in sql
    assert "DROP TABLE qualification_audit_sample" in sql
    assert migration["down_revision"] == "m5_jobs_0001"
