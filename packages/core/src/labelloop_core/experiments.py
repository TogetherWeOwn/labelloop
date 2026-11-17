"""Frozen experiment inputs and read-only paired comparisons, not a scheduler or gate."""

import hashlib
import json
import random
from collections import Counter
from dataclasses import asdict, dataclass
from itertools import product
from statistics import mean
from typing import Literal

from .evaluation import ClassMetrics, EvaluationReport, Metrics
from .qualification_data import require_names, require_probability
from .training import (
    AugmentationProfile,
    InferencePolicy,
    TrainingRequest,
    TrainingResult,
    argument_diff,
    require_digest,
)

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
        require_names(self.critical_slices, allow_empty=True)
        for value in (self.minimum_gain, self.regression_tolerance):
            require_probability(value)
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

    def request(self, cell: Cell, run_id: str) -> TrainingRequest:
        if type(cell.seed) is not int or cell not in self.cells():
            raise ValueError("cell is outside the frozen experiment")
        recipe = next(r for r in self.recipes if r.recipe_id == cell.recipe_id)
        dataset = next(
            d for d in self.datasets if (d.recipe_id, d.cohort) == (cell.recipe_id, cell.cohort)
        )
        profile = next(p for p in self.augmentations if p.profile_id == cell.augmentation_id)
        return TrainingRequest(
            run_id,
            self.project_id,
            recipe.recipe_id,
            dataset.release_id,
            dataset.manifest_sha256,
            self.class_map_sha256,
            self.split_assignment_version,
            self.approved_base_sha256
            if cell.initialization == "base"
            else recipe.warm_start_sha256,
            recipe.policy,
            self.epochs,
            self.batch,
            cell.seed,
            augmentation=profile,
            class_mirror_safety=self.class_mirror_safety,
        )


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


def validate_report(
    manifest: ExperimentManifest,
    training: TrainingResult,
    report: EvaluationReport,
    source_ids: set[str],
) -> None:
    """Validate development report bindings; support and paired-arm gates are separate."""
    development = {s.asset_id for s in manifest.sources if s.role == "development"}
    if not source_ids or not source_ids <= development:
        raise ValueError("report sources must be frozen development members")
    request = training.request
    if (
        report.recipe_id,
        report.dataset_release_id,
        report.class_map_sha256,
        report.weights_sha256,
        report.role,
        report.policy,
    ) != (
        request.recipe_id,
        request.dataset_release_id,
        request.class_map_sha256,
        training.selected.sha256,
        "development",
        request.policy,
    ):
        raise ValueError("evaluation checkpoint, role or pipeline mismatch")
    ids = [c.asset_id for c in report.coverage]
    if (
        set(ids) != source_ids
        or len(ids) != len(source_ids)
        or report.overall.images != len(source_ids)
    ):
        raise ValueError("evaluation membership differs from frozen sources")
    truth_pins = {s.asset_id: s.truth_sha256 for s in manifest.sources if s.asset_id in source_ids}
    if dict(report.source_truth_sha256) != truth_pins or len(report.source_truth_sha256) != len(
        truth_pins
    ):
        raise ValueError("evaluation truth binding differs from frozen sources")
    if any(
        c.covered_fraction != 1 or c.uncovered_targets or c.partially_covered_targets
        for c in report.coverage
    ):
        raise ValueError("comparison cannot hide targets through reduced coverage")
    for metrics in (report.overall, *report.slices.values()):
        if set(metrics.per_class) != dict(manifest.class_mirror_safety).keys():
            raise ValueError("evaluation class catalog mismatch")
        score_sets: tuple[Metrics | ClassMetrics, ...] = (metrics, *metrics.per_class.values())
        for scores in score_sets:
            for value in (scores.map50, scores.map50_95, scores.precision, scores.recall):
                if value is not None:
                    try:
                        require_probability(value)
                    except ValueError as error:
                        raise ValueError("nonfinite or invalid evaluation metric") from error


