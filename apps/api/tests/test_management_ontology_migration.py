"""Pure migration-preparation controls; database publication runs in Actions."""

import unittest
from types import SimpleNamespace
from uuid import uuid4

from labelloop_api.management_contracts import ClassMigrationStepInput
from labelloop_api.management_ontology_migration import (
    migration_class_map,
    migration_steps,
    prepare_migration,
)
from labelloop_api.management_ports import ManagementFailure


def step(kind, old, new):
    return ClassMigrationStepInput(kind=kind, old_ids=old, new_ids=new)


def ontology_row():
    return SimpleNamespace(
        ontology_key="ontology-1",
        version=1,
        class_map={"package": 0, "label": 1},
        classes=[
            {"stable_id": "package", "name": "Package", "definition": "A package"},
            {"stable_id": "label", "name": "Label", "definition": "A label"},
        ],
    )


def catalog(*stable_ids):
    return {
        "base_ontology_id": "ontology-1",
        "classes": [
            {
                "stable_id": sid,
                "name": sid.title(),
                "definition": f"Definition of {sid}",
                "aliases": [],
                "parent_id": None,
            }
            for sid in stable_ids
        ],
    }


class MigrationPreparationTests(unittest.TestCase):
    def test_dense_map_keeps_survivor_order_and_appends_new_sorted(self):
        self.assertEqual(
            migration_class_map({"b": 1, "a": 0, "gone": 2}, ["zeta", "b", "a", "mid"]),
            {"a": 0, "b": 1, "mid": 2, "zeta": 3},
        )

    def test_duplicate_ids_within_a_step_refuse(self):
        with self.assertRaises(ManagementFailure) as raised:
            migration_steps([step("split", ["package"], ["box", "box"])])
        self.assertEqual(raised.exception.error.detail, "unsupported_classes")

    def test_split_migration_prepares_map_and_export_indices(self):
        steps = migration_steps(
            [step("split", ["package"], ["box", "bag"]), step("retain", ["label"], ["label"])]
        )
        mapping, export_map = prepare_migration(
            uuid4(), ontology_row(), catalog("bag", "label", "box"), steps
        )
        self.assertEqual(mapping, {"label": 0, "bag": 1, "box": 2})
        self.assertEqual(export_map["package"], {"old_index": 0, "new_index": None})
        self.assertEqual(export_map["label"], {"old_index": 1, "new_index": 0})
        self.assertEqual(export_map["box"], {"old_index": None, "new_index": 2})

    def test_rename_only_migration_refuses_as_ordinary_publication(self):
        steps = migration_steps(
            [step("rename", ["package"], ["package"]), step("retain", ["label"], ["label"])]
        )
        with self.assertRaises(ManagementFailure) as raised:
            prepare_migration(uuid4(), ontology_row(), catalog("package", "label"), steps)
        self.assertEqual(raised.exception.error.detail, "unsupported_classes")

    def test_missing_disposition_refuses(self):
        steps = migration_steps([step("remove", ["label"], [])])
        with self.assertRaises(ManagementFailure) as raised:
            prepare_migration(uuid4(), ontology_row(), catalog("package"), steps)
        self.assertEqual(raised.exception.error.detail, "unsupported_classes")

    def test_inferred_relabel_refuses(self):
        steps = migration_steps(
            [step("retain", ["package"], ["replacement"]), step("retain", ["label"], ["label"])]
        )
        with self.assertRaises(ManagementFailure) as raised:
            prepare_migration(uuid4(), ontology_row(), catalog("replacement", "label"), steps)
        self.assertEqual(raised.exception.error.detail, "unsupported_classes")

    def test_merge_into_existing_survivor_is_explicit(self):
        steps = migration_steps([step("merge", ["package", "label"], ["package"])])
        mapping, export_map = prepare_migration(uuid4(), ontology_row(), catalog("package"), steps)
        self.assertEqual(mapping, {"package": 0})
        self.assertEqual(export_map["label"], {"old_index": 1, "new_index": None})


if __name__ == "__main__":
    unittest.main()
