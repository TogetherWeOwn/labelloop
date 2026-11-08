"""Shared source-bound gates for selection, training collections and references."""

from dataclasses import dataclass, field

from .domain import AnnotationRevision, OntologyRelease, SourceAsset, SourceSplitAssignment


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceAnnotationRevision(AnnotationRevision):
    """Canonical revision projected with its authoritative persistence identity."""

    asset_id: str
    project_id: str

    def __post_init__(self) -> None:
        AnnotationRevision.__post_init__(self)
        if not self.asset_id.strip() or not self.project_id.strip():
            raise ValueError("revision source and project identities are required")


def require_revision_source(revision: AnnotationRevision, asset_id: str) -> None:
    if not isinstance(revision, SourceAnnotationRevision):
        raise ValueError("evidence requires a source-bound revision")
    if revision.asset_id != asset_id:
        raise ValueError("revision source differs from the evidence source")


@dataclass(frozen=True, slots=True)
class EvidenceScope:
    ontology: OntologyRelease
    assignment: SourceSplitAssignment
    sources: tuple[SourceAsset, ...]
    derivative_sources: dict[str, str] = field(default_factory=dict)
    evaluation_asset_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        assets = {asset.asset_id: asset for asset in self.sources}
        if not assets or len(assets) != len(self.sources):
            raise ValueError("evidence requires unique canonical source IDs")
        if not self.assignment.locked or not self.assignment.assignment_version:
            raise ValueError("evidence requires a versioned locked split assignment")
        if set(assets) != set(self.assignment.asset_splits) or set(assets) != set(
            self.assignment.group_ids
        ):
            raise ValueError("evidence must include the complete assigned source inventory")
        if not self.evaluation_asset_ids <= assets.keys():
            raise ValueError("evaluation membership must identify canonical sources")
        hashes: dict[str, str] = {}
        for asset in self.sources:
            if asset.project_id != self.ontology.project_id:
                raise ValueError("evidence project does not match ontology")
            group = self.assignment.group_ids[asset.asset_id]
            if not group or (asset.source_group is not None and asset.source_group != group):
                raise ValueError("source group disagrees with the locked assignment")
            if self.assignment.split_for(asset.asset_id) not in {"train", "val", "test"}:
                raise ValueError("unrecognized evidence split")
            previous = hashes.setdefault(asset.sha256, group)
            if previous != group:
                raise ValueError("exact duplicates must share a source group")
        if assets.keys() & self.derivative_sources.keys():
            raise ValueError("a derivative cannot masquerade as a canonical source")
        for derivative in self.derivative_sources:
            self.root_for(derivative)

    def root_for(self, asset_id: str) -> str:
        seen: set[str] = set()
        while asset_id in self.derivative_sources:
            if asset_id in seen:
                raise ValueError("derivative lineage must be acyclic")
            seen.add(asset_id)
            asset_id = self.derivative_sources[asset_id]
        if asset_id not in self.assignment.asset_splits:
            raise ValueError("missing canonical source evidence")
        return asset_id

    def evaluation_groups(self) -> frozenset[str]:
        return frozenset(
            self.assignment.group_ids[asset_id]
            for asset_id in self.assignment.asset_splits
            if self.assignment.split_for(asset_id) != "train"
            or asset_id in self.evaluation_asset_ids
        )

    def evaluation_hashes(self) -> frozenset[str]:
        groups = self.evaluation_groups()
        return frozenset(
            asset.sha256
            for asset in self.sources
            if self.assignment.group_ids[asset.asset_id] in groups
        )

    def require_training_source(self, asset_id: str) -> SourceAsset:
        root = self.root_for(asset_id)
        if root != asset_id:
            raise ValueError("derivatives are not canonical training/reference evidence")
        if self.assignment.group_ids[root] in self.evaluation_groups():
            raise ValueError("evaluation source groups cannot enter training or retrieval")
        return next(asset for asset in self.sources if asset.asset_id == root)

    def validate_revision(self, revision: SourceAnnotationRevision) -> None:
        if revision.project_id != self.ontology.project_id:
            raise ValueError("revision project differs from the pinned evidence project")
        if any(obj.class_id not in self.ontology.class_map for obj in revision.objects):
            raise ValueError("evidence class is absent from the pinned ontology")


def require_accepted(revision: AnnotationRevision) -> None:
    if not revision.training_eligible or revision.gold_eligible:
        raise ValueError("reference/training evidence needs accepted non-gold eligibility")
    if revision.approval_state not in {"auto_accepted", "human_accepted"}:
        raise ValueError("predictions are not approved reference additions")
