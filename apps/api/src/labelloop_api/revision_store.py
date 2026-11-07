"""Expected-revision, append-only review writes; never update historical rows."""

from typing import Any

from sqlalchemy import Connection, insert, text

from .repository import source_uuid
from .schema import annotation_revision, review_event


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
    if training_eligible or gold_eligible:
        if scope_completeness != "exhaustive_for_scope" or approval_state not in {
            "auto_accepted",
            "human_accepted",
        }:
            raise ValueError("eligible revisions require exhaustive, accepted annotations")
        if any(item.get("label_state", "identified") != "identified" for item in objects):
            raise ValueError("unresolved objects prevent training eligibility")
    if actor_kind != "human" and (training_eligible or gold_eligible):
        raise ValueError("non-human output cannot assert release eligibility")
    asset_uuid = source_uuid(connection, asset_id)
    with connection.begin_nested():
        connection.execute(
            text("SELECT id FROM source_asset WHERE id = :asset_id FOR UPDATE"),
            {"asset_id": asset_uuid},
        ).one()
        current = (
            connection.execute(
                text(
                    "SELECT revision, actor_kind, approval_state, review_tier"
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
            current["review_tier"] == "adjudicated" or current["approval_state"] == "human_accepted"
        ):
            raise ValueError("late model output cannot supersede human labels")
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
        # The revision is write-locked while completeness is migrated to unreviewed.
        connection.execute(
            text(
                """INSERT INTO completeness_assessment
                   (asset_id, revision, ontology_hash, scope_completeness, actor_kind,
                    actor_id, output_truncated, unresolved_regions)
                   SELECT asset_id, :next_revision, ontology_hash, 'unreviewed', :actor_kind,
                    :actor_id, output_truncated, unresolved_regions
                   FROM completeness_assessment
                   WHERE asset_id = :asset_id AND revision = :expected_revision
                   ON CONFLICT (asset_id, revision) DO NOTHING"""
            ),
            {
                "asset_id": asset_uuid,
                "next_revision": expected_revision + 1,
                "expected_revision": expected_revision,
                "actor_kind": actor_kind,
                "actor_id": actor_id,
            },
        )
    return expected_revision + 1
