"""Pure geometric planning cases, not image decoding or DB/publication acceptance."""

from dataclasses import replace

import pytest

from labelloop_api.management_ports import ManagementFailure
from labelloop_api.management_recipe_reads import excluded_preview, recipe_plan
from labelloop_core.domain import (
    AnnotationRevision,
    CanonicalDatasetRelease,
    ClassDefinition,
    DatasetObject,
    OntologyRelease,
    ReleasedSample,
    SourceAsset,
    SourceSplitAssignment,
)
from labelloop_core.geometry import Box, full_frame_transform


def frozen(width, height, boxes=(), *, split="val"):
    sample = ReleasedSample(
        asset=SourceAsset("source", "project", "1" * 64, "sha256/11/" + "1" * 64, width, height),
        revision=AnnotationRevision(
            revision=1,
            objects=tuple(
                DatasetObject(f"object-{index}", "package", box) for index, box in enumerate(boxes)
            ),
            actor_kind="human",
            approval_state="human_accepted",
            review_tier="human_reviewed",
            scope_completeness="exhaustive_for_scope",
            training_eligible=True,
        ),
        group_id="group",
        split=split,
    )
    samples = (sample,)
    if split != "val":
        samples += (
            replace(
                sample,
                asset=replace(sample.asset, asset_id="validation"),
                group_id="validation",
                split="val",
            ),
        )
    return CanonicalDatasetRelease(
        release_id="release",
        project_id="project",
        ontology=OntologyRelease(
            "ontology",
            "project",
            1,
            (ClassDefinition("package", "Package", "A package"),),
            {"package": 0},
        ),
        assignment=SourceSplitAssignment(
            "assignment",
            {item.asset.asset_id: item.split for item in samples},
            {item.group_id: item.split for item in samples},
            {item.asset.asset_id: item.group_id for item in samples},
            7,
            "fixture",
            {item.asset.asset_id: "fixture" for item in samples},
        ),
        samples=samples,
        excluded_asset_ids={"excluded": "not_selected"},
    )


@pytest.mark.parametrize("side", [320, 640])
def test_plan_negative_geometry_has_no_artifacts_decode_or_qualification_claim(side):
    release = frozen(1920, 1080)
    detail = recipe_plan(release, f"full-letterbox-{side}-v1", "source")
    assert detail.evidence_state == "planned"
    assert detail.transform == full_frame_transform(1920, 1080, side)
    assert detail.boxes == [] and detail.excluded_images == detail.tiny_targets == 0
    assert detail.manifest_artifact_id is detail.validation_artifact_id is None
    assert [(item.code, item.passed) for item in detail.checks] == [
        ("geometric_size_policy", True),
        ("compiler_publication", None),
        ("model_qualification", None),
    ]


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("derived_px", [2, 4, 6, 7])
def test_plan_uses_core_minimum_and_warning_boundaries_without_discarding_objects(side, derived_px):
    box = Box(3, 3, 3 + 3 * derived_px, 3 + 3 * derived_px)
    release = frozen(side * 3, side * 3, (box,))
    detail = recipe_plan(release, f"full-letterbox-{side}-v1", "source")
    assert detail.boxes[0].box == detail.transform.apply(box)
    assert detail.boxes[0].review_tier == "human_reviewed"
    assert detail.boxes[0].clipped is False
    assert detail.boxes[0].flag == (
        "exclude" if derived_px < 4 else "warn" if derived_px <= 6 else "none"
    )
    assert detail.tiny_targets == int(derived_px <= 6)
    assert detail.below_minimum_targets == detail.excluded_images == int(derived_px < 4)
    assert detail.checks[0].passed is True
    assert len(release.samples[0].revision.objects) == 1


@pytest.mark.parametrize("side", [320, 640])
def test_mixed_tiny_and_retained_objects_block_instead_of_silently_becoming_negative(side):
    release = frozen(side * 3, side * 3, (Box(3, 3, 9, 9), Box(30, 30, 90, 90)))
    detail = recipe_plan(release, f"full-letterbox-{side}-v1", "source")
    assert len(detail.boxes) == 2 and detail.below_minimum_targets == 1
    assert detail.excluded_images == 0 and detail.checks[0].passed is False
    assert [box.flag for box in detail.boxes] == ["exclude", "none"]
    assert all(check.passed is None for check in detail.checks[1:])


@pytest.mark.parametrize("asset_id", ["source", "missing", "excluded"])
def test_plan_cannot_read_test_or_unpinned_excluded_labels(asset_id):
    release = frozen(1920, 1080, (Box(30, 30, 90, 90),), split="test")
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        recipe_plan(release, "full-letterbox-320-v1", asset_id)


@pytest.mark.parametrize("side", [320, 640])
def test_excluded_preview_distinguishes_published_membership_from_a_compiled_raster(side):
    release = frozen(side * 3, side * 3, (Box(3, 3, 9, 9),))
    manifest = {
        "release_membership": {"source": "excluded"},
        "samples": [],
        "excluded_count": 1,
        "file_hashes": {"validation-report.json": "a" * 64},
    }
    planned = recipe_plan(release, f"full-letterbox-{side}-v1", "source")
    detail = excluded_preview(release, f"full-letterbox-{side}-v1", "source", manifest, "b" * 64)
    assert detail.evidence_state == "excluded" and detail.excluded_images == 1
    assert detail.boxes == planned.boxes and detail.transform == planned.transform
    assert detail.boxes[0].flag == "exclude"
    assert detail.manifest_artifact_id == "b" * 64
    assert detail.validation_artifact_id == "a" * 64
    assert [(check.code, check.passed) for check in detail.checks] == [
        ("geometric_size_policy", True),
        ("publication_integrity", True),
        ("model_qualification", None),
    ]


@pytest.mark.parametrize("change", ["membership", "emitted", "count", "negative", "mixed", "test"])
def test_excluded_preview_requires_exact_membership_and_all_small_non_test_source(change):
    boxes = (Box(3, 3, 9, 9),)
    if change == "negative":
        boxes = ()
    elif change == "mixed":
        boxes += (Box(30, 30, 90, 90),)
    release = frozen(960, 960, boxes, split="test" if change == "test" else "val")
    manifest = {
        "release_membership": {"source": "compiled" if change == "membership" else "excluded"},
        "samples": [{"asset_id": "source"}] if change == "emitted" else [],
        "excluded_count": 0 if change == "count" else 1,
        "file_hashes": {"validation-report.json": "a" * 64},
    }
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        excluded_preview(release, "full-letterbox-320-v1", "source", manifest, "b" * 64)


def test_plan_rejects_unconfigured_recipe():
    with pytest.raises(ValueError, match="unknown preprocessing recipe"):
        recipe_plan(frozen(1920, 1080), "client-selected", "source")
