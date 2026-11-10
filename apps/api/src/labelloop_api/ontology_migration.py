"""Versioned ontology migration leaf; no HTTP mounts, locks or audit writes.

Code-owned validation/publication of immutable catalog versions. Old
ontology_release rows and annotation_revision history are never rewritten:
the new version is appended, and completeness/setup/reference eligibility
becomes stale automatically because it is bound to the old ontology hash
(see revision_store.completeness_context_hash). Unmerged management_*
modules, authenticated principals and final source-integrator audit are
out of scope for this leaf; callers own the outer transaction and pass a
SQLAlchemy Connection into every DB helper.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

from sqlalchemy import Connection, insert, select
from sqlalchemy.exc import IntegrityError

from labelloop_core.domain import ClassDefinition, OntologyRelease

from .schema import annotation_revision, ontology_release

MigrationKind = Literal["retain", "rename", "add", "remove", "merge", "split"]
_KINDS = frozenset({"retain", "rename", "add", "remove", "merge", "split"})


@dataclass(frozen=True, slots=True)
class ClassMigration:
    """One typed old->new stable-ID mapping step.

    ``old_ids``/``new_ids`` are disjoint across steps and together must
    cover every stable ID of both catalogs. A 1->1 step with different IDs
    is never a valid kind: it would be an inferred relabel, so removals
    and additions must be declared as separate ``remove``/``add`` steps.
    """

    old_ids: tuple[str, ...]
    new_ids: tuple[str, ...]
    kind: MigrationKind


@dataclass(frozen=True, slots=True)
class MigrationReviewItem:
    asset_id: str
    revision: int
    object_id: str
    class_id: str
    reason: str


def _ontology_hash(classes: list[dict], class_map: dict[str, int]) -> str:
    encoded = json.dumps(
        {"class_map": class_map, "classes": classes},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_catalog_migration(
    old: OntologyRelease, new: OntologyRelease, steps: tuple[ClassMigration, ...]
) -> None:
    """Reject missing/ambiguous mappings; never infer identity from names."""
    if not steps:
        raise ValueError("ontology migration requires explicit class mappings")
    old_ids = {item.stable_id for item in old.classes}
    new_ids = {item.stable_id for item in new.classes}
    if old.project_id != new.project_id:
        raise ValueError("ontology migration must stay within one project")
    if new.version <= old.version:
        raise ValueError("new ontology version must exceed the current version")
    seen_old: list[str] = []
    seen_new: list[str] = []
    for step in steps:
        if step.kind not in _KINDS:
            raise ValueError(f"unknown migration kind: {step.kind!r}")
        if step.kind in {"retain", "rename"} and (
            tuple(step.old_ids) != tuple(step.new_ids) or len(step.old_ids) != 1
        ):
            raise ValueError(f"{step.kind} must map one stable ID to itself")
        if step.kind == "add" and (step.old_ids or len(step.new_ids) != 1):
            raise ValueError("add must map no old ID to one new ID")
        if step.kind == "remove" and (step.new_ids or len(step.old_ids) != 1):
            raise ValueError("remove must map one old ID to no new ID")
        if step.kind == "merge" and (len(step.old_ids) < 2 or len(step.new_ids) != 1):
            raise ValueError("merge must map two or more old IDs to one new ID")
        if step.kind == "split" and (len(step.old_ids) != 1 or len(step.new_ids) < 2):
            raise ValueError("split must map one old ID to two or more new IDs")
        seen_old.extend(step.old_ids)
        seen_new.extend(step.new_ids)
    if sorted(seen_old) != sorted(old_ids):
        raise ValueError("every old class must have exactly one explicit disposition")
    if sorted(seen_new) != sorted(new_ids):
        raise ValueError("every new class must have exactly one explicit disposition")
    if len(seen_old) != len(set(seen_old)) or len(seen_new) != len(set(seen_new)):
        raise ValueError("ambiguous mapping: a stable ID cannot appear in two steps")
    # A display-name change is a rename, not a relabel: identity is the
    # stable ID. Definitions may evolve, but retained IDs must stay known.
    for step in steps:
        if step.kind in {"retain", "rename"}:
            if step.old_ids[0] not in old_ids or step.new_ids[0] not in new_ids:
                raise ValueError("retained stable ID is absent from a catalog")


def new_ids_for(class_id: str, steps: tuple[ClassMigration, ...]) -> tuple[str, ...]:
    for step in steps:
        if class_id in step.old_ids:
            return step.new_ids
    return ()


def kind_for(class_id: str, steps: tuple[ClassMigration, ...]) -> MigrationKind | None:
    for step in steps:
        if class_id in step.old_ids:
            return step.kind
    return None


def export_class_index_map(
    old: OntologyRelease, new: OntologyRelease
) -> dict[str, dict[str, int | None]]:
    """Stable-ID -> old/new YOLO index, preserved for frozen export maps."""
    result: dict[str, dict[str, int | None]] = {}
    for item in sorted(old.classes, key=lambda c: old.class_map[c.stable_id]):
        target = item.stable_id if item.stable_id in new.class_map else None
        result[item.stable_id] = {
            "old_index": old.class_map[item.stable_id],
            "new_index": new.class_map[target] if target else None,
        }
    for item in new.classes:
        if item.stable_id not in result:
            result[item.stable_id] = {
                "old_index": None,
                "new_index": new.class_map[item.stable_id],
            }
    return result


def annotation_objects_needing_review(
    records: list[dict], steps: tuple[ClassMigration, ...]
) -> list[MigrationReviewItem]:
    """Per-object review queue. Retained/renamed objects need no relabel.

    Merge/split/remove objects cannot be remapped per-object by code: a
    split branch or merge survivor choice requires explicit review and
    must never become silent background.
    """
    items: list[MigrationReviewItem] = []
    for record in records:
        class_id = record["class_id"]
        kind = kind_for(class_id, steps)
        if kind in {"remove", "merge", "split"}:
            items.append(
                MigrationReviewItem(
                    asset_id=record["asset_id"],
                    revision=record["revision"],
                    object_id=record["object_id"],
                    class_id=class_id,
                    reason=f"class {class_id!r} was {kind}; explicit review required",
                )
            )
        elif kind is None:
            items.append(
                MigrationReviewItem(
                    asset_id=record["asset_id"],
                    revision=record["revision"],
                    object_id=record["object_id"],
                    class_id=class_id,
                    reason=f"class {class_id!r} has no mapping; explicit review required",
                )
            )
    return sorted(items, key=lambda i: (i.asset_id, i.revision, i.object_id))


def load_ontology(
    connection: Connection, *, project_id: str, version: int | None = None
) -> OntologyRelease:
    statement = select(ontology_release).where(
        ontology_release.c.project_id == project_id,
    )
    if version is not None:
        statement = statement.where(ontology_release.c.version == version)
    row = connection.execute(statement.order_by(ontology_release.c.version.desc())).mappings().one()
    classes = tuple(ClassDefinition(**item) for item in row["classes"])
    return OntologyRelease(
        ontology_id=str(row["id"]),
        project_id=project_id,
        version=row["version"],
        classes=classes,
        class_map=dict(row["class_map"]),
    )


def latest_annotation_objects(connection: Connection, *, project_id: str) -> list[dict]:
    """Latest-revision objects per asset for one project (review input only)."""
    rows = (
        connection.execute(
            select(
                annotation_revision.c.asset_id,
                annotation_revision.c.revision,
                annotation_revision.c.objects,
            )
            .where(annotation_revision.c.asset_id.is_not(None))
            .order_by(annotation_revision.c.revision.desc())
        )
        .mappings()
        .all()
    )
    seen: set[tuple[object, int]] = set()
    records: list[dict] = []
    for row in rows:
        key = (row["asset_id"], row["revision"])
        if key in seen:
            continue
        seen.add(key)
        for item in row["objects"] or []:
            records.append(
                {
                    "asset_id": str(row["asset_id"]),
                    "revision": row["revision"],
                    "object_id": item["object_id"],
                    "class_id": item["class_id"],
                }
            )
    return records


def publish_ontology_version(
    connection: Connection,
    *,
    project_id: str,
    new_version: int,
    new_classes: tuple[ClassDefinition, ...],
    new_class_map: dict[str, int],
    steps: tuple[ClassMigration, ...],
    expected_current_version: int,
) -> OntologyRelease:
    """Append the next immutable ontology version; old rows untouched.

    The current version is checked under a savepoint so a stale or
    conflicting publication rolls back without touching history.
    """
    try:
        with connection.begin_nested():
            current = load_ontology(connection, project_id=project_id)
            if current.version != expected_current_version:
                raise ValueError("stale ontology version")
            candidate = OntologyRelease(
                ontology_id=str(new_version),
                project_id=project_id,
                version=new_version,
                classes=new_classes,
                class_map=dict(new_class_map),
            )
            validate_catalog_migration(current, candidate, steps)
            project_uuid = connection.execute(
                select(ontology_release.c.project_id).where(
                    ontology_release.c.project_id == project_id
                )
            ).scalar_one()
            connection.execute(
                insert(ontology_release).values(
                    ontology_key=f"{project_id}:v{new_version}",
                    project_id=project_uuid,
                    version=new_version,
                    classes=[asdict(item) for item in new_classes],
                    class_map=dict(new_class_map),
                )
            )
    except IntegrityError as error:
        raise ValueError("ontology version already exists") from error
    return candidate
