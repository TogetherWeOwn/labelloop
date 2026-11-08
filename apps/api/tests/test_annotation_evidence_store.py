"""Measured outcome binding; truth provenance and HTTP authority are separate gates."""

import io
import json
import runpy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_annotation_audit_loader import stored_result

from labelloop_api import annotation_evidence_store as store
from labelloop_api import qualification_schema
from labelloop_api.annotation_audit_store import StoredAnnotationAudit
from labelloop_core.qualification import AnnotationEvidence, AnnotationMeasurement
from labelloop_core.qualification_data import ScopeCell, SourceGroup
from labelloop_core.qualification_wire import dumps, loads


def setup(monkeypatch, kind="clean", canary=False):
    connection, _, _, frozen, observations, verdict = stored_result(monkeypatch, kind)
    evidence = AnnotationEvidence(
        frozen.sample,
        observations,
        tuple(
            SourceGroup(key, group, "fixture", ("ordinary",))
            for key, group in frozen.group_ids.items()
        ),
        "constructive_synthetic",
        (
            AnnotationMeasurement(
                ScopeCell("class", "fixture", "ordinary"), ("shared",), 1, 1, 1, 1, 0.0
            ),
        ),
    )
    row = dict(
        actor_id="measurement-auditor",
        record=json.loads(dumps(evidence)),
        canary_model_id="model-id" if canary else None,
        canary_release_sha256="a" * 64 if canary else None,
    )
    connection.execute.return_value.mappings.return_value.one.return_value = row
    monkeypatch.setattr(store, "lock_project", MagicMock(return_value="project-id"))
    monkeypatch.setattr(store, "_audit", MagicMock(return_value={"id": "audit-id"}))
    loader = MagicMock(return_value=StoredAnnotationAudit(frozen, observations, verdict))
    monkeypatch.setattr(store, "load_annotation_audit", loader)
    monkeypatch.setattr(store, "_canary", MagicMock(return_value=("model-id", "a" * 64)))
    return connection, evidence, row, loader


def arguments(**changes):
    return (
        dict(
            project_key="project", release_key="release", audit_key="audit", evidence_key="measured"
        )
        | changes
    )


@pytest.mark.parametrize("kind", ["clean", "partial", "adverse", "truncated"])
@pytest.mark.parametrize("canary", [False, True])
def test_outcomes_roundtrip_without_turning_membership_into_quality(monkeypatch, kind, canary):
    connection, evidence, _, loader = setup(monkeypatch, kind, canary)
    assert loads(dumps(evidence), AnnotationEvidence) == evidence
    args = arguments(canary_model_key="candidate" if canary else None)
    stored = store.record_annotation_evidence(
        connection, **args, actor_id="auditor", evidence=evidence
    )
    assert stored.evidence == evidence
    assert stored.canary_release_sha256 == ("a" * 64 if canary else None)
    record = connection.execute.call_args.args[0]
    assert record.table.name == "qualification_annotation_evidence"
    assert record.compile().params["record"] == json.loads(dumps(evidence))
    assert store.load_annotation_evidence(connection, **args) == stored
    loader.assert_called_with(
        connection,
        project_key="project",
        release_key="release",
        audit_key="audit",
        require_current_groups=True,
    )
    assert loader.return_value.verdict.passed == (kind == "clean")
    store.load_annotation_evidence(connection, **args, require_current_groups=False)
    assert loader.call_args.kwargs["require_current_groups"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "sample",
        "observations",
        "groups",
        "missing-source",
        "root",
        "actor",
        "model",
        "digest",
        "ordinary-as-canary",
        "canary-as-ordinary",
    ],
)
def test_loader_rejects_substitution_before_returning_measurements(monkeypatch, mutation):
    canary = mutation in {"model", "digest", "canary-as-ordinary"}
    connection, evidence, row, _ = setup(monkeypatch, canary=canary)
    args = arguments(canary_model_key="candidate" if canary else None)
    if mutation == "sample":
        evidence = replace(evidence, sample=replace(evidence.sample, sample_id="other"))
    elif mutation == "observations":
        evidence = replace(evidence, observations=evidence.observations[:1])
    elif mutation == "groups":
        evidence = replace(
            evidence,
            sources=(replace(evidence.sources[0], group_id="other"), *evidence.sources[1:]),
        )
    elif mutation == "missing-source":
        evidence = replace(evidence, sources=evidence.sources[:1])
    row["record"] = json.loads(dumps(evidence))
    if mutation == "root":
        row["record"] = json.loads(dumps(evidence.sample))
    elif mutation == "actor":
        row["actor_id"] = " "
    elif mutation == "model":
        row["canary_model_id"] = "foreign"
    elif mutation == "digest":
        row["canary_release_sha256"] = "b" * 64
    elif mutation == "ordinary-as-canary":
        args["canary_model_key"] = "candidate"
    elif mutation == "canary-as-ordinary":
        args["canary_model_key"] = None
    with pytest.raises(ValueError):
        store.load_annotation_evidence(connection, **args)


