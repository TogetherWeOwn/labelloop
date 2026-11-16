"""Validated experiment manifests and evidence records; consumers own execution and gates."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from itertools import product
from typing import Literal

from .evaluation import EvaluationReport
from .training import AugmentationProfile, InferencePolicy, TrainingResult, require_digest

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
class ExperimentManifest:
    experiment_id: str
    project_id: str
    class_map_sha256: str
    class_mirror_safety: tuple[tuple[str, bool], ...]
    split_assignment_version: str
    approved_base_sha256: str
    sources: tuple[SourcePin, ...]
    recipes: tuple[RecipePin, ...]
    datasets: tuple[DatasetPin, ...]
    augmentations: tuple[AugmentationProfile, ...]
    seeds: tuple[int, ...]
    epochs: int = 1
    batch: int = 2
    metric: str = METRIC
    minimum_gain: float = 0.0
    regression_tolerance: float = 0.0
    critical_slices: tuple[str, ...] = ()
    minimum_class_support: int = 1
    minimum_slice_support: int = 1
    fixture_only: bool = True
    uncertainty_seed: int = 0

    def __post_init__(self) -> None:
        collections = (
            self.sources,
            self.recipes,
            self.datasets,
            self.augmentations,
            self.seeds,
            self.critical_slices,
            self.class_mirror_safety,
        )
        if any(type(value) is not tuple for value in collections) or any(
            type(value) is not tuple
            for value in (
                *self.class_mirror_safety,
                *(r.warm_start_training_groups for r in self.recipes),
                *(d.training_asset_ids for d in self.datasets),
            )
        ):
            raise ValueError("manifest collections must be immutable tuples")
        if not all((self.experiment_id, self.project_id, self.split_assignment_version)):
            raise ValueError("experiment identity is required")
        for digest in (self.class_map_sha256, self.approved_base_sha256):
            require_digest(digest)
        if self.metric != METRIC:
            raise ValueError("unsupported declared metric")
        if not all(
            math.isfinite(v) and 0 <= v <= 1 for v in (self.minimum_gain, self.regression_tolerance)
        ):
            raise ValueError("invalid comparison thresholds")
        if any(
            type(value) is not int or value < 1
            for value in (
                self.epochs,
                self.batch,
                self.minimum_class_support,
                self.minimum_slice_support,
            )
        ):
            raise ValueError("training bounds and support minima must be positive integers")
        if type(self.uncertainty_seed) is not int or self.uncertainty_seed < 0:
            raise ValueError("invalid uncertainty seed")
        if not self.seeds or len(set(self.seeds)) != len(self.seeds):
            raise ValueError("seeds must be unique and nonempty")
        if not all(type(seed) is int and seed >= 0 for seed in self.seeds):
            raise ValueError("invalid seed")
        classes = dict(self.class_mirror_safety)
        if not classes or len(classes) != len(self.class_mirror_safety) or not all(classes):
            raise ValueError("unique class catalog required")
        profiles = {p.profile_id: p for p in self.augmentations}
        if (
            len(profiles) != len(self.augmentations)
            or profiles.get(BASELINE.profile_id) != BASELINE
        ):
            raise ValueError("baseline augmentation drift or duplicate profiles")
        if len(profiles) < 2 or any(
            p == BASELINE for k, p in profiles.items() if k != BASELINE.profile_id
        ):
            raise ValueError("an explicit augmentation alternative is required")
        if any(p.fliplr for p in profiles.values()) and not all(
            v is True for v in classes.values()
        ):
            raise ValueError("horizontal flip requires every class to be mirror_safe")
        if any(
            p.arguments() == BASELINE.arguments()
            for k, p in profiles.items()
            if k != BASELINE.profile_id
        ):
            raise ValueError("alternative must differ from baseline")
        if not self.sources or len({s.asset_id for s in self.sources}) != len(self.sources):
            raise ValueError("source membership must be unique and nonempty")
        group_roles: dict[str, set[str]] = {}
        hash_roles: dict[str, set[str]] = {}
        hash_groups: dict[str, set[str]] = {}
        for source in self.sources:
            if (
                not source.asset_id
                or not source.group_id
                or source.role
                not in {"curated", "expansion", "development", "calibration", "locked_final_test"}
            ):
                raise ValueError("invalid source identity or role")
            require_digest(source.source_sha256)
            require_digest(source.truth_sha256)
            # Curated and expansion may share training groups, never evaluation groups.
            role = "train" if source.role in {"curated", "expansion"} else source.role
            group_roles.setdefault(source.group_id, set()).add(role)
            hash_roles.setdefault(source.source_sha256, set()).add(role)
            hash_groups.setdefault(source.source_sha256, set()).add(source.group_id)
        if any(len(roles) != 1 for roles in (*group_roles.values(), *hash_roles.values())):
            raise ValueError("source-group or pixel leakage across evaluation roles")
        if any(len(groups) != 1 for groups in hash_groups.values()):
            raise ValueError("identical pixels must share one source group")
        if not any(s.role == "curated" for s in self.sources) or not self.development_groups():
            raise ValueError("curated anchor and development sources are required")
        if len(self.recipes) != 2 or {r.policy.imgsz for r in self.recipes} != {320, 640}:
            raise ValueError("paired 320/640 recipes are required")
        if not all(r.recipe_id for r in self.recipes) or len(
            {r.recipe_id for r in self.recipes}
        ) != len(self.recipes):
            raise ValueError("recipe identities must be nonempty and unique")
        # A shared warm checkpoint initializes both cohorts: expansion must never have
        # influenced it, including through a group containing both kinds of source.
        training_groups = {s.group_id for s in self.sources if s.role == "curated"} - {
            s.group_id for s in self.sources if s.role == "expansion"
        }
        for recipe in self.recipes:
            for digest in (
                recipe.recipe_sha256,
                recipe.warm_start_sha256,
                recipe.warm_start_base_sha256,
            ):
                require_digest(digest)
            if recipe.warm_start_base_sha256 != self.approved_base_sha256:
                raise ValueError("warm-start must descend from the same approved base checkpoint")
            if recipe.warm_start_sha256 == self.approved_base_sha256:
                raise ValueError("warm-start must identify a distinct trained checkpoint")
            if (
                not recipe.warm_start_training_groups
                or not set(recipe.warm_start_training_groups) <= training_groups
            ):
                raise ValueError(
                    "warm-start ancestry missing or includes expansion/evaluation groups"
                )
        expected = set(product((r.recipe_id for r in self.recipes), self.cohorts()))
        if {(d.recipe_id, d.cohort) for d in self.datasets} != expected or len(
            self.datasets
        ) != len(expected):
            raise ValueError("dataset pins must cover every recipe and configured cohort")
        for dataset in self.datasets:
            require_digest(dataset.manifest_sha256)
            roles = {"curated"} if dataset.cohort == "curated-only" else {"curated", "expansion"}
            intended = {s.asset_id for s in self.sources if s.role in roles}
            if (
                not dataset.release_id
                or set(dataset.training_asset_ids) != intended
                or len(dataset.training_asset_ids) != len(intended)
            ):
                raise ValueError("training membership differs from the frozen cohort")

    def cohorts(self) -> tuple[Cohort, ...]:
        return (
            ("curated-only", "expanded")
            if any(s.role == "expansion" for s in self.sources)
            else ("curated-only",)
        )

    def development_groups(self) -> tuple[str, ...]:
        return tuple(sorted({s.group_id for s in self.sources if s.role == "development"}))

    def cells(self) -> tuple[Cell, ...]:
        initializations: tuple[Initialization, ...] = ("base", "warm-start")
        return tuple(
            Cell(*values)
            for values in product(
                (r.recipe_id for r in self.recipes),
                self.cohorts(),
                initializations,
                (p.profile_id for p in self.augmentations),
                self.seeds,
            )
        )

    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode()).hexdigest()


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
