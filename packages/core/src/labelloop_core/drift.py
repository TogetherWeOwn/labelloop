"""Scoped acceptance pauses; rollback alone never clears a drift incident."""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .evaluation import EvaluationReport
from .qualification import QualificationContext
from .qualification_data import ScopeCell, record_sha256, require_count, require_name, require_scope
from .training import require_digest

if TYPE_CHECKING:
    from .registry import ModelRelease


@dataclass(frozen=True, slots=True)
class DriftPause:
    pause_id: str
    scope: tuple[ScopeCell, ...]
    release_id: str
    release_sha256: str
    qualification_id: str
    reason: str
    resumed_by_qualification_id: str | None = None
    resumed_by_release_id: str | None = None

    def __post_init__(self) -> None:
        for value in (self.pause_id, self.release_id, self.qualification_id, self.reason):
            require_name(value)
        require_scope(self.scope)
        require_digest(self.release_sha256)
        if (self.resumed_by_qualification_id is None) != (self.resumed_by_release_id is None):
            raise ValueError("drift resumption requires qualification and release lineage")
        if self.resumed_by_qualification_id is not None:
            require_name(self.resumed_by_qualification_id)
            assert self.resumed_by_release_id is not None
            require_name(self.resumed_by_release_id)
            if self.resumed_by_qualification_id == self.qualification_id:
                raise ValueError("drift resumption requires new qualification")
            if self.resumed_by_release_id == self.release_id:
                raise ValueError("drift resumption requires a new qualified release")


@dataclass(frozen=True, slots=True)
class AcceptanceState:
    project_id: str
    recipe_id: str
    revision: int = 0
    pauses: tuple[DriftPause, ...] = ()

    def __post_init__(self) -> None:
        require_name(self.project_id)
        require_name(self.recipe_id)
        require_count(self.revision)
        if len({item.pause_id for item in self.pauses}) != len(self.pauses):
            raise ValueError("drift pause identities must be unique")

    def _expect(self, release: "ModelRelease", revision: int) -> None:
        if revision != self.revision:
            raise ValueError("stale acceptance revision")
        if (release.project_id, release.recipe_id) != (self.project_id, self.recipe_id):
            raise ValueError("acceptance state cannot cross project or recipe boundaries")

    def pause(
        self,
        release: "ModelRelease",
        *,
        pause_id: str,
        qualification_id: str,
        scope: tuple[ScopeCell, ...],
        reason: str,
        expected_revision: int,
    ) -> "AcceptanceState":
        self._expect(release, expected_revision)
        if any(item.class_id not in release.class_ids for item in scope):
            raise ValueError("drift pause names a class outside this release")
        return replace(
            self,
            revision=self.revision + 1,
            pauses=(
                *self.pauses,
                DriftPause(
                    pause_id,
                    scope,
                    release.release_id,
                    record_sha256(release),
                    qualification_id,
                    reason,
                ),
            ),
        )

    def is_paused(self, scope: ScopeCell) -> bool:
        return any(
            scope in item.scope and item.resumed_by_qualification_id is None for item in self.pauses
        )

    def require_acceptance(
        self,
        release: "ModelRelease",
        report: EvaluationReport,
        context: QualificationContext,
        scope: ScopeCell,
    ) -> None:
        self._expect(release, self.revision)
        context.require_qualified(release, report)
        if scope not in context.evidence.scope:
            raise ValueError("automatic acceptance is outside qualified scope")
        if self.is_paused(scope):
            raise ValueError("automatic acceptance paused for affected class/source/condition")
        if any(
            scope in item.scope
            and (
                release.release_id == item.release_id
                or record_sha256(release) == item.release_sha256
                or context.evidence.qualification_id == item.qualification_id
            )
            for item in self.pauses
        ):
            raise ValueError("drift-invalidated release cannot regain acceptance by rollback")

    def resume(
        self,
        release: "ModelRelease",
        report: EvaluationReport,
        context: QualificationContext,
        *,
        pause_ids: tuple[str, ...],
        expected_revision: int,
    ) -> "AcceptanceState":
        self._expect(release, expected_revision)
        if not pause_ids or len(set(pause_ids)) != len(pause_ids):
            raise ValueError("resume must name unique active drift pauses")
        context.require_qualified(release, report)
        active = {
            item.pause_id: item for item in self.pauses if item.resumed_by_qualification_id is None
        }
        for pause_id in pause_ids:
            if pause_id not in active:
                raise ValueError("unknown or already resumed drift pause")
            previous = active[pause_id]
            if (
                release.release_id == previous.release_id
                or record_sha256(release) == previous.release_sha256
                or context.evidence.qualification_id == previous.qualification_id
                or not set(previous.scope) <= set(context.evidence.scope)
            ):
                raise ValueError("resume requires a new qualified release covering affected scope")
        return replace(
            self,
            revision=self.revision + 1,
            pauses=tuple(
                replace(
                    item,
                    resumed_by_qualification_id=context.evidence.qualification_id,
                    resumed_by_release_id=release.release_id,
                )
                if item.pause_id in pause_ids
                else item
                for item in self.pauses
            ),
        )
