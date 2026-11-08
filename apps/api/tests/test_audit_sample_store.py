"""Canonical decoder and tamper guards; PostgreSQL sampling runs in Actions smoke."""

import io
import json
import runpy
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from labelloop_api import qualification_schema
from labelloop_api.audit_sample_store import (
    canonical_candidate,
    freeze_audit_sample,
    load_audit_sample,
)
from labelloop_core.audit import AuditSample, select_audit_sample
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
