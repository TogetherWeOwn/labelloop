"""Pure evaluation-use guards and append-only final-test exposure accounting.

The durable adapter must lock the set and selection before exposing any result,
including aggregate scores or a pass/fail bit. A failed request is not permission
not to record a real exposure: record it, then account for adaptation separately.
"""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from .evaluation import EvaluationReport
from .qualification_data import (
    canonical_record,
    record_sha256,
    require_count,
    require_name,
    require_names,
)
from .training import require_digest

if TYPE_CHECKING:
    from .registry import ModelRelease

SetRole = Literal["training", "development", "calibration", "locked_final_test", "rotating_audit"]
EvidenceUse = Literal["training", "checkpoint_selection", "threshold_tuning", "reference_retrieval"]


@dataclass(frozen=True, slots=True)
class EvaluationSet:
    release_id: str
    project_id: str
    group_ids: tuple[str, ...]
    role: SetRole

    def __post_init__(self) -> None:
        require_name(self.release_id)
        require_name(self.project_id)
        require_names(self.group_ids)
        if self.role not in {
            "training",
            "development",
            "calibration",
            "locked_final_test",
            "rotating_audit",
        }:
            raise ValueError("unknown evaluation-set role")

    def require_use(self, purpose: EvidenceUse) -> None:
        allowed = {
            "training": {"training"},
            "checkpoint_selection": {"development"},
            "threshold_tuning": {"calibration"},
            "reference_retrieval": {"training"},
        }
        if purpose not in allowed or self.role not in allowed[purpose]:
            raise ValueError("evaluation role forbids this development use")


@dataclass(frozen=True, slots=True)
class CandidateSelection:
    selection_id: str
    project_id: str
    release_id: str
    release_sha256: str
    development_run_id: str
    development_set_id: str
    calibration_run_id: str | None
    calibration_set_id: str | None

    def __post_init__(self) -> None:
        for value in (
            self.selection_id,
            self.project_id,
            self.release_id,
            self.development_run_id,
            self.development_set_id,
        ):
            require_name(value)
        require_digest(self.release_sha256)
        if (self.calibration_run_id is None) != (self.calibration_set_id is None):
            raise ValueError("threshold tuning needs both calibration run and set")
        if self.calibration_run_id is not None:
            require_name(self.calibration_run_id)
            assert self.calibration_set_id is not None
            require_name(self.calibration_set_id)


def _require_report(release: "ModelRelease", report: EvaluationReport) -> None:
    if (
        report.run_id not in release.evaluation_run_ids
        or report.recipe_id != release.recipe_id
        or report.weights_sha256 != release.weights_sha256
        or report.class_map_sha256 != release.class_map_sha256
        or report.policy != release.policy
        or set(report.overall.per_class) != set(release.class_ids)
    ):
        raise ValueError("evaluation does not describe the selected atomic release")


def select_candidate(
    release: "ModelRelease",
    development: EvaluationReport,
    development_set: EvaluationSet,
    *,
    selection_id: str,
    calibration: EvaluationReport | None = None,
    calibration_set: EvaluationSet | None = None,
) -> CandidateSelection:
    """Freeze the entire candidate before opening final assessment; never rank on final."""
    development_set.require_use("checkpoint_selection")
    _require_report(release, development)
    if (
        development.role != "development"
        or development.dataset_release_id != development_set.release_id
        or development_set.project_id != release.project_id
    ):
        raise ValueError("selection requires matching development evidence")
    if (calibration is None) != (calibration_set is None):
        raise ValueError("threshold tuning requires a calibration set and report")
    if calibration is not None and calibration_set is not None:
        calibration_set.require_use("threshold_tuning")
        _require_report(release, calibration)
        if (
            calibration.role != "calibration"
            or calibration.dataset_release_id != calibration_set.release_id
            or calibration_set.project_id != release.project_id
            or set(calibration_set.group_ids) & set(development_set.group_ids)
        ):
            raise ValueError("threshold tuning requires isolated calibration evidence")
    return CandidateSelection(
        selection_id,
        release.project_id,
        release.release_id,
        record_sha256(release),
        development.run_id,
        development_set.release_id,
        calibration.run_id if calibration else None,
        calibration_set.release_id if calibration_set else None,
    )


