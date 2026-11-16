"""Immutable experiment input and evidence records; consumers own validation and gates."""

from dataclasses import dataclass
from typing import Literal

from .evaluation import EvaluationReport
from .training import AugmentationProfile, InferencePolicy, TrainingResult

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


@dataclass(frozen=True, slots=True)
class RetentionObservation:
    asset_id: str
    before: int
    after: int
    before_small: int
    after_small: int


@dataclass(frozen=True, slots=True)
class ArmEvidence:
    manifest_sha256: str
    cell: Cell
    training: TrainingResult
    # Complete-source and per-source-group reports from the same predictions.
    evaluation: EvaluationReport
    group_reports: tuple[tuple[str, EvaluationReport], ...]
    retention: tuple[RetentionObservation, ...]
    tensor_shapes: tuple[tuple[str, tuple[tuple[int, ...], ...]], ...]
    export_sha256: str
    export_weights_sha256: str
    export_signature: tuple[int, ...]
    export_policy: InferencePolicy
    export_samples: int
    export_detections: int


@dataclass(frozen=True, slots=True)
class PairedComparison:
    recipe_id: str
    axis: str
    baseline: Cell
    candidate: Cell
    metric: str
    seeds: tuple[int, ...]
    source_groups: int
    images: int
    support: int
    per_class_support: tuple[tuple[str, int], ...]
    group_deltas: tuple[tuple[str, float], ...]
    mean_delta: float
    bootstrap_95_interval: tuple[float, float] | None
    regression_reasons: tuple[str, ...]
    outcome: str
    fixture_only: bool
    m6_exit_established: bool = False
