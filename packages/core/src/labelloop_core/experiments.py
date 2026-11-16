"""Frozen experiment input pins; validation and comparisons follow separately."""

from dataclasses import dataclass
from typing import Literal

from .training import AugmentationProfile, InferencePolicy

SourceRole = Literal["curated", "expansion", "development", "calibration", "locked_final_test"]
Cohort = Literal["curated-only", "expanded"]
Initialization = Literal["base", "warm-start"]
METRIC = "source-group-macro-map50-95-v1"
BASELINE = AugmentationProfile()


@dataclass(frozen=True, slots=True)
class SourcePin:
    asset_id: str
    group_id: str
    source_sha256: str
    truth_sha256: str
    role: SourceRole


@dataclass(frozen=True, slots=True)
class RecipePin:
    recipe_id: str
    recipe_sha256: str
    policy: InferencePolicy
    warm_start_sha256: str
    warm_start_base_sha256: str
    warm_start_training_groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DatasetPin:
    recipe_id: str
    cohort: Cohort
    release_id: str
    manifest_sha256: str
    training_asset_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Cell:
    recipe_id: str
    cohort: Cohort
    initialization: Initialization
    augmentation_id: str
    seed: int
