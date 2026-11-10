"""Internal anchor-pack preparation and current publication verification.

The caller owns authorization and the transaction. Read-only callers must use one
repeatable snapshot and disable source locking. Publication is historical evidence,
never retrieval registration or setup-release authority. No object I/O occurs here.
"""

import hashlib
import json
from collections import Counter
from dataclasses import asdict

from sqlalchemy import Connection, select

from labelloop_core.domain import (
    ClassDefinition,
    DatasetObject,
    OntologyRelease,
    ReferenceExample,
    SourceAsset,
    SourceSplitAssignment,
)
from labelloop_core.evidence import EvidenceScope, SourceAnnotationRevision
from labelloop_core.geometry import Box
from labelloop_core.references import MemoryExample, ReferencePack

from .management_contracts import AnchorPackEntry as AnchorPackEntry
from .management_contracts import AnchorPackPlan as AnchorPackPlan
from .management_contracts import AnchorPackPublish as AnchorPackPublish
from .management_contracts import ManagementError, Resource
from .management_details import AnchorPackRead
from .management_ports import ManagementFailure
from .management_references import current_reference_evidence, reference_context_sha256
from .management_release import revision_refusal
from .revision_store import completeness_context_hash
from .schema import annotation_revision, canonical_event, completeness_assessment, ontology_release


def pack_snapshot(pack: ReferencePack) -> tuple[dict, str]:
    """Bounded internal snapshot; never a public read or retrieval grant."""

    def encode(value):
        if isinstance(value, frozenset):
            return sorted(value)
        raise TypeError("unsupported pack value")

    try:
        raw = json.dumps(
            asdict(pack), sort_keys=True, separators=(",", ":"), allow_nan=False, default=encode
        ).encode()
        if len(raw) > 2_000_000:
            reject("management_unavailable")
        return json.loads(raw), hashlib.sha256(raw).hexdigest()
    except (TypeError, ValueError):
        reject()


def reject(code="missing_evidence"):
    raise ManagementFailure(ManagementError(detail=code))


