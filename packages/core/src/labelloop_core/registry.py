"""Pure per-recipe promotion decisions; storage must commit each transition atomically."""

import math
from dataclasses import dataclass

from .evaluation import EvaluationReport
from .training import ULTRALYTICS_VERSION, InferencePolicy, require_digest


@dataclass(frozen=True, slots=True)
class ModelRelease:
    release_id: str
    project_id: str
    recipe_id: str
    weights_sha256: str
    preprocessing_sha256: str
    class_map_sha256: str
    class_ids: tuple[str, ...]
    policy: InferencePolicy
    ultralytics_version: str
    torch_version: str
    export_sha256: str
    export_format: str
    static_input_signature: tuple[int, int, int, int]
    evaluation_run_ids: tuple[str, ...]
    tiling_mode: str = "off"

    def __post_init__(self) -> None:
        if not all((self.release_id, self.project_id, self.recipe_id, self.torch_version)):
            raise ValueError("model releases need complete identity and environment lineage")
        for value in (
            self.weights_sha256,
            self.preprocessing_sha256,
            self.class_map_sha256,
            self.export_sha256,
        ):
            require_digest(value)
        if not self.class_ids or len(set(self.class_ids)) != len(self.class_ids):
            raise ValueError("a release needs a unique nonempty class mapping")
        if not self.evaluation_run_ids or len(set(self.evaluation_run_ids)) != len(
            self.evaluation_run_ids
        ):
            raise ValueError("a release requires unique evaluation run IDs")
        if self.ultralytics_version != ULTRALYTICS_VERSION:
            raise ValueError("release must record the pinned Ultralytics version")
        if self.export_format != "onnx" or self.static_input_signature != (
            1,
            3,
            self.policy.imgsz,
            self.policy.imgsz,
        ):
            raise ValueError("release requires a static ONNX export matching its recipe")
        if self.tiling_mode not in {"off", "application"}:
            raise ValueError("native tiling is not part of the v1 controlled pipeline")


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    min_class_support: int
    min_map50_95: float
    max_regression: float = 0.0

    def __post_init__(self) -> None:
        if self.min_class_support < 1:
            raise ValueError("per-class support must be positive")
        if not all(
            math.isfinite(value) and 0 <= value <= 1
            for value in (self.min_map50_95, self.max_regression)
        ):
            raise ValueError("promotion targets must be explicit finite probabilities")

    def qualify(self, release: ModelRelease, report: EvaluationReport) -> None:
        if (
            report.run_id not in release.evaluation_run_ids
            or report.recipe_id != release.recipe_id
            or report.weights_sha256 != release.weights_sha256
            or report.class_map_sha256 != release.class_map_sha256
            or report.policy != release.policy
            or set(report.overall.per_class) != set(release.class_ids)
        ):
            raise ValueError("evaluation does not describe this atomic model release")
        for metrics in report.overall.per_class.values():
            if not metrics.supported or metrics.support < self.min_class_support:
                raise ValueError("unsupported class or insufficient per-class evidence")
            if metrics.map50_95 is None or metrics.map50_95 < self.min_map50_95:
                raise ValueError("per-class evaluation target failed")


@dataclass(frozen=True, slots=True)
class RegistryState:
    project_id: str
    recipe_id: str
    revision: int = 0
    champion: ModelRelease | None = None
    rollback: ModelRelease | None = None

    def __post_init__(self) -> None:
        if not self.project_id or not self.recipe_id or self.revision < 0:
            raise ValueError("registry state requires project, recipe and nonnegative revision")
        for release in (self.champion, self.rollback):
            if release and (release.project_id, release.recipe_id) != (
                self.project_id,
                self.recipe_id,
            ):
                raise ValueError("registry state cannot cross project or recipe boundaries")

    def promote(
        self,
        candidate: ModelRelease,
        report: EvaluationReport,
        policy: PromotionPolicy,
        *,
        expected_revision: int,
        incumbent_report: EvaluationReport | None = None,
    ) -> "RegistryState":
        if expected_revision != self.revision:
            raise ValueError("stale registry revision")
        if (candidate.project_id, candidate.recipe_id) != (self.project_id, self.recipe_id):
            raise ValueError("promotion cannot cross project or recipe boundaries")
        if self.champion and self.champion.release_id == candidate.release_id:
            raise ValueError("candidate is already champion")
        policy.qualify(candidate, report)
        if self.champion:
            if incumbent_report is None:
                raise ValueError("incumbent comparison evidence is required")
            policy.qualify(self.champion, incumbent_report)
            if (
                incumbent_report.dataset_release_id != report.dataset_release_id
                or incumbent_report.role != report.role
            ):
                raise ValueError(
                    "candidate and incumbent must share the frozen evaluation protocol"
                )
            for class_id, metrics in report.overall.per_class.items():
                before = incumbent_report.overall.per_class[class_id].map50_95
                after = metrics.map50_95
                if before is None or after is None or after + policy.max_regression < before:
                    raise ValueError("unacceptable per-class regression")
        return RegistryState(
            self.project_id, self.recipe_id, self.revision + 1, candidate, self.champion
        )

    def roll_back(self, *, expected_revision: int) -> "RegistryState":
        if expected_revision != self.revision:
            raise ValueError("stale registry revision")
        if self.rollback is None:
            raise ValueError("no known-good rollback release")
        return RegistryState(
            self.project_id, self.recipe_id, self.revision + 1, self.rollback, self.champion
        )
