"""Persisted audit reassembly is not independent truth or qualification."""

import json
from dataclasses import asdict, replace
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

from labelloop_api import annotation_audit_store
from labelloop_api.annotation_audit_store import load_annotation_audit
from labelloop_api.audit_sample_store import FrozenAuditSample
from labelloop_core.audit import AuditCandidate, AuditObservation, AuditSample, assess_audit
from labelloop_core.domain import AnnotationRevision


def stored_result(monkeypatch, kind="clean"):
    revision = AnnotationRevision(
        revision=1,
        objects=(),
        actor_kind="system",
        approval_state="auto_accepted",
        review_tier="machine_verified_no_human_review",
        scope_completeness="exhaustive_for_scope",
        training_eligible=True,
        gold_eligible=False,
    )
    sample = AuditSample(
        "sample",
        tuple(
            AuditCandidate(key, revision, "pipeline", frozenset({"original"})) for key in ("a", "b")
        ),
        7,
    )
    frozen = FrozenAuditSample(sample, "a" * 64, {"a": "shared", "b": "shared"})
    observations = tuple(
        AuditObservation(key, 1, "auditor", True, True, True) for key in ("a", "b")
    )
    if kind == "partial":
        observations = observations[:1]
    elif kind == "adverse":
        observations = (replace(observations[0], labels_correct=False), observations[1])
    elif kind == "truncated":
        observations = (replace(observations[0], output_truncated=True), observations[1])
    verdict = assess_audit(sample, observations)
    row = {
        "actor_id": "auditor",
        "record": {
            "observations": [asdict(item) for item in observations],
            "sample": {
                "sample_key": "sample",
                "population_sha256": frozen.population_sha256,
                "group_ids": frozen.group_ids,
                "verdict": asdict(verdict),
            },
        },
        "vetoed_revisions": {key: 1 for key in verdict.blocking_asset_ids},
    }
    # Round-trip to match PostgreSQL JSON arrays, not Python tuples.
    row = json.loads(json.dumps(row))
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = "release-uuid"
    connection.execute.return_value.mappings.return_value.one.return_value = row
    monkeypatch.setattr(
        annotation_audit_store, "lock_project", MagicMock(return_value="project-uuid")
    )
    loader = MagicMock(return_value=frozen)
    monkeypatch.setattr(annotation_audit_store, "load_audit_sample", loader)
    return connection, loader, row, frozen, observations, verdict


def load(connection, **changes):
    return load_annotation_audit(
        connection, project_key="project", release_key="release", audit_key="audit", **changes
    )


@pytest.mark.parametrize("kind", ["clean", "partial", "adverse", "truncated"])
def test_loader_recomputes_verdict_and_rechecks_current_groups(monkeypatch, kind):
    connection, loader, _, frozen, observations, verdict = stored_result(monkeypatch, kind)
    recovered = load(connection)
    assert recovered.frozen == frozen
    assert recovered.observations == observations
    assert recovered.verdict == verdict
    assert recovered.verdict.passed == (kind == "clean")
    loader.assert_called_once_with(
        connection,
        project_id="project-uuid",
        release_id="release-uuid",
        sample_key="sample",
        require_current_groups=True,
    )
    statements = [call.args[0].compile().params for call in connection.execute.call_args_list]
    assert statements == [
        {"project_id_1": "project-uuid", "release_key_1": "release"},
        {"project_id_1": "project-uuid", "release_id_1": "release-uuid", "audit_key_1": "audit"},
    ]


@pytest.mark.parametrize(
    "tamper",
    [
        "unsampled",
        "fingerprint",
        "groups",
        "verdict",
        "verdict-type",
        "actor",
        "duplicate",
        "foreign-member",
        "stale",
        "dependent",
        "flag-type",
        "count-type",
        "missing-field",
        "extra-field",
        "empty",
        "veto",
        "veto-type",
        "binding-field",
    ],
)
def test_loader_rejects_substituted_or_malformed_evidence(monkeypatch, tamper):
    connection, _, row, _, _, _ = stored_result(monkeypatch)
    record = row["record"]
    first = record["observations"][0]
    binding = record["sample"]
    if tamper == "unsampled":
        del record["sample"]
    elif tamper == "fingerprint":
        binding["population_sha256"] = "b" * 64
    elif tamper == "groups":
        binding["group_ids"] = {"a": "invented", "b": "independent"}
    elif tamper == "verdict":
        binding["verdict"]["pending_asset_ids"] = ["a"]
    elif tamper == "verdict-type":
        binding["verdict"]["passed"] = 1
    elif tamper == "actor":
        row["actor_id"] = "different-auditor"
    elif tamper == "duplicate":
        record["observations"].append(first)
    elif tamper == "foreign-member":
        first["asset_id"] = "foreign"
    elif tamper == "stale":
        first["revision"] = 2
    elif tamper == "dependent":
        row["actor_id"] = "original"
        first["reviewer_id"] = "original"
    elif tamper == "flag-type":
        first["labels_correct"] = "true"
    elif tamper == "count-type":
        first["revision"] = True
    elif tamper == "missing-field":
        del first["output_truncated"]
    elif tamper == "extra-field":
        first["truth_basis"] = "adjudicated"
    elif tamper == "empty":
        record["observations"] = []
    elif tamper == "veto":
        row["vetoed_revisions"] = {"a": 1}
    elif tamper == "veto-type":
        row["vetoed_revisions"] = {"a": True}
    else:
        binding["extra"] = "unsupported"
    with pytest.raises(ValueError):
        load(connection)


def test_loader_propagates_current_group_conflict_but_allows_historical_inspection(monkeypatch):
    connection, loader, _, _, _, _ = stored_result(monkeypatch, "adverse")
    frozen = loader.return_value
    loader.side_effect = ValueError("current source relationships cross pinned splits")
    with pytest.raises(ValueError, match="current source relationships"):
        load(connection)
    loader.side_effect = None
    assert load(connection, require_current_groups=False).frozen == frozen
    assert loader.call_args.kwargs["require_current_groups"] is False


@pytest.mark.parametrize("missing", ["release", "audit", "sample"])
def test_missing_or_foreign_scoped_rows_fail_closed(monkeypatch, missing):
    connection, loader, _, _, _, _ = stored_result(monkeypatch)
    if missing == "release":
        connection.execute.return_value.scalar_one.side_effect = sa.exc.NoResultFound()
    elif missing == "audit":
        connection.execute.return_value.mappings.return_value.one.side_effect = (
            sa.exc.NoResultFound()
        )
    else:
        loader.side_effect = sa.exc.NoResultFound()
    with pytest.raises(sa.exc.NoResultFound):
        load(connection)
    if missing != "sample":
        loader.assert_not_called()
