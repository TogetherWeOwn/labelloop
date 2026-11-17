"""Identity-changing catalog migration over the ontology-migration leaf.

The leaf owns migration validation, the per-object review queue and the
old/new export index map. Publication itself stays in the canonical catalog
publisher: the leaf's ``publish_ontology_version`` writes a
``<project>:v<n>`` ontology key, which management ``Identifier`` pins cannot
carry (no colon), and it knows nothing of the stored publication pointer or
setup-approval invalidation that canonical reads verify. So this adapter
prepares a validated migration for that publisher and never writes rows.

A migration must change at least one established identity (remove, merge or
split); rename/add-only catalogs use the ordinary identity-preserving
publication. Objects of removed/merged/split classes are reported in the
receipt evidence as an explicit review queue - publishing never relabels
annotation history, and current-evidence checks bound to the old ontology
refuse afterwards until review produces new evidence.
"""

from dataclasses import asdict

from sqlalchemy import func, select

from labelloop_core.domain import ClassDefinition, OntologyRelease

from .management_contracts import ManagementError
from .management_ontology import canonical_classes
from .management_ports import ManagementFailure
from .ontology_migration import (
    ClassMigration,
    annotation_objects_needing_review,
    export_class_index_map,
    latest_annotation_objects,
    validate_catalog_migration,
)
from .schema import annotation_revision, source_asset

MAX_MIGRATION_ROWS = 10000
IDENTITY_CHANGING = frozenset({"remove", "merge", "split"})


def reject(code):
    raise ManagementFailure(ManagementError(detail=code))


def migration_steps(inputs):
    steps = []
    for item in inputs:
        if len(set(item.old_ids)) != len(item.old_ids) or len(set(item.new_ids)) != len(
            item.new_ids
        ):
            reject("unsupported_classes")
        steps.append(
            ClassMigration(old_ids=tuple(item.old_ids), new_ids=tuple(item.new_ids), kind=item.kind)
        )
    return tuple(steps)


def migration_class_map(old_map, new_ids):
    """Dense server-owned indices: survivors keep old order, new IDs append sorted."""
    ordered = sorted((sid for sid in new_ids if sid in old_map), key=old_map.__getitem__)
    ordered += sorted(sid for sid in new_ids if sid not in old_map)
    return {sid: index for index, sid in enumerate(ordered)}


def prepare_migration(project_id, ontology_row, catalog, steps):
    """Validate ``steps`` against the pinned ontology and the saved draft."""
    if not any(step.kind in IDENTITY_CHANGING for step in steps):
        reject("unsupported_classes")
    try:
        old = OntologyRelease(
            ontology_id=ontology_row.ontology_key,
            project_id=str(project_id),
            version=ontology_row.version,
            classes=tuple(ClassDefinition(**item) for item in ontology_row.classes),
            class_map=dict(ontology_row.class_map),
        )
        classes = canonical_classes(catalog)
        mapping = migration_class_map(old.class_map, [item["stable_id"] for item in classes])
        candidate = OntologyRelease(
            ontology_id="candidate",
            project_id=str(project_id),
            version=ontology_row.version + 1,
            classes=tuple(ClassDefinition(**item) for item in classes),
            class_map=mapping,
        )
        validate_catalog_migration(old, candidate, steps)
    except ManagementFailure:
        raise
    except (KeyError, TypeError, ValueError):
        reject("unsupported_classes")
    return mapping, export_class_index_map(old, candidate)


def bounded_review_queue(connection, project_id, steps):
    """Explicit per-object review queue; unbounded history refuses, never truncates."""
    rows = connection.execute(
        select(func.count())
        .select_from(annotation_revision)
        .join(source_asset, annotation_revision.c.asset_id == source_asset.c.id)
        .where(source_asset.c.project_id == project_id)
    ).scalar_one()
    if rows > MAX_MIGRATION_ROWS:
        reject("management_unavailable")
    try:
        queue = annotation_objects_needing_review(
            latest_annotation_objects(connection, project_id=str(project_id)), steps
        )
    except (KeyError, TypeError, ValueError):
        reject("missing_evidence")
    if len(queue) > MAX_MIGRATION_ROWS:
        reject("management_unavailable")
    return [asdict(item) for item in queue]
