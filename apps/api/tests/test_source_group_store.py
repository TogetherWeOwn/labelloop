"""Current canonical grouping checks; relation write fencing runs in Actions."""

from unittest.mock import MagicMock

import pytest

from labelloop_api.source_group_store import checked_group_membership, current_release_groups


def test_unselected_intermediate_and_pinned_group_edges_are_transitive():
    assets = {"a": "hash-a", "bridge": "hash-b", "c": "hash-c", "unused": "hash-d"}
    assignments = {key: ("shared", "train") for key in ("a", "bridge", "c")}
    assert checked_group_membership(
        assets, assignments, (("a", "bridge"), ("bridge", "c"), ("c", "a")), frozenset({"a"})
    ) == {"a": "shared"}
    assignments["c"] = ("separate", "train")
    with pytest.raises(ValueError, match="merge pinned groups"):
        checked_group_membership(assets, assignments, (("bridge", "c"),), frozenset({"a"}))


@pytest.mark.parametrize(
    "split, message", [("train", "merge pinned groups"), ("test", "cross pinned splits")]
)
@pytest.mark.parametrize("kind", ["relation", "exact-duplicate", "pinned-group"])
def test_new_evidence_cannot_inflate_independence_or_hide_split_leakage(kind, split, message):
    assets = {"a": "hash-a", "b": "hash-b"}
    assignments = {"a": ("first", "train"), "b": ("second", split)}
    relations = (("a", "b"),) if kind == "relation" else ()
    if kind == "exact-duplicate":
        assets["b"] = assets["a"]
    elif kind == "pinned-group":
        assignments["b"] = ("first", split)
        if split == "train":
            assert checked_group_membership(assets, assignments, relations, frozenset({"a"})) == {
                "a": "first"
            }
            return
    with pytest.raises(ValueError, match=message):
        checked_group_membership(assets, assignments, relations, frozenset({"a"}))


@pytest.mark.parametrize("kind", ["missing-asset", "missing-assignment", "empty"])
def test_missing_required_membership_fails_closed(kind):
    required = frozenset() if kind == "empty" else frozenset({"a"})
    with pytest.raises(ValueError, match="complete pinned"):
        checked_group_membership(
            {} if kind == "missing-asset" else {"a": "hash"},
            {} if kind == "missing-assignment" else {"a": ("group", "train")},
            (),
            required,
        )


@pytest.mark.parametrize("foreign", [False, True])
def test_reachable_foreign_or_unassigned_source_is_not_silently_ignored(foreign):
    assets = {"a": "hash-a"} if foreign else {"a": "hash-a", "b": "hash-b"}
    with pytest.raises(ValueError, match="foreign or unassigned"):
        checked_group_membership(assets, {"a": ("group", "train")}, (("a", "b"),), frozenset({"a"}))


def test_unrelated_new_sources_and_relations_do_not_hold_untouched_groups():
    assert checked_group_membership(
        {"a": "hash-a", "b": "hash-b"},
        {"a": ("group", "train")},
        (("b", "foreign"),),
        frozenset({"a"}),
    ) == {"a": "group"}


def test_database_loader_fences_writers_and_scopes_pinned_version():
    connection = MagicMock()
    assets, assignments, relations = MagicMock(), MagicMock(), MagicMock()
    assets.all.return_value = [("a", "hash-a")]
    row = MagicMock(asset_key="a", group_id="group", split="train")
    assignments.__iter__.return_value = iter([row])
    relations.tuples.return_value = []
    connection.execute.side_effect = [MagicMock(), assets, assignments, relations]
    assert current_release_groups(
        connection, project_id="project", assignment_version="pinned", asset_keys=frozenset({"a"})
    ) == {"a": "group"}
    statements = [call.args[0] for call in connection.execute.call_args_list]
    assert (
        str(statements[0])
        == "LOCK TABLE source_asset, source_split_assignment, similarity_relation IN SHARE MODE"
    )
    assert statements[2].compile().params == {
        "project_id_1": "project",
        "assignment_version_1": "pinned",
    }
    assert set(statements[3].compile().params.values()) == {"project"}
