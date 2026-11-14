"""Frozen full-frame plans and published geometry, never inferred qualification."""

from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select

from labelloop_compiler.compiler import resolve_recipe, transform_document
from labelloop_core.domain import CanonicalDatasetRelease
from labelloop_core.geometry import Box, full_frame_transform, target_flags

from .management_canonical import refuse
from .management_details import CheckRead, RecipeBoxRead, RecipePreviewRead
from .management_training import verified_variant
from .schema import annotation_revision, canonical_dataset_release, source_asset


def recipe_plan(
    release: CanonicalDatasetRelease, recipe_id: str, asset_id: str
) -> RecipePreviewRead:
    """Geometry only: no image decoding, file validation or publication claim."""
    sample = next((item for item in release.samples if item.asset.asset_id == asset_id), None)
    if sample is None or sample.split not in {"train", "val"}:
        refuse("missing_evidence")
    recipe = resolve_recipe(recipe_id)
    source = sample.asset
    transform = full_frame_transform(
        source.width, source.height, recipe.output_size, allow_upscale=recipe.allow_upscale
    )
    boxes = []
    tiny = below = 0
    for item in sorted(sample.revision.objects, key=lambda obj: obj.object_id):
        mapped = transform.apply(item.box)
        if mapped is None:
            refuse("missing_evidence")
        flags = target_flags(transform, item.box, recipe.min_target_px)
        excluded = "below_min_target_px" in flags
        small = min(mapped.width, mapped.height) <= 6
        below += excluded
        tiny += small
        boxes.append(
            RecipeBoxRead(
                object_id=item.object_id,
                class_id=item.class_id,
                box=mapped,
                review_tier=sample.revision.review_tier,
                clipped=False,
                consequence="Below the recipe minimum; this source cannot be emitted intact"
                if excluded
                else "At or below six derived pixels"
                if small
                else "Meets full-frame geometric size policy",
                flag="exclude" if excluded else "warn" if small else "none",
            )
        )
    # The compiler excludes an all-small-target source, but rejects a mixed
    # source rather than silently removing only its difficult visible targets.
    mixed = 0 < below < len(boxes)
    return RecipePreviewRead(
        evidence_state="planned",
        recipe_id=recipe.recipe_id,
        recipe_revision=1,
        source_asset_id=asset_id,
        source_width=source.width,
        source_height=source.height,
        transform=transform,
        boxes=boxes,
        tiny_targets=tiny,
        below_minimum_targets=below,
        clipped_targets=0,
        excluded_images=int(bool(boxes) and below == len(boxes)),
        checks=[
            CheckRead(
                code="geometric_size_policy",
                passed=not mixed,
                reason="Mixed-size targets block compilation; recrop or exclude the entire source"
                if mixed
                else "All targets below minimum exclude this source; val must remain nonempty"
                if below
                else "Full-frame geometry meets the recipe minimum",
            ),
            CheckRead(
                code="compiler_publication",
                passed=None,
                reason="Planning only; no decoded pixels, compiled files or validation artifacts",
            ),
            CheckRead(
                code="model_qualification",
                passed=None,
                reason="Geometry is not model quality or qualification",
            ),
        ],
        manifest_artifact_id=None,
        validation_artifact_id=None,
    )


def excluded_preview(release, recipe_id, asset_id, manifest, manifest_digest) -> RecipePreviewRead:
    # Membership alone is insufficient: only a selected, non-test, all-small
    # source can be explained by this full-frame surface. Never invent a raster.
    planned = recipe_plan(release, recipe_id, asset_id)
    if (
        manifest["release_membership"].get(asset_id) != "excluded"
        or any(sample["asset_id"] == asset_id for sample in manifest["samples"])
        or manifest["excluded_count"] < 1
        or planned.excluded_images != 1
        or planned.below_minimum_targets != len(planned.boxes)
    ):
        refuse("missing_evidence")
    return planned.model_copy(
        update={
            "evidence_state": "excluded",
            "checks": [
                planned.checks[0],
                CheckRead(
                    code="publication_integrity",
                    passed=True,
                    reason="Published excluded membership; boxes are frozen geometry, not a raster",
                ),
                planned.checks[2],
            ],
            "manifest_artifact_id": manifest_digest,
            "validation_artifact_id": manifest["file_hashes"]["validation-report.json"],
        }
    )


