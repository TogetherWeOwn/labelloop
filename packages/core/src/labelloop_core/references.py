"""Explicit reference packs, versioned imports and distinguishable memory collections."""

import math
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from .domain import AnnotationRevision, ReferenceExample
from .evidence import EvidenceScope, require_accepted

Collection = Literal["anchor", "auto_expanded", "gold"]
ReferenceKind = Literal["positive", "confusion_negative"]


@dataclass(frozen=True, slots=True)
class GoldAdjudication:
    """Trusted audit projection; callers supply the complete prior human history."""

    evidence_id: str
    asset_id: str
    revision: int
    pixel_origin: Literal["real_image", "constructive_synthetic", "seeded_perturbation"]
    truth_origin: Literal[
        "human_blind", "public_labels", "constructive_synthetic", "seeded_perturbation"
    ]
    adjudicator_id: str
    annotator_ids: frozenset[str]
    reviewer_ids: frozenset[str]
    blind: bool

    def __post_init__(self) -> None:
        if (
            not self.evidence_id.strip()
            or not self.asset_id.strip()
            or type(self.revision) is not int
            or self.revision < 1
            or not self.adjudicator_id.strip()
            or any(not principal.strip() for principal in self.annotator_ids | self.reviewer_ids)
        ):
            raise ValueError("gold adjudication requires identified audit evidence and principals")
        if self.pixel_origin != "real_image" or self.truth_origin != "human_blind":
            raise ValueError("gold requires real-image human truth, never synthetic or public labels")
        if self.blind is not True or self.adjudicator_id in self.annotator_ids | self.reviewer_ids:
            raise ValueError("gold requires blind adjudication distinct from every prior participant")


@dataclass(frozen=True, slots=True)
class MemoryExample:
    reference: ReferenceExample
    revision: AnnotationRevision
    collection: Collection
    teacher_generation: str | None
    scene: str
    cluster_id: str
    validity_tags: frozenset[str]
    kind: ReferenceKind
    utility: float
    gold_adjudication: GoldAdjudication | None = None

    def __post_init__(self) -> None:
        if self.collection not in {"anchor", "auto_expanded", "gold"}:
            raise ValueError("memory collection must remain distinguishable")
        if self.kind not in {"positive", "confusion_negative"}:
            raise ValueError("unknown reference kind")
        if not self.scene or not self.cluster_id or not self.validity_tags:
            raise ValueError("memory needs scene, diversity and validity evidence")
        if not math.isfinite(self.utility) or not 0 <= self.utility <= 1:
            raise ValueError("reference utility must be finite and in [0,1]")
        if self.collection == "auto_expanded":
            if not self.teacher_generation:
                raise ValueError("automatic expansions must retain their teacher generation")
            if self.revision.approval_state != "auto_accepted":
                raise ValueError("automatic expansions must retain machine acceptance provenance")
        elif self.revision.review_tier not in {"human_reviewed", "adjudicated"}:
            raise ValueError("anchor and gold collections require independent human review")
        if self.collection == "gold":
            if not self.revision.gold_eligible:
                raise ValueError("gold collection requires adjudicated gold eligibility")
            if self.gold_adjudication is None:
                raise ValueError("gold collection requires independent adjudication provenance")
            if (
                self.gold_adjudication.asset_id != self.reference.asset_id
                or self.gold_adjudication.revision != self.revision.revision
            ):
                raise ValueError("gold adjudication must bind the exact source and revision")
        else:
            require_accepted(self.revision)


@dataclass(frozen=True, slots=True)
class ReferencePack:
    pack_id: str
    version: str
    scope: EvidenceScope
    examples: tuple[MemoryExample, ...]

    def __post_init__(self) -> None:
        if not self.pack_id or not self.version:
            raise ValueError("reference packs must be explicitly versioned")
        ids = [item.reference.reference_id for item in self.examples]
        if len(set(ids)) != len(ids):
            raise ValueError("reference identities must be unique in a pack")
        for item in self.examples:
            ref = item.reference
            if not all((ref.reference_id, ref.evidence_provenance, ref.validity_scope)):
                raise ValueError("reference provenance and validity scope are required")
            if ref.class_id not in self.scope.ontology.class_map:
                raise ValueError("reference class is absent from its pack ontology")
            self.scope.validate_revision(item.revision)
            root = self.scope.root_for(ref.asset_id)
            source = next(asset for asset in self.scope.sources if asset.asset_id == root)
            if root != ref.asset_id:
                raise ValueError("reference crops must point to canonical source pixels")
            if ref.crop is not None:
                ref.crop.within(source.width, source.height)
            for obj in item.revision.objects:
                obj.box.within(source.width, source.height)
            matching = [
                obj
                for obj in item.revision.objects
                if obj.class_id == ref.class_id
                and (ref.crop is None or obj.box.intersect(ref.crop) == obj.box)
            ]
            if item.kind == "positive" and not matching:
                raise ValueError(
                    "positive references need a complete labeled class instance in crop"
                )
            if item.collection == "gold":
                if self.scope.assignment.group_ids[root] not in self.scope.evaluation_groups():
                    raise ValueError(
                        "gold enrollment requires authoritative source-group quarantine"
                    )
            else:
                self.scope.require_training_source(ref.asset_id)


