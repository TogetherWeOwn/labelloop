"""Read projection controls; fake SELECT results do not prove database integration."""

from collections import deque
from types import SimpleNamespace
from uuid import uuid4

from labelloop_api.management_project_reads import source_groups


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def scalars(self):
        return self.rows


class Snapshot:
    def __init__(self, *results):
        self.results = deque(results)
        self.queries = []

    def execute(self, query):
        assert query.is_select and "FOR UPDATE" not in str(query)
        self.queries.append(query)
        return Rows(self.results.popleft())


def snapshot(*, same_hash=False, same_declaration=False, same_group=False, relation=False):
    left, right = uuid4(), uuid4()
    assets = [
        SimpleNamespace(id=left, asset_key="left", sha256="1" * 64),
        SimpleNamespace(id=right, asset_key="right", sha256=("1" if same_hash else "2") * 64),
    ]
    assignments = [
        SimpleNamespace(
            asset_id=asset.id,
            assignment_version="assignment-1",
            group_id="group-1" if same_group or index == 0 else "group-2",
            split="val",
            locked=True,
            seed=7,
            algorithm="fixture",
            thresholds={},
        )
        for index, asset in enumerate(assets)
    ]
    events = [
        {"result": {"id": asset.asset_key}, "evidence": {"source_group": group}}
        for asset, group in zip(
            assets, ["capture-a", "capture-a" if same_declaration else "capture-b"], strict=True
        )
    ]
    relations = [SimpleNamespace(left_asset_id=left, right_asset_id=right)] if relation else []
    return Snapshot(assets, [], assignments, events, relations)


def test_source_group_read_withholds_inconsistent_current_assignment_pins():
    for options in ({"same_hash": True}, {"same_declaration": True}, {"relation": True}):
        connection = snapshot(**options)
        detail = source_groups(connection, uuid4())
        assert detail.images == 2
        assert detail.groups is detail.split_counts is detail.assignment_version is None
        assert detail.pending_pairs == []


def test_source_group_read_preserves_valid_groups_and_pending_suggestions():
    connection = snapshot(same_hash=True, same_declaration=True, same_group=True, relation=True)
    assets = connection.results[0]
    pair_id = uuid4()
    connection.results[1] = [
        SimpleNamespace(id=pair_id, left_asset_id=assets[0].id, right_asset_id=assets[1].id)
    ]
    detail = source_groups(connection, uuid4())
    assert detail.images == 2 and detail.groups == 1
    assert detail.assignment_version == "assignment-1"
    assert detail.split_counts == {"train": 0, "val": 2, "test": 0}
    assert detail.pending_pairs[0].model_dump() == {
        "id": str(pair_id),
        "left_asset_id": "left",
        "right_asset_id": "right",
    }
    assert not connection.results


def test_source_group_read_does_not_hide_query_failures_as_missing_assignment():
    class Unavailable:
        def execute(self, query):
            raise RuntimeError("fixture unavailable")

    try:
        source_groups(Unavailable(), uuid4())
    except RuntimeError as failure:
        assert str(failure) == "fixture unavailable"
    else:
        raise AssertionError("query failure must not become an empty successful projection")