def recipe_preview(connection, context, workspace_id: UUID, asset_id: str) -> RecipePreviewRead:
    variant = SimpleNamespace(**context, immutable=context["recipe_immutable"])
    manifest, manifest_digest, _ = verified_variant(connection, variant, workspace_id)
    samples = [sample for sample in manifest["samples"] if sample["asset_id"] == asset_id]
    if not samples and manifest["release_membership"].get(asset_id) == "excluded":
        from .management_compile_worker import frozen_release

        release_row = connection.execute(
            select(canonical_dataset_release).where(
                canonical_dataset_release.c.release_key == context["release_key"],
                canonical_dataset_release.c.project_id == context["project_id"],
            )
        ).one()
        frozen, _ = frozen_release(
            connection.connection.driver_connection, dict(release_row._mapping), workspace_id
        )
        return excluded_preview(frozen, context["recipe_key"], asset_id, manifest, manifest_digest)
    if len(samples) != 1:
        refuse("missing_evidence")
    sample = samples[0]
    # This development surface must not become an alternate final-test label reader.
    if sample["split"] not in {"train", "val"}:
        refuse("missing_evidence")
    source = connection.execute(
        select(source_asset, annotation_revision.c.objects, annotation_revision.c.review_tier)
        .select_from(source_asset)
        .join(annotation_revision, annotation_revision.c.asset_id == source_asset.c.id)
        .where(
            source_asset.c.project_id == context["project_id"],
            source_asset.c.asset_key == asset_id,
            annotation_revision.c.revision == sample["revision"],
        )
    ).one_or_none()
    if source is None or source.sha256 != sample["source_sha256"]:
        refuse("missing_evidence")
    recipe = resolve_recipe(context["recipe_key"])
    transform = full_frame_transform(
        source.width, source.height, recipe.output_size, allow_upscale=recipe.allow_upscale
    )
    objects = sorted(source.objects, key=lambda item: item["object_id"])
    if (
        transform_document(transform) != sample["transform"]
        or sample["image_size"] != [recipe.output_size, recipe.output_size]
        or sample["review_tier"] != source.review_tier
        or len(sample["objects"]) != len(objects)
        or sample["verified_negative"] != (not objects)
    ):
        refuse("missing_evidence")
    boxes = []
    tiny = 0
    for original, compiled in zip(objects, sample["objects"], strict=True):
        box = Box(*original["box"])
        mapped = transform.apply(box)
        flags = target_flags(transform, box, recipe.min_target_px)
        if (
            mapped is None
            or flags
            or compiled["object_id"] != original["object_id"]
            or compiled["class_id"] != original["class_id"]
            or compiled["source_box"] != original["box"]
            or compiled["policy"] != "retained"
        ):
            refuse("missing_evidence")
        small = min(mapped.width, mapped.height) <= 6
        tiny += small
        boxes.append(
            RecipeBoxRead(
                object_id=original["object_id"],
                class_id=original["class_id"],
                box=mapped,
                review_tier=source.review_tier,
                clipped=False,
                consequence="At or below six derived pixels" if small else "Retained by compiler",
                flag="warn" if small else "none",
            )
        )
    return RecipePreviewRead(
        recipe_id=recipe.recipe_id,
        recipe_revision=1,
        source_asset_id=asset_id,
        source_width=source.width,
        source_height=source.height,
        transform=transform,
        boxes=boxes,
        tiny_targets=tiny,
        below_minimum_targets=0,
        clipped_targets=0,
        excluded_images=0,
        checks=[
            CheckRead(
                code="publication_integrity",
                passed=True,
                reason="Verified emitted full-frame sample and fenced compiler publication",
            ),
            CheckRead(
                code="model_qualification",
                passed=None,
                reason="Compiler geometry is not model quality or qualification",
            ),
        ],
        manifest_artifact_id=manifest_digest,
        validation_artifact_id=manifest["file_hashes"]["validation-report.json"],
    )