@dataclass(frozen=True, slots=True)
class ReferenceImport:
    import_id: str
    version: str
    source_project_id: str
    pack_id: str
    pack_version: str
    target_project_id: str
    target_ontology_id: str
    target_ontology_version: int
    class_mapping: dict[str, str]
    approved_by: str

    def __post_init__(self) -> None:
        if (
            not all(
                (
                    self.import_id,
                    self.version,
                    self.source_project_id,
                    self.pack_id,
                    self.pack_version,
                    self.target_project_id,
                    self.target_ontology_id,
                    self.approved_by,
                )
            )
            or not self.class_mapping
        ):
            raise ValueError("cross-project imports need explicit versioned approval and mapping")


@dataclass(frozen=True, slots=True)
class ReferenceQuery:
    class_ids: frozenset[str]
    validity_scope: str
    required_tags: frozenset[str]
    max_items: int
    max_per_class: int
    max_per_cluster: int

    def __post_init__(self) -> None:
        if not self.class_ids or not self.validity_scope or not self.required_tags:
            raise ValueError("retrieval requires explicit classes and validity constraints")
        if any(
            type(v) is not int or v < 1
            for v in (self.max_items, self.max_per_class, self.max_per_cluster)
        ):
            raise ValueError("retrieval budgets must be positive integers")


@dataclass(frozen=True, slots=True)
class RetrievedReference:
    pack_id: str
    pack_version: str
    reference_id: str
    target_class_id: str
    kind: ReferenceKind
    import_id: str | None
    import_version: str | None


