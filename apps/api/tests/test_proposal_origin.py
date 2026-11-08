"""Stored producer binding and atomic annotation influence, not HTTP acceptance."""

import copy
import hashlib
from unittest.mock import MagicMock

import pytest
from test_m5_integration_contract import make_release
from test_repository import review_connection

from labelloop_api import generated_label_store as store
from labelloop_api import model_store, revision_store
from labelloop_core.json_records import canonical_json


def fixture(monkeypatch):
    release = make_release(320, "challenger")
    teacher = model_store.json_record(release)
    binding = {
        "run_id": "proposal",
        "source": {"project_id": release.project_id, "asset_id": "asset"},
        "expected_revision": 1,
    }
    record = dict(
        run_id="proposal",
        project_id=release.project_id,
        binding=binding,
        teacher=teacher,
        checkpoint_sha256=release.weights_sha256,
    )
    row = dict(
        record=record,
        sha256=hashlib.sha256(canonical_json(record)).hexdigest(),
        input=dict(
            run_id="proposal",
            request=copy.deepcopy(binding),
            registry={"champion": copy.deepcopy(teacher)},
        ),
        payload=copy.deepcopy(record),
    )
    row["stage_sha256"] = row["sha256"]
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.one.return_value = row
    connection.execute.return_value.scalar_one.return_value = "model-id"
    monkeypatch.setattr(store, "lock_project", MagicMock(return_value="project-id"))
    monkeypatch.setattr(model_store, "load_release", MagicMock(return_value=release))
    args = dict(
        project_key=release.project_id,
        asset_key="asset",
        expected_revision=1,
        proposal_run_key="proposal",
    )
    return connection, args, row, release


def test_teacher_comes_from_successful_same_project_fenced_job_not_current_champion(monkeypatch):
    connection, args, _, release = fixture(monkeypatch)
    assert store.proposal_teacher(connection, **args) == release.release_id
    statement = str(connection.execute.call_args_list[0].args[0])
    for fence in (
        "j.status = 'succeeded'",
        "a.generation = j.generation",
        "j.kind = 'proposals'",
        "FOR SHARE OF j",
    ):
        assert fence in statement
    assert connection.execute.call_args_list[1].args[0].compile().params == {
        "project_id_1": "project-id",
        "release_key_1": release.release_id,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "digest",
        "stage-digest",
        "stage-payload",
        "run",
        "project",
        "asset",
        "revision",
        "boolean-revision",
        "pinned-request",
        "pinned-teacher",
        "no-teacher",
        "checkpoint",
        "registered-release",
    ],
)
def test_substituted_proposal_or_registered_teacher_fails_closed(monkeypatch, mutation):
    connection, args, row, _ = fixture(monkeypatch)
    record = row["record"]
    if mutation == "digest":
        row["sha256"] = "a" * 64
    elif mutation == "stage-digest":
        row["stage_sha256"] = "a" * 64
    elif mutation == "stage-payload":
        row["payload"] = {}
    elif mutation == "pinned-request":
        row["input"]["request"]["expected_revision"] = 2
    elif mutation == "pinned-teacher":
        row["input"]["registry"]["champion"]["export_sha256"] = "a" * 64
    elif mutation == "registered-release":
        monkeypatch.setattr(model_store, "load_release", lambda *_: make_release(320, "incumbent"))
    else:
        if mutation == "run":
            record["run_id"] = "foreign"
        elif mutation == "project":
            record["project_id"] = "foreign"
        elif mutation == "asset":
            record["binding"]["source"]["asset_id"] = "foreign"
        elif mutation in {"revision", "boolean-revision"}:
            record["binding"]["expected_revision"] = True if mutation == "boolean-revision" else 2
        elif mutation == "no-teacher":
            record["teacher"] = None
        else:
            record["checkpoint_sha256"] = "a" * 64
        row["sha256"] = row["stage_sha256"] = hashlib.sha256(canonical_json(record)).hexdigest()
        row["payload"] = copy.deepcopy(record)
    with pytest.raises(ValueError):
        store.proposal_teacher(connection, **args)
    assert not any(
        getattr(call.args[0], "is_insert", False) for call in connection.execute.call_args_list
    )


