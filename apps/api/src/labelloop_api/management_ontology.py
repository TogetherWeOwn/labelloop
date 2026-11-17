"""Immutable catalog versions; identity-changing steps use the migration adapter."""

from sqlalchemy import Connection, select

from labelloop_core.domain import ClassDefinition, OntologyRelease

from .management_contracts import ClassCatalogDraft, ManagementError
from .management_ports import ManagementFailure
from .schema import canonical_event, ontology_release


def canonical_classes(catalog: dict) -> list[dict]:
    return [
        {key: item[key] for key in ("stable_id", "name", "definition", "aliases", "parent_id")}
        for item in catalog["classes"]
    ]


def validate_ontology(row) -> None:
    OntologyRelease(
        ontology_id=row.ontology_key,
        project_id=str(row.project_id),
        version=row.version,
        classes=tuple(ClassDefinition(**item) for item in row.classes),
        class_map=row.class_map,
    )


def read_catalog_publication(connection: Connection, project_row, workspace_id):
    receipt = connection.execute(
        select(canonical_event)
        .where(
            canonical_event.c.aggregate_id == str(project_row.id),
            canonical_event.c.event_type == "management.publish_class_catalog",
            canonical_event.c.actor_kind == "human",
            canonical_event.c.payload["workspace_id"].as_string() == str(workspace_id),
        )
        .order_by(canonical_event.c.expected_revision.desc())
        .limit(1)
    ).one_or_none()
    stored = project_row.access_policy.get("management_class_catalog_publication")
    if receipt is None:
        if stored is not None:
            raise ManagementFailure(ManagementError(detail="missing_evidence"))
        return None
    evidence = receipt.payload["evidence"]
    ontology = connection.execute(
        select(ontology_release)
        .where(ontology_release.c.project_id == project_row.id)
        .order_by(ontology_release.c.version.desc())
        .limit(1)
    ).one_or_none()
    catalog = ClassCatalogDraft.model_validate(
        {**evidence["class_catalog"], "expected_revision": receipt.expected_revision}
    )
    if (
        stored
        != {
            "ontology_id": evidence["ontology_id"],
            "receipt_revision": receipt.expected_revision + 1,
        }
        or ontology is None
        or ontology.ontology_key != evidence["ontology_id"]
        or ontology.version != evidence["ontology_version"]
        or ontology.class_map != evidence["class_map"]
        or ontology.classes != canonical_classes(catalog.model_dump())
    ):
        raise ManagementFailure(ManagementError(detail="missing_evidence"))
    validate_ontology(ontology)
    return receipt