def verify_anchor_receipt(receipt, project_row, workspace_id, pack: ReferencePack) -> None:
    """Compare a publication with freshly prepared evidence, not stored self-claims."""
    try:
        payload = receipt.payload
        evidence = payload["evidence"]
        result = Resource.model_validate(payload["result"])
        snapshot, digest = pack_snapshot(pack)
        saved = json.dumps(
            evidence["snapshot"], sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        if (
            len(saved) > 2_000_000
            or hashlib.sha256(saved).hexdigest() != digest
            or str(project_row.workspace_id) != str(workspace_id)
            or receipt.event_type != "management.publish_anchor_pack"
            or receipt.aggregate_id != str(project_row.id)
            or receipt.actor_kind != "human"
            or not receipt.actor_id
            or payload["workspace_id"] != str(workspace_id)
            or type(receipt.expected_revision) is not int
            or receipt.expected_revision < 1
            or type(payload["aggregate_revision"]) is not int
            or payload["aggregate_revision"] != receipt.expected_revision + 1
            or result.id != str(project_row.id)
            or result.project_id != str(project_row.id)
            or result.kind != "project"
            or result.state != "draft"
            or result.revision != payload["aggregate_revision"]
            or evidence["context_sha256"] != reference_context_sha256(project_row)
            or evidence["snapshot_sha256"] != digest
            or evidence["snapshot"] != snapshot
        ):
            reject()
    except (KeyError, TypeError, ValueError, AttributeError):
        reject()


def read_current_anchor_pack(
    connection: Connection,
    project_row,
    workspace_id,
    pack_id: str,
    version: str,
    *,
    lock_sources=False,
) -> ReferencePack:
    """Caller owns a read snapshot, or project lock plus source locks for mutation."""
    if str(project_row.workspace_id) != str(workspace_id):
        reject("not_found")
    required_isolation = "READ COMMITTED" if lock_sources else "REPEATABLE READ"
    if connection.get_isolation_level() != required_isolation:
        reject("management_unavailable")
    receipts = connection.execute(
        select(canonical_event)
        .where(
            canonical_event.c.aggregate_id == str(project_row.id),
            canonical_event.c.event_type == "management.publish_anchor_pack",
        )
        .limit(1001)
    ).all()
    if len(receipts) > 1000:
        reject("management_unavailable")
    try:
        selected = []
        identities = set()
        for receipt in receipts:
            plan = AnchorPackPlan.model_validate(receipt.payload["evidence"]["plan"])
            identity = (plan.pack_id, plan.version)
            if identity in identities:
                reject()
            identities.add(identity)
            if identity == (pack_id, version):
                selected.append((receipt, plan))
        if len(selected) != 1:
            reject()
        receipt, plan = selected[0]
        pack = prepare_anchor_pack(
            connection, project_row, workspace_id, plan, lock_sources=lock_sources
        )
        verify_anchor_receipt(receipt, project_row, workspace_id, pack)
        return pack
    except (KeyError, TypeError, ValueError):
        reject()


def anchor_pack_summary(pack: ReferencePack, project_revision: int) -> AnchorPackRead:
    """Project only safe facts from verified current evidence, never internal lineage."""
    _, digest = pack_snapshot(pack)
    return AnchorPackRead(
        project_id=pack.scope.ontology.project_id,
        project_revision=project_revision,
        pack_id=pack.pack_id,
        version=pack.version,
        snapshot_sha256=digest,
        ontology_id=pack.scope.ontology.ontology_id,
        assignment_version=pack.scope.assignment.assignment_version,
        examples_per_class=dict(
            sorted(Counter(item.reference.class_id for item in pack.examples).items())
        ),
        qualification="not_assessed",
    )


def assemble_anchor_pack(scope, references, revisions, plan: AnchorPackPlan) -> ReferencePack:
    """Use core source, human-review, positive-instance and holdout gates unchanged."""
    try:
        if (
            scope.ontology.ontology_id != plan.ontology_id
            or scope.assignment.assignment_version != plan.assignment_version
        ):
            reject("revision_conflict")
        by_id = {ref.id: ref for ref in references}
        examples = []
        for identifier, entry in sorted(plan.entries.items()):
            ref = by_id.get(identifier)
            if ref is None:
                reject("not_found")
            if ref.approval != "admin_approved":
                reject()
            revision = revisions.get(ref.asset_id)
            if revision is None or revision.revision != entry.annotation_revision:
                reject("revision_conflict")
            if any(not tag.strip() or len(tag) > 200 for tag in entry.validity_tags):
                reject("invalid_request")
            examples.append(
                MemoryExample(
                    reference=ReferenceExample(
                        reference_id=ref.id,
                        asset_id=ref.asset_id,
                        class_id=ref.class_id,
                        evidence_provenance=json.dumps(ref.model_dump(mode="json"), sort_keys=True),
                        approved=True,
                        validity_scope=ref.validity_scope,
                    ),
                    revision=revision,
                    collection="anchor",
                    teacher_generation=None,
                    scene=entry.scene,
                    cluster_id=entry.cluster_id,
                    validity_tags=frozenset(entry.validity_tags),
                    kind="positive",
                    utility=entry.utility,
                )
            )
        return ReferencePack(plan.pack_id, plan.version, scope, tuple(examples))
    except (KeyError, TypeError, ValueError):
        reject()


def prepare_anchor_pack(
    connection: Connection, project_row, workspace_id, plan: AnchorPackPlan, *, lock_sources=True
) -> ReferencePack:
    from .management_grouping import load_grouping

    if str(project_row.workspace_id) != str(workspace_id):
        reject("not_found")
    # Current receipt-backed approval is necessary, but never sufficient to turn
    # enrollment metadata or a verified negative into a positive memory example.
    references = current_reference_evidence(connection, project_row, workspace_id) or []
    by_id = {ref.id: ref for ref in references}
    if not plan.entries.keys() <= by_id.keys():
        reject("not_found")
    if any(by_id[key].approval != "admin_approved" for key in plan.entries):
        reject()
    ontology = connection.execute(
        select(ontology_release)
        .where(ontology_release.c.project_id == project_row.id)
        .order_by(ontology_release.c.version.desc())
        .limit(1)
    ).one_or_none()
    if ontology is None or ontology.ontology_key != plan.ontology_id:
        reject("revision_conflict")
    assets, by_asset, assignments, group_splits = load_grouping(
        connection, project_row, plan.assignment_version, lock_sources=lock_sources
    )
    selected_keys = {by_id[key].asset_id for key in plan.entries}
    selected_ids = [asset.id for asset in assets if asset.asset_key in selected_keys]
    latest = (
        select(annotation_revision)
        .where(annotation_revision.c.asset_id.in_(selected_ids))
        .distinct(annotation_revision.c.asset_id)
        .order_by(annotation_revision.c.asset_id, annotation_revision.c.revision.desc())
        .subquery()
    )
    rows = {row.asset_id: row for row in connection.execute(select(latest))}
    assessments = {
        row.asset_id: row
        for row in connection.execute(
            select(completeness_assessment).join(
                latest,
                (completeness_assessment.c.asset_id == latest.c.asset_id)
                & (completeness_assessment.c.revision == latest.c.revision),
            )
        )
    }
    context_hash = completeness_context_hash(
        ontology.class_map, ontology.classes, project_row.scope
    )
    revisions = {}
    try:
        scope = EvidenceScope(
            ontology=OntologyRelease(
                ontology_id=ontology.ontology_key,
                project_id=str(project_row.id),
                version=ontology.version,
                classes=tuple(ClassDefinition(**item) for item in ontology.classes),
                class_map=ontology.class_map,
            ),
            assignment=SourceSplitAssignment(
                assignment_version=plan.assignment_version,
                asset_splits={asset.asset_key: by_asset[asset.id].split for asset in assets},
                group_splits=group_splits,
                group_ids={asset.asset_key: by_asset[asset.id].group_id for asset in assets},
                seed=assignments[0].seed,
                algorithm=assignments[0].algorithm,
                reasons={asset.asset_key: by_asset[asset.id].reason for asset in assets},
            ),
            sources=tuple(
                SourceAsset(
                    asset_id=asset.asset_key,
                    project_id=str(project_row.id),
                    sha256=asset.sha256,
                    object_key=asset.object_key,
                    width=asset.width,
                    height=asset.height,
                    exif_orientation=asset.exif_orientation,
                    source_uri=asset.source_uri,
                    source_group=by_asset[asset.id].group_id,
                )
                for asset in assets
            ),
        )
        for asset in assets:
            if asset.asset_key not in selected_keys:
                continue
            row = rows.get(asset.id)
            if row is None:
                reject()
            if reason := revision_refusal(
                project_row, context_hash, row, assessments.get(asset.id)
            ):
                reject(reason)
            revisions[asset.asset_key] = SourceAnnotationRevision(
                asset_id=asset.asset_key,
                project_id=str(project_row.id),
                revision=row.revision,
                parent_revision=row.parent_revision,
                actor_kind=row.actor_kind,
                approval_state=row.approval_state,
                review_tier=row.review_tier,
                scope_completeness=row.scope_completeness,
                training_eligible=row.training_eligible,
                gold_eligible=row.gold_eligible,
                objects=tuple(
                    DatasetObject(
                        object_id=item["object_id"],
                        class_id=item["class_id"],
                        box=Box(*item["box"]),
                        label_state=item.get("label_state", "identified"),
                        attributes=item.get("attributes", {}),
                        occluded=item.get("occluded", False),
                        truncated_at_image_edge=item.get("truncated_at_image_edge", False),
                    )
                    for item in row.objects
                ),
            )
    except (KeyError, TypeError, ValueError):
        reject()
    return assemble_anchor_pack(scope, references, revisions, plan)
