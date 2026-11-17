"""Original-raster grouping inputs, not persistence or publication acceptance."""

import hashlib
import io
from dataclasses import asdict, replace
from unittest import TestCase
from unittest.mock import patch

from PIL import Image

from labelloop_api.management_grouping_evidence import collect_source_grouping_evidence
from labelloop_compiler.ingest import process_image
from labelloop_core.domain import SourceAsset
from labelloop_core.grouping import group_sources, phash_grayscale


def encoded_image(*, mode="RGB", image_format="JPEG", orientation=None):
    image = Image.new(mode, (48, 32))
    for x in range(48):
        for y in range(32):
            value = ((x * 7 + y * 11) % 256, (x * y) % 256, (x + y * 3) % 256)
            image.putpixel((x, y), value if mode == "RGB" else value[0])
    output = io.BytesIO()
    options = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        options["exif"] = exif
    image.save(output, format=image_format, **options)
    return output.getvalue()


def source_for(data, **changes):
    digest = hashlib.sha256(data).hexdigest()
    return SourceAsset(
        **{
            "asset_id": "original-1",
            "project_id": "project-1",
            "sha256": digest,
            "object_key": f"sha256/{digest[:2]}/{digest}",
            "width": 48,
            "height": 32,
            **changes,
        }
    )


class SourceGroupingEvidenceTests(TestCase):
    def collect(self, asset, data, **changes):
        return collect_source_grouping_evidence(
            asset,
            data,
            **{"metadata": {"session": "capture-a"}, "class_counts": {"target": 2}, **changes},
        )

    def test_actual_ingest_raster_uses_the_existing_phash_without_reencoding(self):
        uploaded = encoded_image(orientation=6)
        asset, normalized = process_image(uploaded, asset_id="original-1", project_id="project-1")
        self.assertEqual(asset.exif_orientation, 6)
        self.assertEqual((asset.width, asset.height), (32, 48))
        before = asdict(asset)
        evidence = self.collect(asset, normalized)
        with Image.open(io.BytesIO(normalized)) as image:
            self.assertEqual(evidence.phash, phash_grayscale(image))
        self.assertEqual(evidence.asset_id, asset.asset_id)
        self.assertEqual(evidence.sha256, hashlib.sha256(normalized).hexdigest())
        self.assertEqual(evidence.embedding, ())
        self.assertEqual(asdict(asset), before)

    def test_metadata_and_counts_are_copied_not_authority_or_aliases(self):
        data = encoded_image()
        metadata, counts = {"subject": "specimen-a"}, {"target": 2}
        evidence = self.collect(source_for(data), data, metadata=metadata, class_counts=counts)
        metadata["subject"], counts["target"] = "changed", 100
        self.assertEqual(evidence.metadata, {"subject": "specimen-a"})
        self.assertEqual(evidence.class_counts, {"target": 2})
        self.assertFalse(hasattr(evidence, "locked"))
        self.assertFalse(hasattr(evidence, "training_eligible"))

    def test_empty_counts_remain_unassessed_not_a_negative_label(self):
        data = encoded_image()
        evidence = self.collect(source_for(data), data, metadata={}, class_counts={})
        self.assertEqual(evidence.class_counts, {})
        self.assertEqual(evidence.metadata, {})

    def test_observations_feed_delivered_grouping_with_exact_and_metadata_relations(self):
        data = encoded_image()
        left = self.collect(source_for(data), data)
        right = self.collect(source_for(data, asset_id="original-2"), data)
        groups, relations, _ = group_sources([right, left])
        self.assertEqual(groups[left.asset_id], groups[right.asset_id])
        self.assertEqual(
            {relation.reason for relation in relations}, {"exact_hash", "phash", "metadata"}
        )
        self.assertEqual(group_sources([left, right]), group_sources([right, left]))
        self.assertEqual(
            {relation.evidence for relation in relations if relation.reason == "metadata"},
            {"session"},
        )

    def test_changed_bytes_refuse_before_decoder_or_phash(self):
        data = encoded_image()
        with patch("labelloop_api.management_grouping_evidence.Image.open") as opened:
            with self.assertRaisesRegex(ValueError, "canonical hash"):
                self.collect(source_for(data), data + b"changed")
            opened.assert_not_called()

    def test_empty_oversized_and_nonbyte_inputs_refuse_before_decode(self):
        data = encoded_image()
        with patch("labelloop_api.management_grouping_evidence.MAX_UPLOAD_BYTES", len(data) - 1):
            with self.assertRaisesRegex(ValueError, "bounded byte"):
                self.collect(source_for(data), data)
        for invalid in (b"", bytearray(data), memoryview(data), "not bytes"):
            with self.subTest(kind=type(invalid).__name__):
                with self.assertRaisesRegex(ValueError, "bounded byte"):
                    self.collect(source_for(data), invalid)

    def test_dimensions_and_source_identity_refuse_before_decode(self):
        data = encoded_image()
        for changes in (
            {"asset_id": ""},
            {"project_id": ""},
            {"width": True},
            {"height": 32.0},
            {"width": 100_000_001},
        ):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, "canonical grouping source"):
                    self.collect(source_for(data, **changes), data)
        with self.assertRaisesRegex(ValueError, "normalized canonical"):
            self.collect(source_for(data, width=47), data)

    def test_wrong_format_mode_or_unapplied_orientation_is_not_normalized(self):
        for options in (
            {"image_format": "PNG"},
            {"mode": "L"},
            {"orientation": 6},
            {"orientation": 9},
        ):
            with self.subTest(options=options):
                data = encoded_image(**options)
                with self.assertRaisesRegex(ValueError, "normalized canonical"):
                    self.collect(source_for(data), data)

    def test_truncated_or_undecodable_matching_hash_is_not_evidence(self):
        for data in (b"not an image", encoded_image()[:100], encoded_image()[:-64]):
            with self.subTest(size=len(data)):
                with self.assertRaisesRegex(ValueError, "decoded completely"):
                    self.collect(source_for(data), data)

    def test_untrusted_observation_shapes_refuse(self):
        data = encoded_image()
        invalid = [
            {"metadata": {"split": "test"}},
            {"metadata": {"session": ""}},
            {"metadata": {"session": 7}},
            {"metadata": {"session": "x" * 4097}},
            {"metadata": None},
            {"class_counts": {"target": True}},
            {"class_counts": {"target": -1}},
            {"class_counts": {"target": 1.5}},
            {"class_counts": {"": 1}},
            {"class_counts": {7: 1}},
            {"class_counts": None},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, "canonical grouping observations"):
                    self.collect(source_for(data), data, **changes)

    def test_repeated_collection_is_deterministic_and_has_no_embedding_claim(self):
        data = encoded_image()
        asset = source_for(data)
        self.assertEqual(self.collect(asset, data), self.collect(replace(asset), data))
        self.assertEqual(self.collect(asset, data).embedding, ())
