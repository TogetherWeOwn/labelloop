from dataclasses import replace
from typing import cast

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
def test_manifest_cells_digest_and_requests_preserve_pins(expanded: bool) -> None:
    frozen = manifest(expanded=expanded)
    cells = frozen.cells()
    assert len(cells) == len(set(cells)) == (32 if expanded else 16)
    assert frozen.development_groups() == ("dev",)
    assert frozen.cohorts() == (("curated-only", "expanded") if expanded else ("curated-only",))
    assert frozen.digest() == replace(frozen).digest()
    assert frozen.digest() != replace(frozen, seeds=(2,)).digest()
    for cell in cells:
        request = frozen.request(cell, "run")
        recipe = next(r for r in frozen.recipes if r.recipe_id == cell.recipe_id)
        assert request.initialization_sha256 == (
            frozen.approved_base_sha256
            if cell.initialization == "base"
            else recipe.warm_start_sha256
        )
        assert request.policy == recipe.policy
        assert request.seed == cell.seed and request.project_id == frozen.project_id
        assert request.class_map_sha256 == frozen.class_map_sha256
        assert request.class_mirror_safety == frozen.class_mirror_safety
        assert request.augmentation.profile_id == cell.augmentation_id
        assert request.dataset_manifest_sha256 == "e" * 64
    with pytest.raises(ValueError, match="outside"):
        frozen.request(replace(cells[0], seed=99), "run")


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
    "kind",
    [
        "classes",
        "baseline",
        "alternative",
        "datasets",
        "recipes",
        "seeds",
        "threshold",
        "identity",
        "digest",
        "metric",
    ],
)
def test_manifest_rejects_invalid_contracts(kind: str) -> None:
    frozen = manifest()
    with pytest.raises(ValueError):
        if kind == "classes":
            replace(frozen, class_mirror_safety=(("object", False),))
        elif kind == "baseline":
            replace(frozen, augmentations=(AugmentationProfile("color-v1", hsv_s=0.1),))
        elif kind == "alternative":
            replace(frozen, augmentations=(BASELINE, AugmentationProfile("same-v1")))
        elif kind == "datasets":
            replace(frozen, datasets=frozen.datasets[:1])
        elif kind == "recipes":
            replace(frozen, recipes=(frozen.recipes[0], frozen.recipes[0]))
        elif kind == "seeds":
            replace(frozen, seeds=(0, 0))
        elif kind == "threshold":
            replace(frozen, minimum_gain=float("nan"))
        elif kind == "identity":
            replace(frozen, experiment_id="")
        elif kind == "digest":
            replace(frozen, class_map_sha256="invalid")
        else:
            replace(frozen, metric="unapproved")