@dataclass(frozen=True, slots=True)
class FinalAssessment:
    selection: CandidateSelection
    report_json: str

    def __post_init__(self) -> None:
        require_name(self.report_json)


@dataclass(frozen=True, slots=True)
class FinalTestState:
    dataset: EvaluationSet
    revision: int = 0
    assessments: tuple[FinalAssessment, ...] = ()
    adaptation_reasons: tuple[str, ...] = ()
    quarantine_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.dataset.role != "locked_final_test":
            raise ValueError("final-test state must retain its original locked-test identity")
        require_count(self.revision)
        require_names(self.adaptation_reasons, allow_empty=True)
        require_names(self.quarantine_reasons, allow_empty=True)
        if len({item.selection.selection_id for item in self.assessments}) != len(self.assessments):
            raise ValueError("final-test exposure identities must be unique")
        if any(item.selection.project_id != self.dataset.project_id for item in self.assessments):
            raise ValueError("final assessment cannot cross projects")

    @property
    def effective_role(self) -> SetRole:
        return "development" if self.adaptation_reasons else "locked_final_test"

    def _expect(self, revision: int) -> None:
        if revision != self.revision:
            raise ValueError("stale final-test revision")

    def expose(
        self,
        release: "ModelRelease",
        selection: CandidateSelection,
        report: EvaluationReport,
        *,
        expected_revision: int,
    ) -> "FinalTestState":
        self._expect(expected_revision)
        if self.adaptation_reasons or self.quarantine_reasons:
            raise ValueError("adapted or quarantined final test cannot qualify a release")
        if self.assessments:
            raise ValueError("final test already exposed; obtain a fresh assessment release")
        _require_report(release, report)
        if (
            selection.project_id != self.dataset.project_id
            or release.project_id != self.dataset.project_id
            or selection.release_id != release.release_id
            or selection.release_sha256 != record_sha256(release)
            or report.role != "locked_final_test"
            or report.dataset_release_id != self.dataset.release_id
            or self.dataset.release_id
            in {selection.development_set_id, selection.calibration_set_id}
        ):
            raise ValueError("final assessment must follow a pinned development selection")
        return replace(
            self,
            revision=self.revision + 1,
            assessments=(*self.assessments, FinalAssessment(selection, canonical_record(report))),
        )

    def record_adaptation(self, reason: str, *, expected_revision: int) -> "FinalTestState":
        """Any aggregate, pass/fail or failure-guided change spends the entire test release."""
        self._expect(expected_revision)
        require_name(reason)
        if not self.assessments:
            raise ValueError("cannot attribute adaptation to an unexposed final test")
        if reason in self.adaptation_reasons:
            raise ValueError("adaptation already recorded")
        return replace(
            self,
            revision=self.revision + 1,
            adaptation_reasons=(*self.adaptation_reasons, reason),
        )

    def quarantine(self, reason: str, *, expected_revision: int) -> "FinalTestState":
        self._expect(expected_revision)
        require_name(reason)
        if reason in self.quarantine_reasons:
            raise ValueError("quarantine already recorded")
        return replace(
            self,
            revision=self.revision + 1,
            quarantine_reasons=(*self.quarantine_reasons, reason),
        )

    def require_independent(self, release: "ModelRelease", report: EvaluationReport) -> None:
        if self.effective_role != "locked_final_test" or self.quarantine_reasons:
            raise ValueError("final-test independence was invalidated; obtain a fresh release")
        _require_report(release, report)
        if (
            report.role != "locked_final_test"
            or report.dataset_release_id != self.dataset.release_id
        ):
            raise ValueError("qualification requires the declared locked final test")
        if not any(
            item.selection.release_sha256 == record_sha256(release)
            and item.report_json == canonical_record(report)
            for item in self.assessments
        ):
            raise ValueError("missing recorded final-test exposure for the selected release")
