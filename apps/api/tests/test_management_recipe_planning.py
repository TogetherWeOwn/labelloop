"""Pure recipe-planning controls; database pin resolution runs in Actions."""

import unittest

from labelloop_api.management_ports import ManagementFailure
from labelloop_api.management_recipe_planning import assemble_plan, revise_crop, server_recipe
from labelloop_core.geometry import Crop


def pins(**overrides):
    values = {
        "asset_id": "3f605eaf-d9fa-4325-b201-2def26423fee",
        "asset_key": "asset-1",
        "source_sha256": "a" * 64,
        "source_width": 1920,
        "source_height": 1080,
        "ontology_version": 2,
        "ontology_hash": "b" * 64,
        "assignment_version": "assignment-1",
        "annotation_revision": 3,
    }
    return {**values, **overrides}


class RecipePlanningTests(unittest.TestCase):
    def test_full_frame_plan_pins_and_digest_are_deterministic(self):
        plan, digest = assemble_plan("plan-1", pins(), server_recipe("full-letterbox-320-v1"))
        again, again_digest = assemble_plan(
            "plan-1", pins(), server_recipe("full-letterbox-320-v1")
        )
        self.assertEqual(plan, again)
        self.assertEqual(digest, again_digest)
        self.assertEqual(len(digest), 64)
        self.assertIsNone(plan.crop)
        self.assertEqual(plan.annotation_revision, 3)

    def test_crop_revision_changes_the_digest(self):
        plan, digest = assemble_plan("plan-1", pins(), server_recipe("full-letterbox-640-v1"))
        cropped, cropped_digest = revise_crop(plan, Crop(420, 0, 1080, 1080))
        self.assertNotEqual(digest, cropped_digest)
        self.assertEqual(cropped.crop.width, 1080)
        cleared, cleared_digest = revise_crop(cropped, None)
        self.assertEqual(cleared_digest, digest)
        self.assertIsNone(cleared.crop)

    def test_out_of_bounds_and_fractional_crops_refuse(self):
        plan, _ = assemble_plan("plan-1", pins(), server_recipe("full-letterbox-320-v1"))
        for crop in (Crop(1900, 0, 100, 100), Crop(0.5, 0, 100, 100)):
            with self.assertRaises(ManagementFailure) as caught:
                revise_crop(plan, crop)
            self.assertEqual(caught.exception.error.detail, "incompatible_recipe")

    def test_unknown_recipe_and_broken_pins_refuse(self):
        with self.assertRaises(ManagementFailure) as caught:
            server_recipe("invented-recipe-v9")
        self.assertEqual(caught.exception.error.detail, "incompatible_recipe")
        with self.assertRaises(ManagementFailure) as caught:
            assemble_plan(
                "plan-1", pins(source_sha256="short"), server_recipe("full-letterbox-320-v1")
            )
        self.assertEqual(caught.exception.error.detail, "incompatible_recipe")

    def test_recipe_choice_changes_the_digest(self):
        _, small = assemble_plan("plan-1", pins(), server_recipe("full-letterbox-320-v1"))
        _, large = assemble_plan("plan-1", pins(), server_recipe("full-letterbox-640-v1"))
        self.assertNotEqual(small, large)


if __name__ == "__main__":
    unittest.main()
