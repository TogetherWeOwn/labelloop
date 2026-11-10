"""Editable recipe plans: pins, ROI geometry and frozen publication."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from labelloop_api.recipe_planning import (
    RecipePlan,
    assert_not_frozen_release,
    build_recipe_plan,
    freeze_recipe_plan,
    plan_digest,
    require_fresh,
    validate_plan_geometry,
    with_crop,
)
from labelloop_core.domain import Recipe
from labelloop_core.geometry import Crop

SHA = "a" * 64
RECIPE = Recipe("full-letterbox-640-v1", 640, "auto-area-linear")


def _plan(**changes):
    values = {
        "plan_id": "plan-1",
        "asset_id": "asset",
        "source_sha256": SHA,
        "source_width": 1920,
        "source_height": 1080,
        "ontology_version": 2,
        "ontology_hash": "b" * 64,
        "assignment_version": "split-v1",
        "annotation_revision": 3,
        "recipe": RECIPE,
    }
    values.update(changes)
    return build_recipe_plan(**values)


def _fresh(**changes):
    values = {
        "source_sha256": SHA,
        "ontology_hash": "b" * 64,
        "assignment_version": "split-v1",
        "annotation_revision": 3,
    }
    values.update(changes)
    return values


def test_roi_bounds_and_geometry_reuse_core_validation():
    plan = _plan(crop=Crop(10, 10, 640, 640))
    validate_plan_geometry(plan)
    for bad in (
        Crop(1900, 0, 100, 10),
        Crop(0, 0, 100, 2000),
    ):
        with pytest.raises(ValueError):
            _plan(crop=bad)
    with pytest.raises(ValueError):
        Crop(-1, 0, 10, 10)
    with pytest.raises(ValueError, match="fractional"):
        _plan(crop=Crop(0.5, 0, 10, 10))


def test_plan_digest_is_deterministic_and_pin_sensitive():
    assert plan_digest(_plan()) == plan_digest(_plan())
    assert plan_digest(_plan()) != plan_digest(_plan(crop=Crop(0, 0, 100, 100)))
    assert plan_digest(_plan()) != plan_digest(
        _plan(recipe=Recipe("full-letterbox-320-v1", 320, "auto-area-linear"))
    )


def test_stale_source_ontology_assignment_revision_are_refused():
    plan = _plan()
    require_fresh(plan, **_fresh())
    for changes in (
        {"source_sha256": "c" * 64},
        {"ontology_hash": "c" * 64},
        {"assignment_version": "split-v2"},
        {"annotation_revision": 4},
    ):
        with pytest.raises(ValueError, match="stale"):
            require_fresh(plan, **_fresh(**changes))


def test_frozen_preview_is_not_an_editable_plan():
    with pytest.raises(ValueError, match="frozen"):
        RecipePlan(
            plan_id="p",
            asset_id="asset",
            source_sha256=SHA,
            source_width=100,
            source_height=100,
            ontology_version=1,
            ontology_hash="b" * 64,
            assignment_version="v1",
            annotation_revision=1,
            recipe=RECIPE,
            frozen=True,
        )
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = True
    with pytest.raises(ValueError, match="frozen"):
        assert_not_frozen_release(connection, release_id="release")
    with pytest.raises(ValueError, match="frozen"):
        require_fresh(_plan(), **_fresh(), frozen=True)


def test_editable_crop_and_frozen_publication():
    plan = with_crop(_plan(), Crop(0, 0, 320, 320))
    assert plan.crop == Crop(0, 0, 320, 320)
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.first.return_value = None
    digest = freeze_recipe_plan(connection, plan, recipe_key="roi-320-v1")
    assert digest == plan_digest(plan)
    inserts = [
        call
        for call in connection.execute.call_args_list
        if "INSERT INTO preprocessing_recipe" in str(call.args[0])
    ]
    assert len(inserts) == 1
    assert "immutable" in str(inserts[0].args[0])


def test_concurrent_publication_conflict_is_refused():
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.first.return_value = {
        "recipe_key": "roi-320-v1"
    }
    with pytest.raises(ValueError, match="already published"):
        freeze_recipe_plan(connection, _plan(), recipe_key="roi-320-v1")

    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.first.return_value = None
    connection.execute.side_effect = [
        MagicMock(),
        IntegrityError("duplicate", None, Exception("duplicate")),
    ]
    with pytest.raises(ValueError, match="already published"):
        freeze_recipe_plan(connection, _plan(), recipe_key="roi-320-v1")
