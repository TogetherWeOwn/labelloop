"""Canonical test fixture construction with independent source pixels."""

import io

from PIL import Image

from labelloop_compiler.ingest import process_image
from labelloop_core.domain import (
    AnnotationRevision,
    CanonicalDatasetRelease,
    ClassDefinition,
    DatasetObject,
    OntologyRelease,
    ReleasedSample,
    SourceSplitAssignment,
)
from labelloop_core.geometry import Box


def canonical_release():
    images, samples = {}, []
    for name in ("a", "b"):
        output = io.BytesIO()
        image = Image.new("RGB", (200, 150), (12, 62, 120))
        image.putpixel((0, 0), (255, 0, 0) if name == "a" else (0, 255, 0))
        image.save(output, "JPEG", quality=95)
        asset, encoded = process_image(output.getvalue(), asset_id=name, project_id="project")
        images[name] = encoded
        samples.append(
            ReleasedSample(
                asset=asset,
                revision=AnnotationRevision(
                    1,
                    (DatasetObject("object-1", "item", Box(10, 20, 170, 120)),),
                    "human",
                    approval_state="human_accepted",
                    review_tier="adjudicated",
                    scope_completeness="exhaustive_for_scope",
                    training_eligible=True,
                ),
                group_id=f"{name}-group",
                split="val" if name == "b" else "train",
            )
        )
    return CanonicalDatasetRelease(
        "release",
        "project",
        OntologyRelease(
            "ontology", "project", 1, (ClassDefinition("item", "item", "test object"),), {"item": 0}
        ),
        SourceSplitAssignment(
            "test",
            {"a": "train", "b": "val"},
            {"a-group": "train", "b-group": "val"},
            {"a": "a-group", "b": "b-group"},
            1,
            "test",
            {},
        ),
        tuple(samples),
        {},
    ), images
