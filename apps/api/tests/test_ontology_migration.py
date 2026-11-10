"""Versioned ontology migration: edition validation and review queue."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from labelloop_api.ontology_migration import (
    ClassMigration,
    annotation_objects_needing_review,
    export_class_index_map,
    publish_ontology_version,
    validate_catalog_migration,
)
from labelloop_core.domain import ClassDefinition, OntologyRelease


def _classes(*ids: str, names=None) -> tuple[ClassDefinition, ...]:
    return tuple(
        ClassDefinition(identifier, (names or {}).get(identifier, identifier), "def")
        for identifier in ids
    )


def _ontology(version, ids, names=None):
    classes = _classes(*ids, names=names)
    return OntologyRelease(
        str(version),
        "project",
        version,
        classes,
        {item.stable_id: i for i, item in enumerate(classes)},
    )


def test_rename_preserves_stable_identity():
    old = _ontology(1, ["cat", "dog"])
    new = _ontology(2, ["cat", "dog"], names={"dog": "hound"})
    steps = (
        ClassMigration(("cat",), ("cat",), "retain"),
        ClassMigration(("dog",), ("dog",), "rename"),
    )
    validate_catalog_migration(old, new, steps)
    mapping = export_class_index_map(old, new)
    assert mapping["dog"] == {"old_index": 1, "new_index": 1}
    records = [{"asset_id": "a", "revision": 1, "object_id": "o", "class_id": "dog"}]
    assert annotation_objects_needing_review(records, steps) == []


def test_add_remove_merge_split_are_explicit():
    old = _ontology(1, ["a", "b", "c"])
    new = _ontology(2, ["ab", "d", "e"])
    steps = (
        ClassMigration(("a", "b"), ("ab",), "merge"),
        ClassMigration(("c",), ("d", "e"), "split"),
    )
    validate_catalog_migration(old, new, steps)
    # Every old object under merge/split needs explicit review, not a relabel.
    records = [
        {"asset_id": "a", "revision": 3, "object_id": "o1", "class_id": "a"},
        {"asset_id": "a", "revision": 3, "object_id": "o2", "class_id": "c"},
    ]
    items = annotation_objects_needing_review(records, steps)
    assert {item.class_id for item in items} == {"a", "c"}
    # Removed classes also need review.
    old2, new2 = _ontology(1, ["x", "y"]), _ontology(2, ["x"])
    remove_steps = (
        ClassMigration(("x",), ("x",), "retain"),
        ClassMigration(("y",), (), "remove"),
    )
    validate_catalog_migration(old2, new2, remove_steps)
    items = annotation_objects_needing_review(
        [{"asset_id": "a", "revision": 1, "object_id": "o", "class_id": "y"}], remove_steps
    )
    assert len(items) == 1 and "remove" in items[0].reason


@pytest.mark.parametrize(
    ("old_ids,new_ids,steps,match"),
    [
        (["a"], ["a"], (), "explicit"),
        (["a", "b"], ["a"], (ClassMigration(("a",), ("a",), "retain"),), "exactly one"),
        (
            ["a"],
            ["a"],
            (ClassMigration(("a",), ("a",), "retain"), ClassMigration(("a",), ("a",), "retain")),
            "exactly one",
        ),
        # 1->1 with different IDs is an inferred relabel and must be rejected.
        (["a"], ["b"], (ClassMigration(("a",), ("b",), "retain"),), "one stable ID to itself"),
    ],
)
def test_missing_and_ambiguous_mappings_are_rejected(old_ids, new_ids, steps, match):
    old, new = _ontology(1, old_ids), _ontology(2, new_ids)
    with pytest.raises(ValueError, match=match):
        validate_catalog_migration(old, new, steps)


def _migration_connection(ontology_id=b"oid", *, stale=False, conflict=False):
    connection = MagicMock()
    results = []

    def execute(statement, parameters=None):
        result = MagicMock()
        sql = str(statement)
        results.append((sql, parameters))
        if "ontology_release" in sql and "INSERT" in sql:
            if conflict:
                raise IntegrityError("duplicate", None, Exception("duplicate"))
            return result
        if "SELECT" in sql:
            result.mappings.return_value.one.return_value = {
                "id": ontology_id,
                "version": 99 if stale else 1,
                "classes": [{"stable_id": "cat", "name": "cat", "definition": "def"}],
                "class_map": {"cat": 0},
            }
            result.scalar_one.return_value = ontology_id
        return result

    connection.execute.side_effect = execute
    return connection, results


def test_publish_appends_new_version_and_keeps_history():
    connection, results = _migration_connection()
    candidate = publish_ontology_version(
        connection,
        project_id="project",
        new_version=2,
        new_classes=_classes("cat"),
        new_class_map={"cat": 0},
        steps=(ClassMigration(("cat",), ("cat",), "retain"),),
        expected_current_version=1,
    )
    assert candidate.version == 2
    inserts = [sql for sql, _ in results if "INSERT INTO ontology_release" in sql]
    updates = [sql for sql, _ in results if "UPDATE" in sql or "DELETE" in sql]
    assert len(inserts) == 1
    assert not updates


def test_stale_version_rolls_back_without_insert():
    connection, results = _migration_connection(stale=True)
    with pytest.raises(ValueError, match="stale"):
        publish_ontology_version(
            connection,
            project_id="project",
            new_version=2,
            new_classes=_classes("cat"),
            new_class_map={"cat": 0},
            steps=(ClassMigration(("cat",), ("cat",), "retain"),),
            expected_current_version=1,
        )
    assert not any("INSERT" in sql for sql, _ in results)


def test_concurrent_duplicate_version_rolls_back_as_value_error():
    connection, results = _migration_connection(conflict=True)
    with pytest.raises(ValueError, match="already exists"):
        publish_ontology_version(
            connection,
            project_id="project",
            new_version=2,
            new_classes=_classes("cat"),
            new_class_map={"cat": 0},
            steps=(ClassMigration(("cat",), ("cat",), "retain"),),
            expected_current_version=1,
        )
    assert any("INSERT INTO ontology_release" in sql for sql, _ in results)
