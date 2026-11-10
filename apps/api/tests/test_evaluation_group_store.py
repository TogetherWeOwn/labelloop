"""Canonical group adapter controls; mocked reads do not prove independent truth."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import NoResultFound

from labelloop_api import evaluation_group_store as store
from labelloop_core.evaluation_lifecycle import EvaluationSet


def setup(monkeypatch):
    connection = MagicMock()
    release = dict(
        sample_revisions={"positive": 2, "negative": 1},
        excluded_asset_ids=[],
        assignment_version="pinned-version",
    )
    connection.execute.return_value.mappings.return_value.one.return_value = release
    connection.execute.return_value.all.return_value = [("positive", 2), ("negative", 1)]
    lock = MagicMock(return_value="project-id")
    groups = MagicMock(return_value={"positive": "shared", "negative": "shared"})
    uses = MagicMock()
    monkeypatch.setattr(store, "lock_project", lock)
    monkeypatch.setattr(store, "current_release_groups", groups)
    monkeypatch.setattr(store, "require_development_uses", uses)
    args = dict(project_key="project", dataset_key="evaluation", role="development")
    return connection, args, release, lock, groups, uses


@pytest.mark.parametrize("role", ["development", "calibration", "locked_final_test"])
def test_complete_pinned_membership_supplies_groups_not_caller_claims(monkeypatch, role):
    connection, args, _, lock, groups, uses = setup(monkeypatch)
    assert store.load_evaluation_set(connection, **(args | {"role": role})) == EvaluationSet(
        "evaluation", "project", ("shared",), role
    )
    lock.assert_called_once_with(connection, "project")
    statements = [call.args[0] for call in connection.execute.call_args_list]
    assert statements[0].compile().params == dict(
        project_id_1="project-id", release_key_1="evaluation"
    )
    assert "frozen" in str(statements[0])
    assert statements[1].compile().params == dict(
        project_id_1="project-id", param_1=[("positive", 2), ("negative", 1)]
    )
    groups.assert_called_once_with(
        connection,
        project_id="project-id",
        assignment_version="pinned-version",
        asset_keys=frozenset({"positive", "negative"}),
    )
    if role == "locked_final_test":
        uses.assert_not_called()
    else:
        expected = dict(project_key="project", development_set_key="evaluation")
        if role == "calibration":
            expected["calibration_set_key"] = "evaluation"
        uses.assert_called_once_with(connection, **expected)
    assert all(statement.is_select for statement in statements)


def test_distinct_groups_are_sorted_without_inflating_correlated_support(monkeypatch):
    connection, args, _, _, groups, _ = setup(monkeypatch)
    groups.return_value = {"positive": "z", "negative": "a"}
    assert store.load_evaluation_set(connection, **args).group_ids == ("a", "z")


@pytest.mark.parametrize(
    "mutation",
    [
        {"project_key": " "},
        {"dataset_key": ""},
        {"role": "training"},
        {"role": "rotating_audit"},
        {"role": "invented"},
    ],
)
def test_invalid_selectors_fail_before_database_access(monkeypatch, mutation):
    connection, args, _, lock, _, _ = setup(monkeypatch)
    with pytest.raises(ValueError):
        store.load_evaluation_set(connection, **(args | mutation))
    lock.assert_not_called()
    connection.execute.assert_not_called()


@pytest.mark.parametrize(
    "membership,excluded",
    [({}, []), ({"positive": True}, []), ({"positive": 0}, []), ({"positive": 2}, ["positive"])],
)
def test_invalid_or_excluded_membership_cannot_shrink_the_holdout(
    monkeypatch, membership, excluded
):
    connection, args, release, _, groups, _ = setup(monkeypatch)
    release.update(sample_revisions=membership, excluded_asset_ids=excluded)
    with pytest.raises(ValueError):
        store.load_evaluation_set(connection, **args)
    groups.assert_not_called()


@pytest.mark.parametrize(
    "rows",
    [
        [("positive", 2)],
        [("positive", 3), ("negative", 1)],
        [("positive", 2), ("foreign", 1)],
        [("positive", 2), ("positive", 2)],
    ],
)
def test_missing_or_substituted_exact_revisions_fail_before_group_lookup(monkeypatch, rows):
    connection, args, _, _, groups, _ = setup(monkeypatch)
    connection.execute.return_value.all.return_value = rows
    with pytest.raises(ValueError, match="missing canonical source revisions"):
        store.load_evaluation_set(connection, **args)
    groups.assert_not_called()


def test_foreign_unfrozen_or_missing_release_is_not_a_partial_result(monkeypatch):
    connection, args, _, _, groups, _ = setup(monkeypatch)
    connection.execute.return_value.mappings.return_value.one.side_effect = NoResultFound
    with pytest.raises(NoResultFound):
        store.load_evaluation_set(connection, **args)
    groups.assert_not_called()


@pytest.mark.parametrize("failure", ["conflict", "missing", "foreign", "empty"])
def test_current_group_conflicts_and_incomplete_membership_fail_closed(monkeypatch, failure):
    connection, args, _, _, groups, uses = setup(monkeypatch)
    if failure == "conflict":
        groups.side_effect = ValueError("new transitive relationship")
    else:
        groups.return_value = {
            "missing": {"positive": "group"},
            "foreign": {"positive": "group", "foreign": "group"},
            "empty": {"positive": "", "negative": "group"},
        }[failure]
    with pytest.raises(ValueError):
        store.load_evaluation_set(connection, **args)
    if failure != "empty":
        uses.assert_not_called()


@pytest.mark.parametrize("role", ["development", "calibration"])
def test_final_reservation_is_not_overridden_by_a_requested_role(monkeypatch, role):
    connection, args, _, _, _, uses = setup(monkeypatch)
    uses.side_effect = ValueError("evaluation role forbids this development use")
    with pytest.raises(ValueError, match="role forbids"):
        store.load_evaluation_set(connection, **(args | {"role": role}))
