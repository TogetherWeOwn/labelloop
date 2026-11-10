"""Bounded first assignment publication, not regrouping or project publication.

The canonical mutation caller holds the project lock and owns the transaction.
Existing assignment history and unverified borderline evidence refuse: no holdout
can be laundered and no unreviewed similarity band is silently separated or merged.
The opt-in facade supplies an explicit trusted reader; this is no admission grant.
"""

from collections import Counter
from dataclasses import asdict, dataclass, replace
from importlib.metadata import version as library_version

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import insert, select, update

from labelloop_compiler.ingest import MAX_UPLOAD_BYTES, imaging_toolchain
from labelloop_core.domain import DatasetObject, SourceAsset
from labelloop_core.geometry import Box
from labelloop_core.grouping import (
    ALGORITHM,
    GroupingThresholds,
    assign_splits,
    group_sources,
)

from .management_canonical import CanonicalManagementMutations
from .management_compile_worker import CompilationObjects
from .management_contracts import InitialSourceGrouping, ManagementError, Resource, SourceMetadata
from .management_grouping_evidence import collect_source_grouping_evidence
from .management_ontology import validate_ontology
from .management_ports import ManagementFailure
from .management_setup import draft_sha256
from .schema import (
    annotation_revision,
    borderline_review,
    canonical_event,
    ontology_release,
    project,
    reference_example,
    similarity_relation,
    source_asset,
    source_split_assignment,
)

MAX_SOURCES = 256
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_RELATIONS = 65536


def reject(code):
    raise ManagementFailure(ManagementError(detail=code))


def declared_metadata(evidence, allowed_keys):
    # Absence is the historical pre-metadata receipt, not a fabricated observation.
    try:
        values = TypeAdapter(SourceMetadata).validate_python(
            evidence.get("source_metadata", {}), strict=True
        )
    except ValidationError:
        reject("missing_evidence")
    if not values.keys() <= set(allowed_keys):
        reject("missing_evidence")
    return values


def constrained_evidence(evidence, declarations, edges, *, reviewed=()):
    """Feed conservative components back to core's unchanged split allocator.

    Core owns visual relations and split stratification. A synthetic internal
    session key encodes the transitive closure of its groups plus canonical
    declarations/persisted edges; it is never stored as observed source metadata.
    """
    groups, relations, reviews = group_sources(evidence, GroupingThresholds())
    if reviews != list(reviewed):
        reject("missing_evidence")
    if set(declarations) != set(groups) or any(not value for value in declarations.values()):
        reject("missing_evidence")
    parents = {key: key for key in groups}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    def union(left, right):
        if left not in parents or right not in parents or left == right:
            reject("missing_evidence")
        first, second = sorted((root(left), root(right)))
        parents[second] = first

    for values in (groups, declarations):
        first_by_value = {}
        for key, value in sorted(values.items()):
            if value in first_by_value:
                union(first_by_value[value], key)
            else:
                first_by_value[value] = key
    for left, right in edges:
        union(left, right)
    return [replace(row, metadata={"session": root(row.asset_id)}) for row in evidence], relations


