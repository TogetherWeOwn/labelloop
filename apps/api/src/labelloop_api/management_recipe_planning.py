"""Editable ROI/recipe planning over the recipe-planning leaf.

Bound as the write-free ``readRecipePlan`` detail read through the shared
reader boundary; no default mounting and no writes at all. A plan revision
is a fresh read with a new crop: it always plans against current evidence
and returns the pins it used, so drift shows up as changed pins and digest
rather than being silently carried. The leaf owns plan geometry and the
deterministic digest; this integrator resolves the pins those plans are
checked against, because two leaf DB helpers cannot be consumed as-is:

* ``load_current_pins`` selects the current assignment by maximal
  ``created_at``. Canonical reads and freeze select it by grouping
  publication order (:func:`management_grouping.current_assignment_version`),
  so planning against the leaf loader could pin a version freeze will not
  use. Pins below come from the canonical helpers instead.
* ``freeze_recipe_plan`` publishes the ``preprocessing_recipe`` row. The
  compile worker already publishes that row for the code-resolved recipe
  inside its fenced publication transaction, so a second publisher here
  would split recipe authority. A plan therefore stays an editable,
  digest-bound proposal until a later binding hands it to that path; the
  per-asset crop remains a plan pin, exactly as the leaf records it.

Recipes are server-resolved by ID through the compiler; a caller can pick
an existing recipe and an in-bounds crop, never invent policy. Frozen
release previews stay on the existing read surface and are never plans.
"""

from dataclasses import asdict

from sqlalchemy import select

from labelloop_compiler.compiler import resolve_recipe
from labelloop_core.geometry import Crop, full_frame_transform

from .management_contracts import ManagementError
from .management_details import CropRead, RecipePlanRead
from .management_grouping import current_assignment_version
from .management_ontology import validate_ontology
from .management_ports import ManagementFailure
from .recipe_planning import (
    build_recipe_plan,
    ontology_release_hash,
    plan_digest,
    require_fresh,
    with_crop,
)
from .schema import annotation_revision, ontology_release, project, source_asset

PIN_KEYS = (
    "source_sha256",
    "source_width",
    "source_height",
    "ontology_version",
    "ontology_hash",
    "assignment_version",
    "annotation_revision",
)


def reject(code):
    raise ManagementFailure(ManagementError(detail=code))


def plan_pins(connection, workspace_id, project_id, asset_id):
    """Current canonical pins for one owned asset, or a typed refusal."""
    owned = connection.execute(
        select(project).where(project.c.id == project_id, project.c.workspace_id == workspace_id)
    ).one_or_none()
    if owned is None:
        reject("not_found")
    source = connection.execute(
        select(source_asset).where(
            source_asset.c.asset_key == asset_id, source_asset.c.project_id == owned.id
        )
    ).one_or_none()
    if source is None:
        reject("not_found")
    ontology = connection.execute(
        select(ontology_release)
        .where(ontology_release.c.project_id == owned.id)
        .order_by(ontology_release.c.version.desc())
        .limit(1)
    ).one_or_none()
    assignment = current_assignment_version(connection, owned.id, [source.id])
    revision = connection.execute(
        select(annotation_revision.c.revision)
        .where(annotation_revision.c.asset_id == source.id)
        .order_by(annotation_revision.c.revision.desc())
        .limit(1)
    ).scalar_one_or_none()
    if ontology is None or assignment is None or revision is None:
        reject("missing_evidence")
    validate_ontology(ontology)
    return {
        "asset_id": str(source.id),
        "asset_key": source.asset_key,
        "source_sha256": source.sha256,
        "source_width": source.width,
        "source_height": source.height,
        "ontology_version": ontology.version,
        "ontology_hash": ontology_release_hash(list(ontology.classes), dict(ontology.class_map)),
        "assignment_version": assignment,
        "annotation_revision": revision,
    }


def assemble_plan(plan_id, pins, recipe, crop=None):
    """Validated leaf plan from resolved pins; geometry errors are typed."""
    try:
        plan = build_recipe_plan(
            plan_id=plan_id,
            asset_id=pins["asset_id"],
            recipe=recipe,
            crop=crop,
            **{key: pins[key] for key in PIN_KEYS},
        )
    except (KeyError, TypeError, ValueError):
        reject("incompatible_recipe")
    return plan, plan_digest(plan)


def server_recipe(recipe_id):
    try:
        return resolve_recipe(recipe_id)
    except (TypeError, ValueError):
        reject("incompatible_recipe")


def build_plan(connection, workspace_id, *, plan_id, project_id, asset_id, recipe_id, crop=None):
    """Editable plan pinned to current canonical evidence; writes nothing."""
    pins = plan_pins(connection, workspace_id, project_id, asset_id)
    return assemble_plan(plan_id, pins, server_recipe(recipe_id), crop)


def recipe_plan_read(
    connection, workspace_id, *, project_id, asset_id, recipe_id, plan_id, crop=None
):
    """Public plan projection; the transform is the compiler's own geometry."""
    plan, digest = build_plan(
        connection,
        workspace_id,
        plan_id=plan_id,
        project_id=project_id,
        asset_id=asset_id,
        recipe_id=recipe_id,
        crop=crop,
    )
    return RecipePlanRead(
        plan_id=plan.plan_id,
        project_id=project_id,
        asset_id=asset_id,
        recipe_id=recipe_id,
        source_sha256=plan.source_sha256,
        source_width=plan.source_width,
        source_height=plan.source_height,
        ontology_version=plan.ontology_version,
        ontology_sha256=plan.ontology_hash,
        assignment_version=plan.assignment_version,
        annotation_revision=plan.annotation_revision,
        crop=CropRead(
            x=int(plan.crop.x),
            y=int(plan.crop.y),
            width=int(plan.crop.width),
            height=int(plan.crop.height),
        )
        if plan.crop is not None
        else None,
        transform=full_frame_transform(
            plan.source_width,
            plan.source_height,
            plan.recipe.output_size,
            crop=plan.crop,
            allow_upscale=plan.recipe.allow_upscale,
        ),
        plan_sha256=digest,
    )


def revise_crop(plan, crop: Crop | None):
    """Revised editable plan and digest; frozen or invalid geometry refuses."""
    try:
        revised = with_crop(plan, crop)
    except (TypeError, ValueError):
        reject("incompatible_recipe")
    return revised, plan_digest(revised)


def require_current(connection, workspace_id, project_id, asset_id, plan):
    """Refuse a plan whose pins no longer match current canonical evidence."""
    pins = plan_pins(connection, workspace_id, project_id, asset_id)
    if pins["asset_id"] != plan.asset_id:
        reject("revision_conflict")
    try:
        require_fresh(
            plan,
            source_sha256=pins["source_sha256"],
            ontology_hash=pins["ontology_hash"],
            assignment_version=pins["assignment_version"],
            annotation_revision=pins["annotation_revision"],
            recipe_policy=asdict(server_recipe(plan.recipe.recipe_id)),
        )
    except ManagementFailure:
        raise
    except (TypeError, ValueError):
        reject("revision_conflict")
    return plan_digest(plan)
