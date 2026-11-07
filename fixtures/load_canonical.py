"""Fixture-only adapter from declarative metadata to canonical records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
from labelloop_core.geometry import Box
from labelloop_core.grouping import AssetEvidence

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "fixtures" / "golden"


class FixtureDomain:
    def __init__(self, domain: str) -> None:
        document = load_labels(domain)
        source_directory = ROOT / "fixtures" / domain / "images"
        self.images: dict[str, bytes] = {}
        sources: dict[str, SourceAsset] = {}
        for item in document["images"]:
            data = (source_directory / item["file"]).read_bytes()
            asset_id = item["file"].removesuffix(".jpg")
            from labelloop_compiler.ingest import process_image  # type: ignore[import-untyped]

            asset, encoded = process_image(
                data,
                asset_id=asset_id,
                project_id=domain,
                source_uri=item["file"],
            )
            assert asset.sha256 == item["normalized_sha256"]
            self.images[asset_id] = encoded
            sources[asset_id] = asset
        ontology = OntologyRelease(
            ontology_id=f"{domain}-ontology-v1",
            project_id=domain,
            version=1,
            classes=tuple(ClassDefinition(**item) for item in document["classes"]),
            class_map=document["class_map"],
        )
        assignment = SourceSplitAssignment(**document["split_assignment"])
        self.release = CanonicalDatasetRelease(
            release_id=f"{domain}-canonical-r1",
            project_id=domain,
            ontology=ontology,
            assignment=assignment,
            samples=tuple(
                ReleasedSample(
                    asset=sources[item["asset_id"]],
                    revision=AnnotationRevision(
                        revision=item.get("revision", 1),
                        objects=tuple(
                            DatasetObject(
                                object_id=obj["object_id"],
                                class_id=obj["class_id"],
                                box=Box(*obj["box"]),
                                attributes=obj.get("attributes", {}),
                                occluded=obj.get("occluded", False),
                                truncated_at_image_edge=obj.get("truncated_at_image_edge", False),
                            )
                            for obj in item["objects"]
                        ),
                        actor_kind="human",
                        approval_state="auto_accepted",
                        review_tier="machine_verified_no_human_review",
                        scope_completeness="exhaustive_for_scope",
                        training_eligible=True,
                    ),
                    group_id=assignment.group_ids[item["asset_id"]],
                    split=assignment.asset_splits[item["asset_id"]],
                )
                for item in document["images"]
                if item["asset_id"] not in document.get("exclusions", {})
            ),
            excluded_asset_ids=document.get("exclusions", {}),
        )


def load_labels(domain: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((GOLDEN / f"{domain}.labels.json").read_text())
    return document


def load_evidence(domain: str) -> list[AssetEvidence]:
    return [AssetEvidence(**item) for item in load_labels(domain)["evidence"]]