def prepare_initial_grouping(
    connection, project_row, workspace_id, store, payload, *, allow_resolved_pairs=False
):
    # Import at the composition edge; this loader shares the canonical error helper.
    from .management_project_reads import source_policy_draft

    ontology = connection.execute(
        select(ontology_release)
        .where(ontology_release.c.project_id == project_row.id)
        .order_by(ontology_release.c.version.desc())
        .limit(1)
    ).one_or_none()
    if ontology is None or ontology.ontology_key != payload.ontology_id:
        reject("revision_conflict")
    try:
        validate_ontology(ontology)
    except (TypeError, ValueError):
        reject("missing_evidence")
    draft = source_policy_draft(connection, project_row, workspace_id)
    if draft is None or "management_class_catalog_draft" in project_row.access_policy:
        reject("missing_evidence")
    if draft_sha256(draft.model_dump(mode="json")) != payload.source_policy_sha256:
        reject("revision_conflict")
    assets = connection.execute(
        select(source_asset)
        .where(source_asset.c.project_id == project_row.id)
        .order_by(source_asset.c.id)
        .limit(MAX_SOURCES + 1)
        .with_for_update()
    ).all()
    if len(assets) > MAX_SOURCES:
        reject("management_unavailable")
    by_key = {row.asset_key: row for row in assets}
    if set(payload.annotation_revisions) != set(by_key):
        reject("revision_conflict")
    ids = [row.id for row in assets]
    # No first-publication shortcut through any assignment/reference history.
    # Publication separately verifies resolved pairs against current discovery.
    for table, predicate in (
        (source_split_assignment, source_split_assignment.c.asset_id.in_(ids)),
        (reference_example, reference_example.c.asset_id.in_(ids)),
        (
            borderline_review,
            (
                borderline_review.c.left_asset_id.in_(ids)
                | borderline_review.c.right_asset_id.in_(ids)
            )
            & (borderline_review.c.review_state == "pending" if allow_resolved_pairs else True),
        ),
    ):
        if connection.execute(select(table.c.id).where(predicate).limit(1)).first() is not None:
            reject("missing_evidence")
    receipts = connection.execute(
        select(canonical_event)
        .where(
            canonical_event.c.aggregate_id == str(project_row.id),
            canonical_event.c.event_type == "management.ingest_asset",
            canonical_event.c.actor_kind == "human",
            canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
        )
        .limit(MAX_SOURCES + 1)
    ).all()
    declarations = {}
    metadata = {}
    for receipt in receipts:
        key = receipt.payload["result"]["id"]
        declared = receipt.payload["evidence"]["source_group"]
        if key not in by_key or key in declarations or type(declared) is not str or not declared:
            reject("missing_evidence")
        declarations[key] = declared
        metadata[key] = declared_metadata(receipt.payload["evidence"], draft.metadata_keys)
    if set(declarations) != set(by_key):
        reject("missing_evidence")
    current = {
        row.asset_id: row
        for row in connection.execute(
            select(annotation_revision)
            .where(annotation_revision.c.asset_id.in_(ids))
            .distinct(annotation_revision.c.asset_id)
            .order_by(annotation_revision.c.asset_id, annotation_revision.c.revision.desc())
        ).all()
    }
    counts = {}
    for asset in assets:
        revision = current.get(asset.id)
        if payload.annotation_revisions[asset.asset_key] != (revision.revision if revision else 0):
            reject("revision_conflict")
        objects = revision.objects if revision else []
        if not isinstance(objects, list) or len(objects) > 10000:
            reject("missing_evidence")
        observed = Counter()
        for obj in objects:
            try:
                parsed = DatasetObject(**{**obj, "box": Box(*obj["box"])})
            except (KeyError, TypeError, ValueError):
                reject("missing_evidence")
            if parsed.class_id not in ontology.class_map:
                reject("unsupported_classes")
            if parsed.label_state == "identified":
                observed[parsed.class_id] += 1
        counts[asset.asset_key] = dict(observed)
    persisted = connection.execute(
        select(similarity_relation)
        .where(
            similarity_relation.c.left_asset_id.in_(ids)
            | similarity_relation.c.right_asset_id.in_(ids)
        )
        .limit(MAX_RELATIONS + 1)
    ).all()
    if len(persisted) > MAX_RELATIONS:
        reject("management_unavailable")
    key_by_id = {row.id: row.asset_key for row in assets}
    edges = []
    for row in persisted:
        if (
            row.left_asset_id not in key_by_id
            or row.right_asset_id not in key_by_id
            or row.reason not in {"exact_hash", "phash", "embedding_cosine", "metadata"}
            or not row.algorithm_version
        ):
            reject("missing_evidence")
        edges.append((key_by_id[row.left_asset_id], key_by_id[row.right_asset_id]))
    evidence = []
    total = 0
    for asset in assets:
        data = store.read(asset.object_key)
        if type(data) is not bytes or len(data) > MAX_UPLOAD_BYTES:
            reject("missing_evidence")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            reject("management_unavailable")
        try:
            evidence.append(
                collect_source_grouping_evidence(
                    SourceAsset(
                        asset_id=asset.asset_key,
                        project_id=str(project_row.id),
                        sha256=asset.sha256,
                        object_key=asset.object_key,
                        width=asset.width,
                        height=asset.height,
                        exif_orientation=asset.exif_orientation,
                    ),
                    data,
                    metadata=metadata[asset.asset_key],
                    class_counts=counts[asset.asset_key],
                )
            )
        except ValueError:
            reject("missing_evidence")
    pins = {
        "ontology_id": ontology.ontology_key,
        "source_policy_sha256": payload.source_policy_sha256,
        "annotation_revisions": payload.annotation_revisions,
        "sources": [asdict(row) for row in sorted(evidence, key=lambda row: row.asset_id)],
        "source_groups": declarations,
        "persisted_relations": [
            {
                "id": str(row.id),
                "left_asset_id": key_by_id[row.left_asset_id],
                "right_asset_id": key_by_id[row.right_asset_id],
                "reason": row.reason,
                "evidence": row.evidence,
                "algorithm_version": row.algorithm_version,
                "embedding_model": row.embedding_model,
            }
            for row in sorted(persisted, key=lambda row: str(row.id))
        ],
        "constraint_adapter": "initial-canonical-closure-v1",
        "toolchain": {**imaging_toolchain(), "numpy": library_version("numpy")},
        "phash_algorithm": "phash-dct64-v1",
        "observed_metadata_keys": sorted({key for values in metadata.values() for key in values}),
    }
    return assets, evidence, declarations, edges, pins


