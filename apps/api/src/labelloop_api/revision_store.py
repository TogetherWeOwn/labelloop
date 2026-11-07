"""Expected-revision, append-only review writes; never update historical rows."""

from typing import Any

from sqlalchemy import Connection, insert, select, text

from labelloop_core.domain import AnnotationRevision, DatasetObject
from labelloop_core.geometry import Box

from .repository import source_uuid
from .schema import annotation_revision, review_event, source_asset


def append_review(
    connection: Connection,
    *,
    asset_id: str,
    actor_kind: str,
    actor_id: str,
    expected_revision: int,
    approval_state: str,
    review_tier: str,
    scope_completeness: str,
    objects: list[dict[str, Any]],
    training_eligible: bool = False,
    gold_eligible: bool = False,
) -> int:
    if actor_kind not in {"human", "model", "system"} or expected_revision < 1:
        raise ValueError("invalid actor or expected revision")
    if actor_kind != "human" and (
        approval_state == "human_accepted" or review_tier in {"human_reviewed", "adjudicated"}
    ):
        raise ValueError("model or system output cannot assert human approval")
    if actor_kind != "human" and gold_eligible:
        raise ValueError("gold eligibility requires authenticated adjudication")
    if actor_kind == "model" and training_eligible:
        raise ValueError("model output cannot assert eligibility; a qualified system policy must")
    asset_uuid = source_uuid(connection, asset_id)
    with connection.begin_nested():
        connection.execute(
            text("SELECT id FROM source_asset WHERE id = :asset_id FOR UPDATE"),
            {"asset_id": asset_uuid},
        ).one()
        asset = connection.execute(
            select(source_asset.c.width, source_asset.c.height).where(
                source_asset.c.id == asset_uuid
            )
        ).one()
        current = (
            connection.execute(
                text(
                    "SELECT revision, actor_kind, approval_state, review_tier, objects"
                    " FROM annotation_revision WHERE asset_id = :asset_id"
                    " ORDER BY revision DESC LIMIT 1 FOR UPDATE"
                ),
                {"asset_id": asset_uuid},
            )
            .mappings()
            .one()
        )
        if current["revision"] != expected_revision:
            raise ValueError("stale expected annotation revision")
        if actor_kind != "human" and (
            current["review_tier"] == "adjudicated"
            or current.get("approval_state") == "human_accepted"
        ):
            raise ValueError("late model output cannot supersede human labels")

        context = (
            connection.execute(
                text(
                    "SELECT ontology_release.class_map, project.access_policy,"
                    " completeness_assessment.scope_completeness"
                    " AS assessed_completeness, completeness_assessment.output_truncated,"
                    " completeness_assessment.unresolved_regions"
                    " FROM source_asset"
                    " JOIN project ON project.id = source_asset.project_id"
                    " JOIN ontology_release ON ontology_release.project_id = "
                    "source_asset.project_id"
                    " LEFT JOIN completeness_assessment ON completeness_assessment.asset_id = "
                    "source_asset.id AND completeness_assessment.revision = :expected_revision"
                    " WHERE source_asset.id = :asset_id"
                    " ORDER BY ontology_release.version DESC LIMIT 1"
                ),
                {"asset_id": asset_uuid, "expected_revision": expected_revision},
            )
            .mappings()
            .one()
        )
        allowed_classes = set(context["class_map"] or {})
        if (training_eligible or gold_eligible) and actor_kind == "system":
            qualification = (context["access_policy"] or {}).get("auto_accept_qualification", {})
            if qualification.get("id") != actor_id or approval_state != "auto_accepted":
                raise ValueError("system eligibility requires its qualified auto-accept policy")
        try:
            revision = AnnotationRevision(
                expected_revision + 1,
                tuple(
                    DatasetObject(
                        object_id=item["object_id"],
                        class_id=item["class_id"],
                        box=Box(*item["box"]),
                        label_state=item.get("label_state", "identified"),
                        attributes=item.get("attributes", {}),
                        occluded=item.get("occluded", False),
                        truncated_at_image_edge=item.get("truncated_at_image_edge", False),
                    )
                    for item in objects
                ),
                actor_kind,
                parent_revision=expected_revision,
                approval_state=approval_state,
                review_tier=review_tier,
                scope_completeness=scope_completeness,
                training_eligible=training_eligible,
                gold_eligible=gold_eligible,
            )
        except (KeyError, TypeError) as error:
            raise ValueError("revision objects have malformed identities or boxes") from error
        for item in revision.objects:
            if item.class_id not in allowed_classes:
                raise ValueError("annotation class is absent from the project ontology")
            item.box.within(asset[0], asset[1])

        objects_changed = current["objects"] != objects
        if objects_changed and any(
            (training_eligible, gold_eligible, scope_completeness == "exhaustive_for_scope")
        ):
            raise ValueError("changed object sets need a new authoritative completeness review")
        if (training_eligible or gold_eligible) and not objects_changed:
            if (
                context["assessed_completeness"] != "exhaustive_for_scope"
                or context["output_truncated"]
                or context["unresolved_regions"]
            ):
                raise ValueError(
                    "release eligibility needs a valid current completeness assessment"
                )
        next_completeness = "unreviewed" if objects_changed else scope_completeness
        values = {
            "asset_id": asset_uuid,
            "revision": expected_revision + 1,
            "parent_revision": expected_revision,
            "actor_kind": actor_kind,
            "actor_id": actor_id,
            "objects": objects,
            "approval_state": approval_state,
            "review_tier": review_tier,
            "scope_completeness": scope_completeness,
            "training_eligible": training_eligible,
            "gold_eligible": gold_eligible,
        }
        connection.execute(
            insert(review_event).values(
                asset_id=asset_uuid,
                actor_kind=actor_kind,
                actor_id=actor_id,
                decision="apply_revision",
                expected_revision=expected_revision,
                correction={"new_revision": expected_revision + 1},
                reason="expected-revision guarded",
            )
        )
        connection.execute(insert(annotation_revision).values(**values))
        connection.execute(
            text(
                """INSERT INTO completeness_assessment
                   (asset_id, revision, ontology_hash, scope_completeness, actor_kind,
                    actor_id, output_truncated, unresolved_regions)
                   SELECT asset_id, :next_revision, ontology_hash, :next_completeness, :actor_kind,
                    :actor_id, output_truncated, unresolved_regions
                   FROM completeness_assessment
                   WHERE asset_id = :asset_id AND revision = :expected_revision
                   ON CONFLICT (asset_id, revision) DO NOTHING"""
            ),
            {
                "asset_id": asset_uuid,
                "next_revision": expected_revision + 1,
                "expected_revision": expected_revision,
                "next_completeness": next_completeness,
                "actor_kind": actor_kind,
                "actor_id": actor_id,
            },
        )
    return expected_revision + 1
