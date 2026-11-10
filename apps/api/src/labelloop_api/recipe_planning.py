"""Editable ROI/recipe plan leaf; no HTTP mounts, locks or audit writes.

Plans pin exact source/ontology/assignment/recipe revisions and reuse
core geometry. Historical frozen previews (immutable/frozen) are never
editable. Callers own the outer transaction and pass a SQLAlchemy
Connection into every DB helper; integrity failures roll back via a
savepoint, preserving the frozen history.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from sqlalchemy import Connection, insert, select
from sqlalchemy.exc import IntegrityError

from labelloop_core.domain import Recipe
from labelloop_core.geometry import Crop, full_frame_transform

from .schema import (
    annotation_revision,
    canonical_dataset_release,
    preprocessing_recipe,
    source_asset,
    source_split_assignment,
)


@dataclass(frozen=True, slots=True)
class RecipePlan:
    """An editable planning pin. Frozen previews must use frozen=True."""

    plan_id: str
    asset_id: str
    source_sha256: str
    source_width: int
    source_height: int
    ontology_version: int
    ontology_hash: str
    assignment_version: str
    annotation_revision: int
    recipe: Recipe
    crop: Crop | None = None
    frozen: bool = False

    def __post_init__(self) -> None:
        if not self.plan_id or not self.asset_id:
            raise ValueError("recipe plan requires plan and asset identity")
        if len(self.source_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.source_sha256
        ):
            raise ValueError("recipe plan requires a SHA-256 source pin")
        if self.source_width <= 0 or self.source_height <= 0:
            raise ValueError("recipe plan requires positive source dimensions")
        if self.ontology_version < 1 or self.annotation_revision < 1:
            raise ValueError("recipe plan requires positive revision pins")
        if self.frozen:
            raise ValueError("historical frozen preview is not an editable plan")


def plan_digest(plan: RecipePlan) -> str:
    """Deterministic digest over the exact revision pins and geometry."""
    document = {
        "plan_id": plan.plan_id,
        "asset_id": plan.asset_id,
        "source_sha256": plan.source_sha256,
        "source_width": plan.source_width,
        "source_height": plan.source_height,
        "ontology_version": plan.ontology_version,
        "ontology_hash": plan.ontology_hash,
        "assignment_version": plan.assignment_version,
        "annotation_revision": plan.annotation_revision,
        "recipe": asdict(plan.recipe),
        "crop": asdict(plan.crop) if plan.crop else None,
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_plan_geometry(plan: RecipePlan) -> None:
    if plan.crop is not None:
        for value in (plan.crop.x, plan.crop.y, plan.crop.width, plan.crop.height):
            if not float(value).is_integer():
                raise ValueError("rasterization of fractional crops requires a versioned policy")
        if plan.crop.x + plan.crop.width > plan.source_width or plan.crop.y + plan.crop.height > (
            plan.source_height
        ):
            raise ValueError("crop must be contained by the source")
    # Reuse the authoritative core transform: invalid sizes/crops fail here.
    full_frame_transform(
        plan.source_width,
        plan.source_height,
        plan.recipe.output_size,
        crop=plan.crop,
        allow_upscale=plan.recipe.allow_upscale,
    )
    if not isinstance(plan.recipe, Recipe):
        raise ValueError("recipe plan requires a core Recipe")


def build_recipe_plan(
    *,
    plan_id: str,
    asset_id: str,
    source_sha256: str,
    source_width: int,
    source_height: int,
    ontology_version: int,
    ontology_hash: str,
    assignment_version: str,
    annotation_revision: int,
    recipe: Recipe,
    crop: Crop | None = None,
) -> RecipePlan:
    plan = RecipePlan(
        plan_id=plan_id,
        asset_id=asset_id,
        source_sha256=source_sha256,
        source_width=source_width,
        source_height=source_height,
        ontology_version=ontology_version,
        ontology_hash=ontology_hash,
        assignment_version=assignment_version,
        annotation_revision=annotation_revision,
        recipe=recipe,
        crop=crop,
    )
    validate_plan_geometry(plan)
    return plan


def with_crop(plan: RecipePlan, crop: Crop | None) -> RecipePlan:
    if plan.frozen:
        raise ValueError("historical frozen preview is not an editable plan")
    updated = RecipePlan(
        plan_id=plan.plan_id,
        asset_id=plan.asset_id,
        source_sha256=plan.source_sha256,
        source_width=plan.source_width,
        source_height=plan.source_height,
        ontology_version=plan.ontology_version,
        ontology_hash=plan.ontology_hash,
        assignment_version=plan.assignment_version,
        annotation_revision=plan.annotation_revision,
        recipe=plan.recipe,
        crop=crop,
    )
    validate_plan_geometry(updated)
    return updated


def require_fresh(
    plan: RecipePlan,
    *,
    source_sha256: str,
    ontology_hash: str,
    assignment_version: str,
    annotation_revision: int,
    recipe_policy: dict | None = None,
    frozen: bool = False,
) -> None:
    if frozen:
        raise ValueError("historical frozen preview is not an editable plan")
    if plan.source_sha256 != source_sha256:
        raise ValueError("stale source pin")
    if plan.ontology_hash != ontology_hash:
        raise ValueError("stale ontology pin")
    if plan.assignment_version != assignment_version:
        raise ValueError("stale assignment pin")
    if plan.annotation_revision != annotation_revision:
        raise ValueError("stale annotation revision pin")
    if recipe_policy is not None and recipe_policy != asdict(plan.recipe):
        raise ValueError("stale recipe pin")


def ontology_release_hash(classes: list[dict], class_map: dict[str, int]) -> str:
    encoded = json.dumps(
        {"class_map": class_map, "classes": classes},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def load_current_pins(connection: Connection, *, asset_id: str) -> dict:
    """Current DB pins for staleness checks; caller owns transaction."""
    from .schema import ontology_release

    asset = (
        connection.execute(
            select(
                source_asset.c.sha256,
                source_asset.c.width,
                source_asset.c.height,
                source_asset.c.project_id,
            ).where(source_asset.c.id == asset_id)
        )
        .mappings()
        .one()
    )
    ontology = (
        connection.execute(
            select(
                ontology_release.c.classes,
                ontology_release.c.class_map,
                ontology_release.c.version,
            )
            .where(ontology_release.c.project_id == asset["project_id"])
            .order_by(ontology_release.c.version.desc())
        )
        .mappings()
        .first()
    )
    assignment = (
        connection.execute(
            select(source_split_assignment.c.assignment_version).where(
                source_split_assignment.c.asset_id == asset_id
            )
        )
        .mappings()
        .first()
    )
    revision_row = (
        connection.execute(
            select(annotation_revision.c.revision)
            .where(annotation_revision.c.asset_id == asset_id)
            .order_by(annotation_revision.c.revision.desc())
        )
        .mappings()
        .first()
    )
    return {
        "source_sha256": asset["sha256"],
        "source_width": asset["width"],
        "source_height": asset["height"],
        "ontology_version": ontology["version"] if ontology else None,
        "ontology_hash": ontology_release_hash(
            list(ontology["classes"]), dict(ontology["class_map"])
        )
        if ontology
        else None,
        "assignment_version": assignment["assignment_version"] if assignment else None,
        "annotation_revision": revision_row["revision"] if revision_row else None,
    }


def freeze_recipe_plan(
    connection: Connection,
    plan: RecipePlan,
    *,
    recipe_key: str,
    expected_recipe_policy: dict | None = None,
) -> str:
    """Revalidate pins and publish the immutable recipe row atomically.

    A stale pin or an already-published recipe_key rolls the savepoint
    back; frozen history (canonical_dataset_release) is never rewritten.
    """
    validate_plan_geometry(plan)
    if plan.frozen:
        raise ValueError("historical frozen preview is not an editable plan")
    try:
        with connection.begin_nested():
            existing = (
                connection.execute(
                    select(preprocessing_recipe.c.recipe_key).where(
                        preprocessing_recipe.c.recipe_key == recipe_key
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                raise ValueError("recipe already published")
            connection.execute(
                insert(preprocessing_recipe).values(
                    recipe_key=recipe_key,
                    policy=json.loads(json.dumps(asdict(plan.recipe))),
                    immutable=True,
                )
            )
    except IntegrityError as error:
        raise ValueError("recipe already published") from error
    return plan_digest(plan)


def assert_not_frozen_release(connection: Connection, *, release_id: str) -> None:
    if connection.execute(
        select(canonical_dataset_release.c.frozen).where(
            canonical_dataset_release.c.id == release_id
        )
    ).scalar_one():
        raise ValueError("historical frozen preview is not an editable plan")