def validate_training_evidence(manifest: ExperimentManifest, arm: ArmEvidence) -> None:
    """Validate frozen training, dataloader and export evidence, not evaluation quality."""
    if arm.manifest_sha256 != manifest.digest():
        raise ValueError("experiment manifest digest mismatch")
    expected = manifest.request(arm.cell, arm.training.request.run_id)
    if arm.training.request != expected:
        raise ValueError("training request differs from frozen experiment")
    if argument_diff(expected.arguments(), arm.training.resolved_arguments):
        raise ValueError("args_mismatch")
    epochs = tuple(checkpoint.epoch for checkpoint in arm.training.checkpoints)
    if any(type(epoch) is not int for epoch in epochs) or tuple(sorted(epochs)) != tuple(
        range(expected.epochs)
    ):
        raise ValueError("checkpoint epoch domain differs from frozen experiment")
    training_ids = set(
        next(
            d.training_asset_ids
            for d in manifest.datasets
            if (d.recipe_id, d.cohort) == (arm.cell.recipe_id, arm.cell.cohort)
        )
    )
    # The compiler emits one derivative per asset/recipe; the controlled CPU loader
    # visits each once per epoch (workers=0, no native tiling or scene composition).
    visits = Counter(r.asset_id for r in arm.retention)
    if visits != {asset_id: expected.epochs for asset_id in training_ids}:
        raise ValueError("incomplete dataloader retention audit")
    for observation in arm.retention:
        counts = (
            observation.before,
            observation.after,
            observation.before_small,
            observation.after_small,
        )
        if any(type(count) is not int or count < 0 for count in counts) or (
            observation.before != observation.after
            or observation.before_small > observation.before
            or observation.after_small > observation.after
        ):
            raise ValueError("dataloader lost targets or invalid retention counts")
    shapes = dict(arm.tensor_shapes)
    if (
        len(shapes) != len(arm.tensor_shapes)
        or set(shapes) != {"training", "validation", "inference"}
        or not all(shapes.values())
    ):
        raise ValueError("incomplete actual tensor evidence")
    for values in shapes.values():
        for shape in values:
            if any(type(dimension) is not int for dimension in shape):
                raise ValueError("actual tensor dimensions must be integers")
            expected.policy.assert_shape(shape, stride=32)
    require_digest(arm.export_sha256)
    if (
        arm.export_weights_sha256 != arm.training.selected.sha256
        or arm.export_policy != expected.policy
        or arm.export_signature != (1, 3, expected.policy.imgsz, expected.policy.imgsz)
        or any(type(dimension) is not int for dimension in arm.export_signature)
        or any(
            type(count) is not int or count < 1
            for count in (arm.export_samples, arm.export_detections)
        )
    ):
        raise ValueError("export contract or checkpoint mismatch")


def validate_arm(manifest: ExperimentManifest, arm: ArmEvidence) -> None:
    validate_training_evidence(manifest, arm)
    development = {s.asset_id for s in manifest.sources if s.role == "development"}
    validate_report(manifest, arm.training, arm.evaluation, development)
    if (
        arm.evaluation.run_id != arm.training.selected.evaluation_run_id
        or arm.evaluation.overall.map50_95 != arm.training.selected.validation_map
    ):
        raise ValueError("selected checkpoint evaluation drift")
    groups = dict(arm.group_reports)
    if len(groups) != len(arm.group_reports) or set(groups) != set(manifest.development_groups()):
        raise ValueError("unpaired source groups")
    for group, report in groups.items():
        ids = {
            s.asset_id for s in manifest.sources if s.role == "development" and s.group_id == group
        }
        validate_report(manifest, arm.training, report, ids)
        if report.overall.map50_95 is None:
            raise ValueError("source group lacks support for the declared metric")
    for class_id, metrics in arm.evaluation.overall.per_class.items():
        if (
            metrics.supported is not True
            or type(metrics.support) is not int
            or metrics.support < manifest.minimum_class_support
            or metrics.map50_95 is None
        ):
            raise ValueError(f"unsupported class: {class_id}")
    for name in manifest.critical_slices:
        slice_metrics = arm.evaluation.slices.get(name)
        if (
            slice_metrics is None
            or type(slice_metrics.support) is not int
            or slice_metrics.support < manifest.minimum_slice_support
            or slice_metrics.map50_95 is None
        ):
            raise ValueError(f"unsupported critical slice: {name}")
    for report in (arm.evaluation, *groups.values()):
        for scores in (report.overall, *report.slices.values()):
            _validate_metric_support(scores)
        for scores in report.slices.values():
            if any(
                item.support > report.overall.per_class[class_id].support
                for class_id, item in scores.per_class.items()
            ):
                raise ValueError("slice class support exceeds overall class support")
    if any(report.overall.support == 0 for report in groups.values()):
        raise ValueError("source group lacks support for the declared metric")
    for class_id, metrics in arm.evaluation.overall.per_class.items():
        if metrics.support != sum(
            report.overall.per_class[class_id].support for report in groups.values()
        ):
            raise ValueError("aggregate class support differs from source groups")
    for name in manifest.critical_slices:
        if any(name not in report.slices for report in groups.values()):
            raise ValueError(f"missing source-group critical slice: {name}")
        for class_id, metrics in arm.evaluation.slices[name].per_class.items():
            if metrics.support != sum(
                report.slices[name].per_class[class_id].support for report in groups.values()
            ):
                raise ValueError(
                    f"aggregate critical-slice support differs from source groups: {name}"
                )


def _validate_metric_support(metrics: Metrics) -> None:
    for item in metrics.per_class.values():
        if (
            type(item.support) is not int
            or item.support < 0
            or item.supported is not (item.support > 0)
            or (item.map50_95 is None) != (item.support == 0)
        ):
            raise ValueError("inconsistent class support")
    if (
        type(metrics.support) is not int
        or metrics.support != sum(item.support for item in metrics.per_class.values())
        or (metrics.map50_95 is None) != (metrics.support == 0)
    ):
        raise ValueError("evaluation support drift or inconsistent metric support")


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


def _support(metrics: Metrics) -> tuple[object, ...]:
    return (
        metrics.images,
        metrics.support,
        tuple(sorted((key, item.support) for key, item in metrics.per_class.items())),
    )


