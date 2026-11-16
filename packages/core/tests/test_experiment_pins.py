from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from labelloop_core.experiments import Cell, DatasetPin, RecipePin, SourcePin
from labelloop_core.training import InferencePolicy


@pytest.mark.parametrize(
    "pin",
    [
        SourcePin("asset", "group", "a" * 64, "b" * 64, "development"),
        RecipePin("recipe", "c" * 64, InferencePolicy(320), "d" * 64, "e" * 64, ("group",)),
        DatasetPin("recipe", "curated-only", "release", "f" * 64, ("asset",)),
        Cell("recipe", "curated-only", "base", "no-scene-composition-v1", 0),
    ],
)
def test_experiment_pins_are_immutable_value_records(
    pin: SourcePin | RecipePin | DatasetPin | Cell,
) -> None:
    assert replace(pin) == pin
    assert asdict(replace(pin)) == asdict(pin)
    field = next(iter(asdict(pin)))
    with pytest.raises(FrozenInstanceError):
        setattr(pin, field, "changed")


def test_source_truth_and_checkpoint_ancestry_remain_distinct() -> None:
    source = SourcePin("asset", "group", "a" * 64, "b" * 64, "locked_final_test")
    recipe = RecipePin(
        "recipe", "c" * 64, InferencePolicy(640), "d" * 64, "e" * 64, ("train-group",)
    )
    assert source.source_sha256 != source.truth_sha256
    assert source.role == "locked_final_test"
    assert recipe.policy.imgsz == 640
    assert recipe.warm_start_sha256 != recipe.warm_start_base_sha256
    assert recipe.warm_start_training_groups == ("train-group",)
    assert replace(source, role="calibration").truth_sha256 == source.truth_sha256


def test_factorial_cell_identity_includes_seed_and_cohort() -> None:
    cell = Cell("recipe", "curated-only", "base", "no-scene-composition-v1", 0)
    assert cell != replace(cell, seed=1)
    assert cell != replace(cell, cohort="expanded")
    assert cell != replace(cell, initialization="warm-start")
    assert cell != replace(cell, augmentation_id="color-v1")
    assert cell != replace(cell, recipe_id="other-recipe")
    dataset = DatasetPin("recipe", "expanded", "release", "a" * 64, ("anchor", "extra"))
    assert asdict(dataset)["training_asset_ids"] == ("anchor", "extra")