@pytest.mark.parametrize("positive", [False, True])
@pytest.mark.parametrize("failure", [None, "lookup", "origin"])
def test_review_commits_proposal_influence_with_exact_revision_or_rolls_back(
    monkeypatch, positive, failure
):
    monkeypatch.setattr(revision_store, "source_uuid", lambda *_: "uuid")
    monkeypatch.setattr(revision_store, "lock_project", MagicMock())
    teacher = MagicMock(return_value="pinned-teacher")
    origin = MagicMock()
    if failure == "lookup":
        teacher.side_effect = ValueError("proposal lookup failure")
    if failure == "origin":
        origin.side_effect = ValueError("origin append failure")
    monkeypatch.setattr(revision_store, "proposal_teacher", teacher)
    monkeypatch.setattr(revision_store, "record_generated_label_origin", origin)
    objects = [{"object_id": "one", "class_id": "item", "box": [0, 0, 5, 5]}] if positive else []
    connection = review_connection(
        objects, actor_kind="model", approval_state="proposed", review_tier="unreviewed"
    )
    args = dict(
        asset_id="asset",
        actor_kind="model",
        actor_id="worker",
        expected_revision=1,
        approval_state="proposed",
        review_tier="unreviewed",
        scope_completeness="unreviewed",
        objects=objects,
        proposal_run_key="proposal",
    )
    if failure:
        with pytest.raises(ValueError, match="failure"):
            revision_store.append_review(connection, **args)
        assert connection.begin_nested.return_value.__exit__.call_args.args[0] is ValueError
    else:
        assert revision_store.append_review(connection, **args) == 2
        origin.assert_called_once_with(
            connection,
            project_key="project-key",
            asset_key="asset",
            revision=2,
            teacher_model_key="pinned-teacher",
            actor_id="worker",
        )
    teacher.assert_called_once_with(
        connection,
        project_key="project-key",
        asset_key="asset",
        expected_revision=1,
        proposal_run_key="proposal",
    )
    if failure == "lookup":
        origin.assert_not_called()
        assert not any(
            getattr(call.args[0], "is_insert", False) for call in connection.execute.call_args_list
        )


@pytest.mark.parametrize("positive", [False, True])
@pytest.mark.parametrize("same_teacher", [False, True])
@pytest.mark.parametrize("failure", [None, "lookup", "origin"])
def test_multiple_proposals_resolve_all_runs_and_atomically_retain_distinct_teachers(
    monkeypatch, positive, same_teacher, failure
):
    monkeypatch.setattr(revision_store, "source_uuid", lambda *_: "uuid")
    events = []
    monkeypatch.setattr(revision_store, "lock_project", lambda *_: events.append("project"))

    def resolve(_connection, **kwargs):
        key = kwargs["proposal_run_key"]
        events.append(key)
        assert kwargs == dict(
            project_key="project-key", asset_key="asset", expected_revision=1, proposal_run_key=key
        )
        if failure == "lookup" and key == "proposal-b":
            raise ValueError("injected lookup failure")
        return "teacher-a" if same_teacher else key.replace("proposal", "teacher")

    teacher = MagicMock(side_effect=resolve)
    origin = MagicMock()
    if failure == "origin":
        origin.side_effect = (
            ValueError("injected origin failure")
            if same_teacher
            else [None, ValueError("injected origin failure")]
        )
    monkeypatch.setattr(revision_store, "proposal_teacher", teacher)
    monkeypatch.setattr(revision_store, "record_generated_label_origin", origin)
    objects = [{"object_id": "one", "class_id": "item", "box": [0, 0, 5, 5]}] if positive else []
    connection = review_connection(
        objects, actor_kind="model", approval_state="proposed", review_tier="unreviewed"
    )
    execute = connection.execute.side_effect

    def observed(statement, parameters=None):
        if "SELECT id FROM source_asset" in str(statement):
            events.append("asset")
        return execute(statement, parameters)

    connection.execute.side_effect = observed
    args = dict(
        asset_id="asset",
        actor_kind="model",
        actor_id="worker",
        expected_revision=1,
        approval_state="proposed",
        review_tier="unreviewed",
        scope_completeness="unreviewed",
        objects=objects,
        proposal_run_keys=("proposal-b", "proposal-a"),
    )
    if failure:
        with pytest.raises(ValueError, match="injected"):
            revision_store.append_review(connection, **args)
        assert connection.begin_nested.return_value.__exit__.call_args.args[0] is ValueError
    else:
        assert revision_store.append_review(connection, **args) == 2
    assert teacher.call_count == 2
    assert events == ["project", "proposal-a", "proposal-b"] + (
        [] if failure == "lookup" else ["asset"]
    )
    if failure == "lookup":
        origin.assert_not_called()
        assert not any(
            getattr(call.args[0], "is_insert", False) for call in connection.execute.call_args_list
        )
    else:
        expected = ["teacher-a"] if same_teacher else ["teacher-a", "teacher-b"]
        assert [call.kwargs["teacher_model_key"] for call in origin.call_args_list] == expected
        for call in origin.call_args_list:
            assert call.args == (connection,)
            assert call.kwargs == dict(
                project_key="project-key",
                asset_key="asset",
                revision=2,
                teacher_model_key=call.kwargs["teacher_model_key"],
                actor_id="worker",
            )


@pytest.mark.parametrize(
    "changes",
    [
        {"proposal_run_keys": "proposal"},
        {"proposal_run_keys": ["proposal"]},
        {"proposal_run_keys": ("proposal", "proposal")},
        {"proposal_run_keys": ("proposal", " ")},
        {"proposal_run_keys": ("proposal", None)},
        {"proposal_run_key": "single", "proposal_run_keys": ("proposal",)},
    ],
)
def test_invalid_proposal_collection_is_rejected_before_database_access(changes):
    connection = MagicMock()
    with pytest.raises(ValueError):
        revision_store.append_review(
            connection,
            asset_id="asset",
            actor_kind="model",
            actor_id="worker",
            expected_revision=1,
            approval_state="proposed",
            review_tier="unreviewed",
            scope_completeness="unreviewed",
            objects=[],
            **changes,
        )
    connection.execute.assert_not_called()
    connection.begin_nested.assert_not_called()
