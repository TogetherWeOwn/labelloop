"""Fail-closed qualification evidence consumed by the existing recipe registry.

These records are not credentials. Trusted adapters must load policy, membership,
independent truth and *current* final-test state under a lock; model/HTTP bodies
must never be allowed to construct authoritative contexts or select their scope.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .audit import AuditObservation, AuditSample, assess_audit
from .evaluation import ClassMetrics, EvaluationReport
from .evaluation_lifecycle import EvaluationSet, FinalTestState
from .qualification_data import (
    DriftIncident,
    PipelineIdentity,
    ScopeCell,
    SourceGroup,
    record_sha256,
    require_count,
    require_incidents,
    require_name,
    require_names,
    require_probability,
    require_scope,
)
from .training import require_digest

if TYPE_CHECKING:
    from .registry import ModelRelease, PromotionPolicy


@dataclass(frozen=True, slots=True)
class SliceTarget:
    slice_name: str
    class_id: str
    min_support: int | None = None
    min_map50_95: float | None = None
    max_regression: float | None = None

    def __post_init__(self) -> None:
        require_name(self.slice_name)
        require_name(self.class_id)
        if self.min_support is not None:
            require_count(self.min_support, minimum=1)
        for value in (self.min_map50_95, self.max_regression):
            if value is not None:
                require_probability(value)


@dataclass(frozen=True, slots=True)
class QualificationTargets:
    """No defaults invent a production target. Nulls deliberately retain the incumbent."""

    min_class_support: int | None = None
    min_map50_95: float | None = None
    max_regression: float | None = None
    min_audit_groups: int | None = None
    min_accepted_precision: float | None = None
    min_discovery_recall: float | None = None
    max_geometry_error: float | None = None
    min_canary_groups: int | None = None
    critical_slices: tuple[SliceTarget, ...] = ()

    def __post_init__(self) -> None:
        for count in (self.min_class_support, self.min_audit_groups, self.min_canary_groups):
            if count is not None:
                require_count(count, minimum=1)
        for value in (
            self.min_map50_95,
            self.max_regression,
            self.min_accepted_precision,
            self.min_discovery_recall,
            self.max_geometry_error,
        ):
            if value is not None:
                require_probability(value)
        keys = [(item.slice_name, item.class_id) for item in self.critical_slices]
        if len(set(keys)) != len(keys):
            raise ValueError("critical slice targets must be unique")

    def require_configured(self) -> None:
        if (
            any(
                value is None
                for value in (
                    self.min_class_support,
                    self.min_map50_95,
                    self.max_regression,
                    self.min_audit_groups,
                    self.min_accepted_precision,
                    self.min_discovery_recall,
                    self.max_geometry_error,
                    self.min_canary_groups,
                )
            )
            or not self.critical_slices
        ):
            raise ValueError("null or missing qualification targets retain the incumbent")
        if any(
            item.min_support is None or item.min_map50_95 is None or item.max_regression is None
            for item in self.critical_slices
        ):
            raise ValueError("null critical slice targets retain the incumbent")


@dataclass(frozen=True, slots=True)
class AnnotationMeasurement:
    scope: ScopeCell
    group_ids: tuple[str, ...]
    accepted_labels: int
    correct_labels: int
    truth_objects: int
    discovered_objects: int
    max_geometry_error: float

    def __post_init__(self) -> None:
        require_names(self.group_ids)
        for value in (
            self.accepted_labels,
            self.correct_labels,
            self.truth_objects,
            self.discovered_objects,
        ):
            require_count(value)
        if (
            self.correct_labels > self.accepted_labels
            or self.discovered_objects > self.truth_objects
        ):
            raise ValueError("annotation measurement numerator exceeds denominator")
        require_probability(self.max_geometry_error)


@dataclass(frozen=True, slots=True)
class AnnotationEvidence:
    sample: AuditSample
    observations: tuple[AuditObservation, ...]
    sources: tuple[SourceGroup, ...]
    truth_basis: Literal["adjudicated", "trusted_system_truth", "model_agreement"]
    measurements: tuple[AnnotationMeasurement, ...]
    remediation_for: tuple[DriftIncident, ...] = ()

    def __post_init__(self) -> None:
        require_incidents(self.remediation_for)
        if self.truth_basis not in {"adjudicated", "trusted_system_truth", "model_agreement"}:
            raise ValueError("unknown audit truth basis")
        if len({item.asset_id for item in self.sources}) != len(self.sources):
            raise ValueError("audit source identities must be unique")
        cells = tuple(item.scope for item in self.measurements)
        require_scope(cells)

    @property
    def group_ids(self) -> frozenset[str]:
        return frozenset(item.group_id for item in self.sources)

    def require_quality(
        self,
        pipeline: PipelineIdentity,
        scope: tuple[ScopeCell, ...],
        targets: QualificationTargets,
        *,
        canary: bool = False,
    ) -> None:
        if self.truth_basis == "model_agreement":
            raise ValueError("repeated model agreement is not independent truth")
        verdict = assess_audit(self.sample, self.observations)
        if not verdict.passed:
            raise ValueError(
                "annotation audit blocks qualification: adverse or missing observations"
            )
        members = {item.asset_id: item for item in self.sample.candidates}
        sources = {item.asset_id: item for item in self.sources}
        if set(members) != set(sources):
            raise ValueError("audit groups must describe every frozen sample member exactly")
        if any(
            item.pipeline_id != pipeline.pipeline_id
            or item.revision.scope_completeness != "exhaustive_for_scope"
            for item in members.values()
        ):
            raise ValueError("audit does not describe the complete pinned acceptance pipeline")
        # Ordinary accepted negatives are essential to missing-object discovery evidence.
        if not any(not item.revision.objects for item in members.values()):
            raise ValueError("annotation audit must include ordinary accepted negatives")
        if set(item.scope for item in self.measurements) != set(scope):
            raise ValueError("missing or extra class/source/condition audit coverage")
        minimum = targets.min_canary_groups if canary else targets.min_audit_groups
        assert minimum is not None
        assert targets.min_accepted_precision is not None
        assert targets.min_discovery_recall is not None
        assert targets.max_geometry_error is not None
        for item in self.measurements:
            covered = {
                source.group_id
                for source in self.sources
                if source.source_id == item.scope.source_id
                and item.scope.condition in source.conditions
                and any(
                    obj.class_id == item.scope.class_id
                    for obj in members[source.asset_id].revision.objects
                )
            }
            if not set(item.group_ids) <= covered or len(item.group_ids) < minimum:
                raise ValueError("insufficient independent source-group support for audit scope")
            if (
                item.accepted_labels < 1
                or item.truth_objects < 1
                or item.correct_labels / item.accepted_labels < targets.min_accepted_precision
                or item.discovered_objects / item.truth_objects < targets.min_discovery_recall
                or item.max_geometry_error > targets.max_geometry_error
            ):
                raise ValueError("annotation precision, discovery or geometry target failed")


@dataclass(frozen=True, slots=True)
class QualificationLineage:
    training_group_ids: tuple[str, ...]
    reference_group_ids: tuple[str, ...]
    development: EvaluationSet
    calibration: EvaluationSet | None

    def __post_init__(self) -> None:
        require_names(self.training_group_ids)
        require_names(self.reference_group_ids, allow_empty=True)
        if self.development.role != "development":
            raise ValueError("qualification lineage requires development selection data")
        if self.calibration is not None and self.calibration.role != "calibration":
            raise ValueError("qualification lineage requires calibration threshold data")

    def require_isolated(self, final_test: FinalTestState, selection_id: str) -> frozenset[str]:
        dataset = final_test.dataset
        sets = (self.development, *((self.calibration,) if self.calibration else ()), dataset)
        if len({item.release_id for item in sets}) != len(sets):
            raise ValueError("evaluation role releases must be distinct")
        used = set(self.training_group_ids)
        for item in sets:
            if item.project_id != dataset.project_id or used & set(item.group_ids):
                raise ValueError("evaluation groups overlap training or another evaluation role")
            used.update(item.group_ids)
        evaluation_groups = used - set(self.training_group_ids)
        if evaluation_groups & set(self.reference_group_ids):
            raise ValueError("evaluation holdouts cannot be reference evidence")
        selection = next(
            (
                item.selection
                for item in final_test.assessments
                if item.selection.selection_id == selection_id
            ),
            None,
        )
        if selection is None or (
            selection.development_set_id != self.development.release_id
            or selection.calibration_set_id
            != (self.calibration.release_id if self.calibration else None)
        ):
            raise ValueError("selection and qualification lineage disagree")
        return frozenset(used | set(self.reference_group_ids))


def _require_metrics(metrics: ClassMetrics, support: int, target: float) -> None:
    require_count(metrics.support)
    if metrics.supported is not True or metrics.support < support:
        raise ValueError("unsupported class or insufficient qualification support")
    if metrics.map50_95 is None:
        raise ValueError("missing qualification metric")
    require_probability(metrics.map50_95)
    if metrics.map50_95 < target:
        raise ValueError("qualification per-class or critical-slice target failed")


def _require_regression(before: ClassMetrics, after: ClassMetrics, tolerance: float) -> None:
    # The incumbent need not meet today's absolute quality/support gates, but must be measured.
    _require_metrics(before, 1, 0.0)
    if before.map50_95 is None or after.map50_95 is None:
        raise ValueError("missing non-regression evidence")
    if after.map50_95 + tolerance < before.map50_95:
        raise ValueError("unacceptable qualification critical-slice or class regression")


@dataclass(frozen=True, slots=True)
class QualificationEvidence:
    qualification_id: str
    release_sha256: str
    pipeline: PipelineIdentity
    scope: tuple[ScopeCell, ...]
    targets: QualificationTargets
    annotation_audit: AnnotationEvidence
    canary_audit: AnnotationEvidence
    canary_release_sha256: str
    lineage: QualificationLineage
    final_test: FinalTestState
    selection_id: str
    remediation_for: tuple[DriftIncident, ...] = ()

    def __post_init__(self) -> None:
        require_incidents(self.remediation_for)
        require_name(self.qualification_id)
        require_name(self.selection_id)
        require_digest(self.release_sha256)
        require_digest(self.canary_release_sha256)
        require_scope(self.scope)


@dataclass(frozen=True, slots=True)
class QualificationContext:
    """Trusted read port: current values, never supplied as authoritative HTTP/model claims."""

    evidence: QualificationEvidence
    current_pipeline: PipelineIdentity
    current_final_test: FinalTestState

    def require_qualified(
        self,
        release: "ModelRelease",
        report: EvaluationReport,
        incumbent_report: EvaluationReport | None = None,
        policy: "PromotionPolicy | None" = None,
    ) -> None:
        evidence = self.evidence
        targets = evidence.targets
        targets.require_configured()
        if (
            evidence.pipeline != self.current_pipeline
            or evidence.final_test != self.current_final_test
            or evidence.release_sha256 != record_sha256(release)
            or evidence.canary_release_sha256 != record_sha256(release)
            or evidence.pipeline.project_id != release.project_id
            or evidence.pipeline.recipe_id != release.recipe_id
            or evidence.pipeline.preprocessing_sha256 != release.preprocessing_sha256
            or evidence.pipeline.acceptance_policy_sha256 != record_sha256(targets)
            or set(item.class_id for item in evidence.scope) != set(release.class_ids)
            or evidence.final_test.dataset.project_id != release.project_id
        ):
            raise ValueError("stale or mismatched qualification pipeline, release, scope or policy")
        if set(evidence.remediation_for) != set(evidence.canary_audit.remediation_for) or any(
            (incident.project_id, incident.recipe_id) != (release.project_id, release.recipe_id)
            for incident in evidence.remediation_for
        ):
            raise ValueError("qualification and fresh canary must address the same drift incidents")
        assert targets.min_class_support is not None
        assert targets.min_map50_95 is not None
        assert targets.max_regression is not None
        if policy is not None and (
            policy.min_class_support,
            policy.min_map50_95,
            policy.max_regression,
        ) != (targets.min_class_support, targets.min_map50_95, targets.max_regression):
            raise ValueError("registry and qualification policies disagree")
        evidence.final_test.require_independent(release, report)
        used = evidence.lineage.require_isolated(evidence.final_test, evidence.selection_id)
        evidence.annotation_audit.require_quality(evidence.pipeline, evidence.scope, targets)
        evidence.canary_audit.require_quality(
            evidence.pipeline, evidence.scope, targets, canary=True
        )
        if evidence.canary_audit.group_ids & (used | evidence.annotation_audit.group_ids):
            raise ValueError(
                "canary groups must be fresh, outside training, evaluation and prior audit"
            )
        if evidence.annotation_audit.group_ids & set(evidence.final_test.dataset.group_ids):
            raise ValueError("final-test groups cannot calibrate annotation acceptance")
        for metrics in report.overall.per_class.values():
            _require_metrics(metrics, targets.min_class_support, targets.min_map50_95)
        if incumbent_report is not None:
            if (
                incumbent_report.dataset_release_id != report.dataset_release_id
                or incumbent_report.role != report.role
                or incumbent_report.recipe_id != report.recipe_id
                or set(incumbent_report.overall.per_class) != set(release.class_ids)
            ):
                raise ValueError("non-regression requires the same frozen evaluation protocol")
            for class_id, metrics in report.overall.per_class.items():
                _require_regression(
                    incumbent_report.overall.per_class[class_id], metrics, targets.max_regression
                )
        for target in targets.critical_slices:
            assert target.min_support is not None
            assert target.min_map50_95 is not None
            assert target.max_regression is not None
            if target.class_id not in release.class_ids:
                raise ValueError("critical slice names an out-of-scope class")
            sliced = report.slices.get(target.slice_name)
            if sliced is None or target.class_id not in sliced.per_class:
                raise ValueError("missing critical-slice evidence")
            metrics = sliced.per_class[target.class_id]
            _require_metrics(metrics, target.min_support, target.min_map50_95)
            if incumbent_report is not None:
                previous = incumbent_report.slices.get(target.slice_name)
                if previous is None or target.class_id not in previous.per_class:
                    raise ValueError("missing incumbent critical-slice evidence")
                _require_regression(
                    previous.per_class[target.class_id], metrics, target.max_regression
                )
