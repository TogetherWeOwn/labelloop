from dataclasses import replace
from typing import Any, cast

import pytest

from labelloop_core.experiments import (
    BASELINE,
    Cohort,
    DatasetPin,
    ExperimentManifest,
    RecipePin,
    SourcePin,
    SourceRole,
)
from labelloop_core.training import AugmentationProfile, InferencePolicy


def manifest(*, expanded: bool = False) -> ExperimentManifest:
    sources = tuple(
        SourcePin(name, name, str(i) * 64, str(i + 1) * 64, cast(SourceRole, role))
        for i, (name, role) in enumerate(
            (("train", "curated"), ("dev", "development"), ("test", "locked_final_test"))
            + ((("extra", "expansion"),) if expanded else ())
        )
    )
    recipes = tuple(
        RecipePin(f"r{side}", "a" * 64, InferencePolicy(side), "b" * 64, "c" * 64, ("train",))
        for side in (320, 640)
    )
    return ExperimentManifest(
        "experiment",
        "project",
        "d" * 64,
        (("object", True),),
        "splits",
        "c" * 64,
        sources,
        recipes,
        tuple(
            DatasetPin(recipe.recipe_id, cast(Cohort, cohort), "release", "e" * 64, ids)
            for recipe in recipes
            for cohort, ids in (("curated-only", ("train",)),)
            + ((("expanded", ("train", "extra")),) if expanded else ())
        ),
        (BASELINE, AugmentationProfile("flip-v1", fliplr=0.5)),
        (0, 1),
    )


@pytest.mark.parametrize("expanded", [False, True])
def test_manifest_cells_and_digest_preserve_pins(expanded: bool) -> None:
    frozen = manifest(expanded=expanded)
    cells = frozen.cells()
    assert len(cells) == len(set(cells)) == (32 if expanded else 16)
    assert frozen.development_groups() == ("dev",)
    assert frozen.cohorts() == (("curated-only", "expanded") if expanded else ("curated-only",))
    assert frozen.digest() == replace(frozen).digest()
    assert frozen.digest() != replace(frozen, seeds=(2,)).digest()
    assert {c.recipe_id for c in cells} == {"r320", "r640"}
    assert {c.seed for c in cells} == {0, 1}
    assert {c.initialization for c in cells} == {"base", "warm-start"}
    assert {c.augmentation_id for c in cells} == {BASELINE.profile_id, "flip-v1"}


@pytest.mark.parametrize("role", ["development", "calibration", "locked_final_test"])
@pytest.mark.parametrize("field", ["group_id", "source_sha256"])
def test_manifest_rejects_cross_role_leakage(role: str, field: str) -> None:
    frozen = manifest()
    source = replace(frozen.sources[2], role=cast(SourceRole, role))
    changed = replace(source, **{field: getattr(frozen.sources[0], field)})
    with pytest.raises(ValueError, match="leakage"):
        replace(frozen, sources=frozen.sources[:2] + (changed,))


@pytest.mark.parametrize("kind", ["base", "same", "empty", "expansion", "holdout", "cohort"])
def test_manifest_rejects_checkpoint_ancestry_and_membership_drift(kind: str) -> None:
    frozen = manifest(expanded=True)
    recipe = frozen.recipes[0]
    if kind == "cohort":
        with pytest.raises(ValueError, match="frozen cohort"):
            replace(
                frozen,
                datasets=(replace(frozen.datasets[0], training_asset_ids=("extra",)),)
                + frozen.datasets[1:],
            )
        return
    if kind == "base":
        recipe = replace(recipe, warm_start_base_sha256="f" * 64)
    elif kind == "same":
        recipe = replace(recipe, warm_start_sha256=frozen.approved_base_sha256)
    else:
        groups = {"empty": (), "expansion": ("extra",), "holdout": ("test",)}
        recipe = replace(recipe, warm_start_training_groups=groups[kind])
    with pytest.raises(ValueError, match="base checkpoint|distinct|ancestry"):
        replace(frozen, recipes=(recipe, frozen.recipes[1]))


@pytest.mark.parametrize(
    "changes",
    [
        {"class_mirror_safety": (("object", False),)},
        {"augmentations": (AugmentationProfile("color-v1", hsv_s=0.1),)},
        {"augmentations": (BASELINE, AugmentationProfile("same-v1"))},
        {"datasets": ()},
        {"recipes": ()},
        {"seeds": (0, 0)},
        {"minimum_gain": float("nan")},
        {"experiment_id": ""},
        {"class_map_sha256": "invalid"},
        {"metric": "unapproved"},
    ],
)
def test_manifest_rejects_invalid_contracts(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        replace(manifest(), **changes)


@pytest.mark.parametrize("expanded", [False, True])
def test_identical_pixels_cannot_claim_distinct_groups(expanded: bool) -> None:
    frozen = manifest(expanded=expanded)
    source = frozen.sources[-1] if expanded else frozen.sources[1]
    duplicate = replace(source, asset_id="duplicate", group_id="different")
    with pytest.raises(ValueError, match="identical pixels"):
        replace(frozen, sources=frozen.sources + (duplicate,))


@pytest.mark.parametrize(
    "field",
    ["epochs", "batch", "minimum_class_support", "minimum_slice_support", "uncertainty_seed"],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1.5, True, -1, None])
def test_manifest_integer_controls_are_strict(field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        replace(manifest(), **{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "seeds",
        "sources",
        "recipes",
        "datasets",
        "augmentations",
        "critical_slices",
        "class_mirror_safety",
        "ancestry",
        "membership",
        "class-pair",
    ],
)
def test_manifest_rejects_mutable_collection_aliases(field: str) -> None:
    frozen = manifest()
    changes: dict[str, Any]
    if field == "ancestry":
        changes = {
            "recipes": (
                replace(frozen.recipes[0], warm_start_training_groups=cast(Any, ["train"])),
            )
            + frozen.recipes[1:]
        }
    elif field == "membership":
        changes = {
            "datasets": (replace(frozen.datasets[0], training_asset_ids=cast(Any, ["train"])),)
            + frozen.datasets[1:]
        }
    elif field == "class-pair":
        changes = {"class_mirror_safety": (["object", True],)}
    else:
        changes = {field: list(getattr(frozen, field))}
    with pytest.raises(ValueError, match="immutable tuples"):
        replace(frozen, **cast(Any, changes))


def test_manifest_rejects_empty_recipe_identity() -> None:
    frozen = manifest()
    with pytest.raises(ValueError, match="recipe identit"):
        replace(
            frozen,
            recipes=(replace(frozen.recipes[0], recipe_id=""), frozen.recipes[1]),
            datasets=(replace(frozen.datasets[0], recipe_id=""), frozen.datasets[1]),
        )
