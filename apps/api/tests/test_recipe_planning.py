"""Editable recipe plans: pins, ROI geometry and frozen publication."""

import json
from dataclasses import asdict
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from labelloop_api.recipe_planning import (
    RecipePlan,
    assert_not_frozen_release,
    build_recipe_plan,
    freeze_recipe_plan,
    ontology_release_hash,
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


_ONT_CLASSES = [{"stable_id": "cat", "name": "cat", "definition": "def"}]
_ONT_CLASS_MAP = {"cat": 0}


def _freeze_plan(**changes):
    values = {
        "ontology_hash": ontology_release_hash(_ONT_CLASSES, _ONT_CLASS_MAP),
    }
    values.update(changes)
    return _plan(**values)


def _freeze_connection(plan, *, existing=None, conflict=False, revision=3):
    """Dispatching mock: pin rows must match the plan or freeze must refuse."""
    connection = MagicMock()
    sqls = []

    def execute(statement, parameters=None):
        sql = str(statement)
        sqls.append(sql)
        result = MagicMock()
        if "INSERT INTO preprocessing_recipe" in sql:
            if conflict:
                raise IntegrityError("duplicate", None, Exception("duplicate"))
            return result
        if "FROM source_asset" in sql:
            result.mappings.return_value.one.return_value = {
                "sha256": plan.source_sha256,
                "width": plan.source_width,
                "height": plan.source_height,
                "project_id": "project",
            }
        elif "FROM ontology_release" in sql:
            result.mappings.return_value.first.return_value = {
                "classes": _ONT_CLASSES,
                "class_map": _ONT_CLASS_MAP,
                "version": plan.ontology_version,
            }
        elif "FROM source_split_assignment" in sql:
            result.mappings.return_value.first.return_value = {
                "assignment_version": plan.assignment_version,
            }
        elif "FROM annotation_revision" in sql:
            result.mappings.return_value.first.return_value = {"revision": revision}
        elif "FROM preprocessing_recipe" in sql:
            result.mappings.return_value.first.return_value = existing
        return result

    connection.execute.side_effect = execute
    connection.sqls = sqls
    return connection


def test_editable_crop_and_frozen_publication():
    plan = with_crop(_freeze_plan(), Crop(0, 0, 320, 320))
    assert plan.crop == Crop(0, 0, 320, 320)
    connection = _freeze_connection(plan)
    digest = freeze_recipe_plan(connection, plan, recipe_key=RECIPE.recipe_id)
    assert digest == plan_digest(plan)
    inserts = [sql for sql in connection.sqls if "INSERT INTO preprocessing_recipe" in sql]
    assert len(inserts) == 1


def test_freeze_revalidates_pins_and_refuses_stale_plan():
    plan = _freeze_plan()
    connection = _freeze_connection(plan, revision=4)  # annotation moved on
    with pytest.raises(ValueError, match="stale"):
        freeze_recipe_plan(connection, plan, recipe_key=RECIPE.recipe_id)
    assert not any("INSERT" in sql for sql in connection.sqls)


def test_concurrent_publication_conflict_is_refused():
    plan = _freeze_plan()
    connection = _freeze_connection(plan, existing={"recipe_key": RECIPE.recipe_id})
    with pytest.raises(ValueError, match="already published"):
        freeze_recipe_plan(connection, plan, recipe_key=RECIPE.recipe_id)

    connection = _freeze_connection(plan, conflict=True)
    with pytest.raises(ValueError, match="already published"):
        freeze_recipe_plan(connection, plan, recipe_key=RECIPE.recipe_id)
        freeze_recipe_plan(connection, _plan(), recipe_key=RECIPE.recipe_id)


def test_freeze_refuses_recipe_key_mismatch_and_crop_collisions():
    plan = with_crop(_freeze_plan(), Crop(0, 0, 320, 320))
    connection = _freeze_connection(plan)
    with pytest.raises(ValueError, match="recipe_key must match"):
        freeze_recipe_plan(connection, plan, recipe_key="roi-320-v1")
    assert not any("INSERT" in sql for sql in connection.sqls)
    # A second ROI over the same recipe_id must fail loud, never publish an
    # identical row silently.
    connection = _freeze_connection(plan, existing={"recipe_key": RECIPE.recipe_id})
    with pytest.raises(ValueError, match="already published"):
        freeze_recipe_plan(connection, plan, recipe_key=RECIPE.recipe_id)


def test_json_loaded_policy_matches_tuple_recipe():
    # DB JSON policies hold lists; asdict holds tuples — no false staleness.
    policy = json.loads(json.dumps(asdict(RECIPE)))
    assert policy != asdict(RECIPE)  # tuple vs list shape difference
    require_fresh(
        _freeze_plan(),
        recipe_policy=policy,
        **_fresh(ontology_hash=_freeze_plan().ontology_hash),
    )


def test_ontology_hash_matches_canonical_order():
    classes = [
        {"stable_id": "dog", "name": "dog", "definition": "def"},
        {"stable_id": "cat", "name": "cat", "definition": "def"},
    ]
    reordered = [classes[1], classes[0]]
    assert ontology_release_hash(classes, {"cat": 0, "dog": 1}) == ontology_release_hash(
        reordered, {"cat": 0, "dog": 1}
    )


def test_assignment_query_is_deterministic_and_limit_one():
    from labelloop_api.recipe_planning import load_current_pins

    plan = _freeze_plan()
    connection = _freeze_connection(plan)
    pins = load_current_pins(connection, asset_id=plan.asset_id)
    assert pins["assignment_version"] == plan.assignment_version
    assignment_sql = [s for s in connection.sqls if "source_split_assignment" in s][0]
    assert "ORDER BY" in assignment_sql and "LIMIT" in assignment_sql
    assert "created_at" in assignment_sql