def retrieve_references(
    target: EvidenceScope,
    packs: tuple[ReferencePack, ...],
    query: ReferenceQuery,
    imports: tuple[ReferenceImport, ...] = (),
) -> tuple[RetrievedReference, ...]:
    """Only explicitly enrolled, approved evidence is eligible; gold is never retrieved.

    Callers authorize access to all supplied scopes and approve import records.
    These pure records are not authentication or pack persistence mechanisms.
    """
    if not query.class_ids <= target.ontology.class_map.keys():
        raise ValueError("retrieval query names an unknown target class")
    identities = [(pack.scope.ontology.project_id, pack.pack_id, pack.version) for pack in packs]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate reference packs")
    pool: list[tuple[MemoryExample, ReferencePack, str, ReferenceImport | None]] = []
    for pack in packs:
        source_ontology = pack.scope.ontology
        imported: ReferenceImport | None = None
        mapping = {key: key for key in source_ontology.class_map}
        if source_ontology.project_id == target.ontology.project_id:
            if source_ontology != target.ontology or pack.scope != target:
                raise ValueError("local reference evidence must use the pinned current scope")
        else:
            matches = [
                record
                for record in imports
                if record.source_project_id == source_ontology.project_id
                and record.pack_id == pack.pack_id
                and record.pack_version == pack.version
                and record.target_project_id == target.ontology.project_id
                and record.target_ontology_id == target.ontology.ontology_id
                and record.target_ontology_version == target.ontology.version
            ]
            if len(matches) != 1:
                raise ValueError("cross-project reuse requires one exact versioned import")
            imported = matches[0]
            mapping = imported.class_mapping
            if set(mapping) != set(source_ontology.class_map) or not set(mapping.values()) <= set(
                target.ontology.class_map
            ):
                raise ValueError("import must explicitly map the complete source catalog")
        for item in pack.examples:
            ref = item.reference
            if item.collection == "gold":
                continue
            source = pack.scope.require_training_source(ref.asset_id)
            if source.sha256 in target.evaluation_hashes():
                raise ValueError("imported reference collides with target evaluation evidence")
            if not ref.approved:
                continue
            if (
                mapping[ref.class_id] in query.class_ids
                and ref.validity_scope == query.validity_scope
                and query.required_tags <= item.validity_tags
            ):
                pool.append((item, pack, mapping[ref.class_id], imported))
    pool.sort(
        key=lambda row: (
            -row[0].utility,
            row[1].scope.ontology.project_id,
            row[1].pack_id,
            row[1].version,
            row[0].reference.reference_id,
        )
    )
    if len(query.class_ids) > query.max_items:
        raise ValueError("insufficient item budget for requested class coverage")
    by_class: dict[str, list[int]] = {class_id: [] for class_id in sorted(query.class_ids)}
    for index, row in enumerate(pool):
        by_class[row[2]].append(index)
    cluster_members: dict[tuple[str, str], list[str]] = {}
    reservations: dict[str, int] = {}

    def cluster_for(index: int) -> tuple[str, str]:
        item, pack, _, _ = pool[index]
        return pack.scope.ontology.project_id, item.cluster_id

    for root_class in sorted(query.class_ids):
        pending = [root_class]
        visited = {root_class}
        seen_clusters: set[tuple[str, str]] = set()
        previous: dict[str, tuple[str, int]] = {}
        found: tuple[str, int] | None = None
        for class_id in pending:
            for index in by_class[class_id]:
                cluster = cluster_for(index)
                if cluster in seen_clusters:
                    continue
                seen_clusters.add(cluster)
                members = cluster_members.setdefault(cluster, [])
                if len(members) < query.max_per_cluster:
                    found = class_id, index
                    break
                for member in members:
                    if member not in visited:
                        visited.add(member)
                        previous[member] = class_id, index
                        pending.append(member)
            if found is not None:
                break
        if found is None:
            raise ValueError("insufficient approved reference coverage within cluster budgets")
        class_id, index = found
        while True:
            if class_id in reservations:
                cluster_members[cluster_for(reservations[class_id])].remove(class_id)
            reservations[class_id] = index
            cluster_members[cluster_for(index)].append(class_id)
            if class_id == root_class:
                break
            class_id, index = previous[class_id]

    reserved = set(reservations.values())
    indices = sorted(reserved) + [index for index in range(len(pool)) if index not in reserved]
    classes: Counter[str] = Counter()
    clusters: Counter[tuple[str, str]] = Counter()
    seen_sources: set[tuple[str, str, str]] = set()
    result: list[RetrievedReference] = []
    for index in indices:
        item, pack, class_id, imported = pool[index]
        cluster = (pack.scope.ontology.project_id, item.cluster_id)
        identity = (pack.scope.ontology.project_id, item.reference.asset_id, class_id)
        if (
            identity in seen_sources
            or classes[class_id] >= query.max_per_class
            or clusters[cluster] >= query.max_per_cluster
        ):
            continue
        result.append(
            RetrievedReference(
                pack.pack_id,
                pack.version,
                item.reference.reference_id,
                class_id,
                item.kind,
                imported.import_id if imported else None,
                imported.version if imported else None,
            )
        )
        seen_sources.add(identity)
        classes[class_id] += 1
        clusters[cluster] += 1
        if len(result) == query.max_items:
            break
    if not result or set(query.class_ids) - {item.target_class_id for item in result}:
        raise ValueError("insufficient approved reference coverage within retrieval budgets")
    return tuple(result)


@dataclass(frozen=True, slots=True)
class CollectionDistribution:
    collection: Collection
    examples: int
    class_counts: dict[str, int]
    teacher_counts: dict[str, int]
    scene_counts: dict[str, int]
    scale_counts: dict[str, int]
    occluded_objects: int


def collection_distributions(
    pack: ReferencePack, *, small_max_px: float, medium_max_px: float
) -> tuple[CollectionDistribution, ...]:
    if not 0 < small_max_px < medium_max_px < math.inf:
        raise ValueError("source-pixel size distribution thresholds must be finite and ordered")
    result: list[CollectionDistribution] = []
    collections: tuple[Collection, ...] = ("anchor", "auto_expanded", "gold")
    for collection in collections:
        examples = [item for item in pack.examples if item.collection == collection]
        objects = [obj for item in examples for obj in item.revision.objects]
        scales: Counter[str] = Counter()
        for obj in objects:
            side = min(obj.box.x_max - obj.box.x_min, obj.box.y_max - obj.box.y_min)
            scales[
                "small" if side <= small_max_px else "medium" if side <= medium_max_px else "large"
            ] += 1
        result.append(
            CollectionDistribution(
                collection,
                len(examples),
                dict(sorted(Counter(obj.class_id for obj in objects).items())),
                dict(
                    sorted(
                        Counter(item.teacher_generation or "curated" for item in examples).items()
                    )
                ),
                dict(sorted(Counter(item.scene for item in examples).items())),
                dict(sorted(scales.items())),
                sum(obj.occluded for obj in objects),
            )
        )
    return tuple(result)
