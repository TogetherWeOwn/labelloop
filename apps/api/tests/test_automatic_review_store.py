"""Original pipeline snapshots: adapter controls, not pipeline execution or truth."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from qualification_fixture import qualification_context, synthetic_final
from test_m5_integration_contract import make_release, make_report

from labelloop_api import automatic_review_store as store
from labelloop_core.qualification_data import ScopeCell, record_sha256
from labelloop_core.registry import PromotionPolicy


def fixture(side=320, positive=False):
    release = make_release(side, "challenger")
    report = synthetic_final(make_report(release), run_id="final", dataset_id="final-set")
    release = replace(release, evaluation_run_ids=(*release.evaluation_run_ids, report.run_id))
    context = qualification_context(release, report, PromotionPolicy(1, 0.9))
    values = dict(
        revision=2,
        parent_revision=1,
        actor_kind="system",
        actor_id="service-principal",
        objects=[dict(object_id="one", class_id="component", box=[0, 0, 5, 5])] if positive else [],
        approval_state="auto_accepted",
        review_tier="machine_verified_no_human_review",
        scope_completeness="exhaustive_for_scope",
        training_eligible=False,
        gold_eligible=False,
    )
    arguments = dict(project_key=release.project_id, asset_key="asset", revision=2)
    record = store.automatic_acceptance_record(
        project_key=release.project_id,
        asset_key="asset",
        revision=values,
        evaluation_key=report.run_id,
        scope=context.evidence.scope,
        qualification=context,
    )
    row = values | dict(
        event_actor_kind="system",
        event_actor_id="service-principal",
        correction=dict(new_revision=2, automatic_acceptance=record),
    )
    return arguments, context, row


def read(monkeypatch, arguments, rows):
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.all.return_value = rows
    lock = MagicMock(return_value="project-uuid")
    monkeypatch.setattr(store, "lock_project", lock)
    result = store.load_automatic_acceptance(connection, **arguments)
    lock.assert_called_once_with(connection, arguments["project_key"])
    params = connection.execute.call_args.args[0].compile().params
    assert params == dict(
        expected_revision_1=1,
        decision_1="apply_revision",
        project_id_1="project-uuid",
        asset_key_1="asset",
        revision_1=2,
    )
    return result


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("positive", [False, True])
def test_original_exact_policy_roundtrip_including_negative_and_ineligible(
    monkeypatch, side, positive
):
    arguments, context, row = fixture(side, positive)
    original = deepcopy(row)
    saved = read(monkeypatch, arguments, [row])
    assert saved.policy.pipeline == context.current_pipeline
    assert saved.policy.targets == context.evidence.targets
    assert saved.policy.targets.fixture_only is True
    assert saved.scope == context.evidence.scope
    assert saved.qualification_id == context.evidence.qualification_id
    assert saved.release_sha256 == context.evidence.release_sha256
    assert saved.evaluation_key == "final"
    assert row == original
    # No current champion, policy, final journal or newer revision is consulted.


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_duplicate_original_event_cannot_qualify(monkeypatch, count):
    arguments, _, row = fixture()
    with pytest.raises(ValueError, match="one exact original"):
        read(monkeypatch, arguments, [row] * count)


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_kind", "human"),
        ("approval_state", "proposed"),
        ("parent_revision", 0),
        ("event_actor_kind", "model"),
        ("event_actor_id", "other-principal"),
        ("objects", [dict(object_id="substitution")]),
        ("actor_id", "substitution"),
        ("training_eligible", True),
        ("gold_eligible", True),
        ("review_tier", "adjudicated"),
        ("scope_completeness", "partial"),
    ],
)
def test_canonical_or_principal_substitution_fails_closed(monkeypatch, field, value):
    arguments, _, row = fixture()
    row[field] = value
    with pytest.raises(ValueError, match="canonical revision"):
        read(monkeypatch, arguments, [row])


@pytest.mark.parametrize("correction", [None, {}, {"new_revision": True}, {"new_revision": 3}])
def test_wrong_event_revision_fails_closed(monkeypatch, correction):
    arguments, _, row = fixture()
    row["correction"] = correction
    with pytest.raises(ValueError, match="canonical revision"):
        read(monkeypatch, arguments, [row])


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "future-schema"),
        ("project_key", "foreign-project"),
        ("asset_key", "other-asset"),
        ("revision_sha256", "0" * 64),
        ("qualification_id", ""),
        ("release_sha256", "not-a-digest"),
        ("evaluation_key", None),
        ("scope", []),
        ("scope", [{"class_id": "component", "source_id": "source", "condition": None}]),
        ("scope", [{"class_id": "unknown", "source_id": "source", "condition": "ordinary"}]),
        (
            "scope",
            [{"class_id": "component", "source_id": "source", "condition": "ordinary", "extra": 1}],
        ),
        ("policy", {"record_type": "QualificationContext"}),
    ],
)
def test_malformed_original_record_is_not_current_policy_evidence(monkeypatch, field, value):
    arguments, _, row = fixture()
    row["correction"]["automatic_acceptance"][field] = value
    with pytest.raises(ValueError):
        read(monkeypatch, arguments, [row])


def test_missing_legacy_or_extended_snapshot_fails_closed(monkeypatch):
    arguments, _, row = fixture()
    row["correction"].pop("automatic_acceptance")
    with pytest.raises(ValueError, match="missing or malformed"):
        read(monkeypatch, arguments, [row])
    _, _, row = fixture()
    row["correction"]["automatic_acceptance"]["extra"] = True
    with pytest.raises(ValueError, match="missing or malformed"):
        read(monkeypatch, arguments, [row])


@pytest.mark.parametrize("change", ["foreign-project", "pipeline", "scope"])
def test_writer_cannot_snapshot_a_different_checked_identity(change):
    arguments, context, row = fixture()
    project = arguments["project_key"]
    scope = context.evidence.scope
    if change == "foreign-project":
        project = "foreign-project"
    elif change == "pipeline":
        context = replace(
            context,
            current_pipeline=replace(context.current_pipeline, reviewer_model_version="new-model"),
        )
    else:
        scope = (ScopeCell("unknown", "source", "ordinary"),)
    with pytest.raises(ValueError, match="checked pipeline or scope"):
        store.automatic_acceptance_record(
            project_key=project,
            asset_key="asset",
            revision=row,
            evaluation_key="final",
            scope=scope,
            qualification=context,
        )


def test_scope_duplicates_and_policy_project_substitution_fail(monkeypatch):
    arguments, _, row = fixture()
    record = row["correction"]["automatic_acceptance"]
    record["scope"] *= 2
    with pytest.raises(ValueError, match="unique"):
        read(monkeypatch, arguments, [row])
    arguments, _, row = fixture()
    row["correction"]["automatic_acceptance"]["policy"]["payload"]["pipeline"]["project_id"] = (
        "foreign"
    )
    with pytest.raises(ValueError, match="original project or scope"):
        read(monkeypatch, arguments, [row])


@pytest.mark.parametrize("revision", [True, 1, 0, -1, "2"])
def test_invalid_revision_rejected_before_database_access(revision):
    connection = MagicMock()
    with pytest.raises(ValueError, match="bounded integers"):
        store.load_automatic_acceptance(
            connection, project_key="project", asset_key="asset", revision=revision
        )
    connection.execute.assert_not_called()


def test_snapshot_failure_precedes_review_and_revision_appends(monkeypatch):
    from test_repository import review_connection

    from labelloop_api import revision_store

    _, context, _ = fixture()
    scope = (ScopeCell("item", "source", "ordinary"),)
    connection = review_connection(
        actor_kind="model", approval_state="proposed", review_tier="unreviewed"
    )
    monkeypatch.setattr(revision_store, "source_uuid", lambda *_: "asset-uuid")
    monkeypatch.setattr(revision_store, "lock_project", MagicMock())
    monkeypatch.setattr(revision_store, "require_automatic_acceptance", MagicMock())
    # A guard implementation returning incorrectly must not produce a false snapshot.
    with pytest.raises(ValueError, match="checked pipeline or scope"):
        revision_store.append_review(
            connection,
            asset_id="asset",
            actor_kind="system",
            actor_id="service-principal",
            expected_revision=1,
            approval_state="auto_accepted",
            review_tier="machine_verified_no_human_review",
            scope_completeness="exhaustive_for_scope",
            objects=[],
            automatic=revision_store.AutomaticReview("recipe", "final", scope, context),
        )
    assert not any(
        getattr(call.args[0], "is_insert", False) for call in connection.execute.call_args_list
    )


def test_revision_fingerprint_covers_all_canonical_decision_fields():
    _, _, row = fixture()
    record = row["correction"]["automatic_acceptance"]
    assert record["revision_sha256"] == record_sha256(
        {key: row[key] for key in store._REVISION_FIELDS}
    )
