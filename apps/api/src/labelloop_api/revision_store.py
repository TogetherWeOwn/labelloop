"""Expected-revision, append-only review writes; never update historical rows."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, insert, select, text

from labelloop_core.domain import AnnotationRevision, DatasetObject
from labelloop_core.geometry import Box
from labelloop_core.qualification import QualificationContext
from labelloop_core.qualification_data import ScopeCell, require_name, require_names, require_scope

from .acceptance_store import require_automatic_acceptance
from .automatic_review_store import automatic_acceptance_record
from .generated_label_store import proposal_teacher, record_generated_label_origin
from .qualification_store import lock_project
from .repository import source_uuid
from .schema import annotation_revision, project, review_event, source_asset


@dataclass(frozen=True, slots=True)
class AutomaticReview:
    """Internal service inputs, never a request-body or model-output authorization.

    The service derives source/condition coverage and qualification from trusted
    producers. This port does not establish that provenance. Until those producers
    exist, HTTP callers must not construct this object from submitted claims.
    """

    recipe_key: str
    evaluation_key: str
    scope: tuple[ScopeCell, ...]
    qualification: QualificationContext

    def __post_init__(self) -> None:
        require_name(self.recipe_key)
        require_name(self.evaluation_key)
        require_scope(self.scope)


def completeness_context_hash(
    class_map: dict[str, int], classes: list[dict[str, Any]], scope: str
) -> str:
    """Bind an image completeness decision to the exact ontology and project scope."""
    encoded = json.dumps(
        {"class_map": class_map, "classes": classes, "scope": scope},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


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
    automatic: AutomaticReview | None = None,
    proposal_run_key: str | None = None,
    proposal_run_keys: tuple[str, ...] = (),
) -> int:
    """Append one authorized review, atomically retaining supplied proposal influence.

    The service must supply every contributing proposal run; omission cannot prove
    an annotation was made without a teacher. This is not an HTTP authorization port.
    """
    require_names(proposal_run_keys, allow_empty=True)
    if proposal_run_key is not None:
        if proposal_run_keys:
            raise ValueError("supply either one proposal run or a collection, not both")
        require_name(proposal_run_key)
        proposal_run_keys = (proposal_run_key,)
    if proposal_run_keys:
        require_name(actor_id)
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
    needs_qualification = approval_state == "auto_accepted" or (
        actor_kind == "system" and training_eligible
    )
    if needs_qualification:
        if actor_kind != "system" or approval_state != "auto_accepted" or automatic is None:
            raise ValueError("automatic acceptance requires current qualification from the service")
        require_name(actor_id)
    elif automatic is not None:
        raise ValueError("automatic qualification cannot authorize a non-automatic revision")
    asset_uuid = source_uuid(connection, asset_id)
    with connection.begin_nested():
        teacher_keys = set()
        if needs_qualification or proposal_run_keys:
            # Follow registry/audit lock order before locking the annotation asset.
            project_key = connection.execute(
                select(project.c.project_key)
                .join(source_asset, source_asset.c.project_id == project.c.id)
                .where(source_asset.c.id == asset_uuid)
            ).scalar_one()
            lock_project(connection, project_key)
        # Resolve every run even when several share a teacher; one invalid run vetoes all.
        for key in sorted(proposal_run_keys):
            teacher_keys.add(
                proposal_teacher(
                    connection,
                    project_key=project_key,
                    asset_key=asset_id,
                    expected_revision=expected_revision,
                    proposal_run_key=key,
                )
            )
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
                    "SELECT ontology_release.class_map, ontology_release.classes, project.scope,"
                    " project.access_policy, completeness_assessment.ontology_hash"
                    " AS assessed_ontology_hash, completeness_assessment.scope_completeness"
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
        if training_eligible or gold_eligible or scope_completeness == "exhaustive_for_scope":
            if (
                context["assessed_ontology_hash"]
                != completeness_context_hash(
                    context["class_map"], context["classes"], context["scope"]
                )
                or context["assessed_completeness"] != "exhaustive_for_scope"
                or context["output_truncated"]
                or context["unresolved_regions"]
            ):
                raise ValueError(
                    "exhaustive claims and release eligibility need a valid current completeness"
                    " assessment"
                )
        if needs_qualification:
            assert automatic is not None
            # Complete-image acceptance covers absent classes too, including negatives.
            if (
                not allowed_classes
                or {cell.class_id for cell in automatic.scope} != allowed_classes
            ):
                raise ValueError("automatic acceptance requires the complete ontology scope")
            for cell in automatic.scope:
                require_automatic_acceptance(
                    connection,
                    project_key=project_key,
                    recipe_key=automatic.recipe_key,
                    scope=cell,
                    evaluation_key=automatic.evaluation_key,
                    qualification=automatic.qualification,
                )
        next_completeness = (
            "unreviewed" if objects_changed else context["assessed_completeness"] or "unreviewed"
        )
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
        correction = {"new_revision": expected_revision + 1}
        if proposal_run_keys:
            correction["proposal_run_keys"] = sorted(proposal_run_keys)
        if automatic is not None:
            correction["automatic_acceptance"] = automatic_acceptance_record(
                project_key=project_key,
                asset_key=asset_id,
                revision=values,
                evaluation_key=automatic.evaluation_key,
                scope=automatic.scope,
                qualification=automatic.qualification,
            )
        connection.execute(
            insert(review_event).values(
                asset_id=asset_uuid,
                actor_kind=actor_kind,
                actor_id=actor_id,
                decision="apply_revision",
                expected_revision=expected_revision,
                correction=correction,
                reason="expected-revision guarded",
            )
        )
        connection.execute(insert(annotation_revision).values(**values))
        for teacher_key in sorted(teacher_keys):
            record_generated_label_origin(
                connection,
                project_key=project_key,
                asset_key=asset_id,
                revision=expected_revision + 1,
                teacher_model_key=teacher_key,
                actor_id=actor_id,
            )
        connection.execute(
            text(
                """INSERT INTO completeness_assessment
                   (asset_id, revision, ontology_hash, scope_completeness, actor_kind,
                    actor_id, output_truncated, unresolved_regions, created_at)
                   SELECT asset_id, :next_revision, ontology_hash, :next_completeness, actor_kind,
                    actor_id, output_truncated, unresolved_regions, created_at
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