def _compare(
    manifest: ExperimentManifest,
    baseline: Cell,
    candidate: Cell,
    arms: dict[Cell, ArmEvidence],
    axis: str,
) -> PairedComparison:
    """Compare one pair across seeds; the caller must validate every arm first."""
    from dataclasses import replace

    deltas: dict[str, list[float]] = {group: [] for group in manifest.development_groups()}
    regressions: set[str] = set()
    reference = arms[baseline]
    for seed in manifest.seeds:
        left = arms[replace(baseline, seed=seed)]
        right = arms[replace(candidate, seed=seed)]
        for anchor, a, b in (
            (reference.evaluation, left.evaluation, right.evaluation),
            *(
                (
                    dict(reference.group_reports)[g],
                    dict(left.group_reports)[g],
                    dict(right.group_reports)[g],
                )
                for g in deltas
            ),
        ):
            if not _support(anchor.overall) == _support(a.overall) == _support(b.overall):
                raise ValueError("paired ground-truth support drift")
            for name in manifest.critical_slices:
                if (
                    not _support(anchor.slices[name])
                    == _support(a.slices[name])
                    == _support(b.slices[name])
                ):
                    raise ValueError("paired critical-slice support drift")
        for group in deltas:
            a_score = dict(left.group_reports)[group].overall.map50_95
            b_score = dict(right.group_reports)[group].overall.map50_95
            assert a_score is not None and b_score is not None
            deltas[group].append(b_score - a_score)
        for class_id, a_class in left.evaluation.overall.per_class.items():
            b_class = right.evaluation.overall.per_class[class_id]
            assert a_class.map50_95 is not None and b_class.map50_95 is not None
            if b_class.map50_95 < a_class.map50_95 - manifest.regression_tolerance:
                regressions.add(f"class:{class_id}:seed:{seed}")
        for name in manifest.critical_slices:
            a_slice, b_slice = left.evaluation.slices[name], right.evaluation.slices[name]
            if _support(a_slice) != _support(b_slice):
                raise ValueError("paired critical-slice support drift")
            assert a_slice.map50_95 is not None and b_slice.map50_95 is not None
            if b_slice.map50_95 < a_slice.map50_95 - manifest.regression_tolerance:
                regressions.add(f"slice:{name}:seed:{seed}")
        environment = (reference.training.ultralytics_version, reference.training.torch_version)
        if any(
            (arm.training.ultralytics_version, arm.training.torch_version) != environment
            for arm in (left, right)
        ):
            raise ValueError("paired training environment drift")
    group_deltas = tuple((group, mean(values)) for group, values in sorted(deltas.items()))
    values = [value for _, value in group_deltas]
    delta = mean(values)
    interval = None
    if len(values) > 1:
        rng = random.Random(manifest.uncertainty_seed)
        samples = sorted(mean(rng.choices(values, k=len(values))) for _ in range(2000))
        interval = (samples[49], samples[1949])
    if regressions or delta < -manifest.regression_tolerance:
        outcome = "regression"
    elif interval is None:
        outcome = "insufficient-evidence"
    elif interval[0] > manifest.minimum_gain:
        outcome = "observed-gain-unqualified"
    else:
        outcome = "plateau-or-inconclusive"
    if manifest.fixture_only and outcome == "observed-gain-unqualified":
        outcome = "fixture-wiring-only"
    overall = arms[baseline].evaluation.overall
    return PairedComparison(
        baseline.recipe_id,
        axis,
        baseline,
        candidate,
        manifest.metric,
        manifest.seeds,
        len(values),
        overall.images,
        overall.support,
        tuple(sorted((k, v.support) for k, v in overall.per_class.items())),
        group_deltas,
        delta,
        interval,
        tuple(sorted(regressions)),
        outcome,
        manifest.fixture_only,
    )


def compare_experiment(
    manifest: ExperimentManifest, evidence: tuple[ArmEvidence, ...]
) -> tuple[PairedComparison, ...]:
    """Compare only development evidence; final assessments never select an experiment."""
    from dataclasses import replace

    arms = {arm.cell: arm for arm in evidence}
    if len(arms) != len(evidence) or set(arms) != set(manifest.cells()):
        raise ValueError("experiment requires every paired cell exactly once")
    if len({arm.training.request.run_id for arm in evidence}) != len(evidence):
        raise ValueError("training run identities must be unique")
    for arm in evidence:
        validate_arm(manifest, arm)
    comparisons = []
    for cell in manifest.cells():
        if cell.seed != manifest.seeds[0]:
            continue
        if cell.initialization == "warm-start":
            comparisons.append(
                _compare(
                    manifest, replace(cell, initialization="base"), cell, arms, "initialization"
                )
            )
        if cell.augmentation_id != BASELINE.profile_id:
            comparisons.append(
                _compare(
                    manifest,
                    replace(cell, augmentation_id=BASELINE.profile_id),
                    cell,
                    arms,
                    "augmentation",
                )
            )
        if cell.cohort == "expanded":
            comparisons.append(
                _compare(
                    manifest, replace(cell, cohort="curated-only"), cell, arms, "training-cohort"
                )
            )
    return tuple(comparisons)