@pytest.mark.parametrize("mutation", ["truth", "count", "geometry", "extra-field"])
def test_loader_strictly_decodes_persisted_measurements(monkeypatch, mutation):
    connection, _, row, _ = setup(monkeypatch)
    payload = row["record"]["payload"]
    if mutation == "truth":
        payload["truth_basis"] = "independent-model-agreement"
    elif mutation == "count":
        payload["measurements"][0]["accepted_labels"] = True
    elif mutation == "geometry":
        payload["measurements"][0]["max_geometry_error"] = -0.1
    else:
        payload["passed"] = True
    with pytest.raises(ValueError):
        store.load_annotation_evidence(connection, **arguments())


def test_writer_rejects_unbound_measurements_and_propagates_append_failure(monkeypatch):
    connection, evidence, _, _ = setup(monkeypatch)
    with pytest.raises(ValueError, match="differs from"):
        store.record_annotation_evidence(
            connection,
            **arguments(),
            actor_id="auditor",
            evidence=replace(evidence, observations=()),
        )
    connection.execute.assert_not_called()
    connection.execute.side_effect = RuntimeError("injected append failure")
    with pytest.raises(RuntimeError, match="append failure"):
        store.record_annotation_evidence(
            connection, **arguments(), actor_id="auditor", evidence=evidence
        )


def test_current_group_failure_is_not_hidden_by_a_persisted_clean_result(monkeypatch):
    connection, _, _, loader = setup(monkeypatch)
    loader.side_effect = ValueError("current groups conflict")
    with pytest.raises(ValueError, match="current groups conflict"):
        store.load_annotation_evidence(connection, **arguments())


@pytest.mark.parametrize(
    "field",
    ["project_key", "release_key", "audit_key", "evidence_key", "actor_id", "canary_model_key"],
)
def test_writer_validates_identifiers_before_database_access(field):
    with pytest.raises(ValueError):
        store.record_annotation_evidence(
            None, **(arguments(actor_id="auditor", evidence=None) | {field: " "})
        )


def test_queries_bind_project_release_audit_and_canary_model(monkeypatch):
    connection = MagicMock()
    store._audit(connection, "project-id", "release", "audit")
    assert connection.execute.call_args.args[0].compile().params == dict(
        project_id_1="project-id",
        project_id_2="project-id",
        release_key_1="release",
        audit_key_1="audit",
    )
    monkeypatch.setattr(store, "load_release", MagicMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(store, "record_sha256", MagicMock(return_value="a" * 64))
    store._canary(connection, "project-id", "candidate")
    assert connection.execute.call_args.args[0].compile().params == dict(
        project_id_1="project-id", release_key_1="candidate"
    )


def test_measurement_migration_is_frozen_and_append_only():
    migration = runpy.run_path(
        str(
            Path(__file__).parents[1]
            / "migrations/versions/qualification_0007_annotation_evidence.py"
        )
    )
    table = qualification_schema.annotation_evidence
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
    assert "PRIMARY KEY (project_id, evidence_key)" in sql
    assert "REFERENCES qualification_annotation_audit (id)" in sql
    assert "REFERENCES model_release (id)" in sql
    assert "BEFORE UPDATE OR DELETE ON qualification_annotation_evidence" in sql
    assert "BEFORE TRUNCATE ON qualification_annotation_evidence" in sql
    assert migration["down_revision"] == "qualification_0006"