def discover_initial_pairs(connection, project_row, workspace_id, store, payload):
    assets, evidence, _, _, pins = prepare_initial_grouping(
        connection, project_row, workspace_id, store, payload
    )
    _, _, reviews = group_sources(evidence, GroupingThresholds())
    by_key = {row.asset_key: row for row in assets}
    discovered = []
    for review in reviews:
        identifier = connection.execute(
            insert(borderline_review)
            .values(
                left_asset_id=by_key[review.left_asset_id].id,
                right_asset_id=by_key[review.right_asset_id].id,
                metric=review.metric,
                value=str(review.value),
                review_state="pending",
                algorithm_version=ALGORITHM,
            )
            .returning(borderline_review.c.id)
        ).scalar_one()
        discovered.append({"id": str(identifier), **asdict(review)})
    return {**pins, "thresholds": asdict(GroupingThresholds()), "pending_pairs": discovered}


def publish_initial_grouping(connection, project_row, workspace_id, store, payload):
    from .management_pair_consumption import consume_initial_pair_decisions

    assets, evidence, declarations, edges, pins = prepare_initial_grouping(
        connection, project_row, workspace_id, store, payload, allow_resolved_pairs=True
    )
    ids = [row.id for row in assets]
    pairs = connection.execute(
        select(borderline_review)
        .where(
            borderline_review.c.left_asset_id.in_(ids) | borderline_review.c.right_asset_id.in_(ids)
        )
        .order_by(borderline_review.c.id)
        .limit(MAX_RELATIONS + 1)
    ).all()
    receipts = connection.execute(
        select(canonical_event)
        .where(
            canonical_event.c.aggregate_id == str(project_row.id),
            canonical_event.c.event_type.in_(
                ["management.discover_initial_source_pairs", "management.resolve_source_pair"]
            ),
        )
        .limit(MAX_RELATIONS + 2)
    ).all()
    if len(pairs) > MAX_RELATIONS or len(receipts) > MAX_RELATIONS + 1:
        reject("management_unavailable")
    _, _, reviews = group_sources(evidence, GroupingThresholds())
    consumed = consume_initial_pair_decisions(
        project_row.id, workspace_id, assets, pins, pairs, receipts, reviews
    )
    if consumed:
        pins = {**pins, "reviewed_pairs": consumed}
    by_key = {row.asset_key: row for row in assets}
    constrained, relations = constrained_evidence(evidence, declarations, edges, reviewed=reviews)
    try:
        assignment = assign_splits(constrained)
    except ValueError:
        reject("missing_evidence")
    version = "initial-" + draft_sha256({**pins, "policy": assignment.assignment_version})
    thresholds = {**asdict(GroupingThresholds()), "validation_fraction": 0.25}
    for relation in relations:
        connection.execute(
            insert(similarity_relation).values(
                left_asset_id=by_key[relation.left_asset_id].id,
                right_asset_id=by_key[relation.right_asset_id].id,
                reason=relation.reason,
                evidence=relation.evidence,
                algorithm_version=ALGORITHM,
                embedding_model=None,
            )
        )
    for asset in assets:
        key = asset.asset_key
        group = assignment.group_ids[key]
        connection.execute(
            insert(source_split_assignment).values(
                assignment_version=version,
                asset_id=asset.id,
                group_id=group,
                split=assignment.asset_splits[key],
                seed=assignment.seed,
                algorithm=ALGORITHM,
                thresholds=thresholds,
                reason=assignment.reasons[group],
                locked=True,
            )
        )
    return {
        **pins,
        "assignment_version": version,
        "thresholds": thresholds,
        "seed": assignment.seed,
    }


@dataclass(frozen=True)
class CanonicalInitialGrouping:
    canonical: CanonicalManagementMutations
    objects: CompilationObjects

    def publish(self, actor, context, payload: InitialSourceGrouping) -> Resource:
        return self._mutate(
            actor, context, payload, "publish_initial_grouping", publish_initial_grouping
        )

    def discover(self, actor, context, payload: InitialSourceGrouping) -> Resource:
        return self._mutate(
            actor, context, payload, "discover_initial_source_pairs", discover_initial_pairs
        )

    def _mutate(self, actor, context, payload, operation, action) -> Resource:
        canonical = self.canonical
        canonical.authorize(actor, context, operation, context.target_id, payload)
        with canonical.engine.begin() as connection:
            canonical.begin_mutation(connection, context)
            owned = canonical.lock_project(connection, context.target_id)
            if replay := canonical.replay(connection, context):
                return replay
            canonical.check_project_revision(connection, owned.id, payload.expected_revision)
            evidence = action(connection, owned, canonical.workspace_id, self.objects, payload)
            policy = dict(owned.access_policy)
            policy["management_setup_approvals"] = {}
            policy.pop("auto_accept_qualification", None)
            connection.execute(
                update(project).where(project.c.id == owned.id).values(access_policy=policy)
            )
            result = Resource(
                id=str(owned.id),
                project_id=str(owned.id),
                kind="project",
                revision=payload.expected_revision + 1,
                state="draft",
            )
            canonical.record(
                connection, context, str(owned.id), payload.expected_revision, result, evidence
            )
            return result
