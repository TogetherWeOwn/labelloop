"""Original synthetic NDJSON and raster fixtures; no network or provider credentials."""

import io
import json
from dataclasses import replace

import pytest
from PIL import Image

from labelloop_platform.saved_export import SavedExport, read_saved_export
from labelloop_platform.sync_records import (
    ContentEvidence,
    ImageSnapshot,
    Label,
    SyncConflict,
    sha256,
)


class Source:
    def __init__(self):
        output = io.BytesIO()
        Image.new("RGB", (80, 40), (17, 59, 101)).save(output, format="PNG")
        self.pixels = output.getvalue()
        self.calls = []
        self.record = {
            "id": "dataset-id",
            "owner": "fixture-owner",
            "dataset": "frozen-release",
            "visibility": "private",
            "task": "detect",
            "status": "ready",
            "classNames": ["fixture-class"],
            "versions": [
                {
                    "version": 3,
                    "contentHash": "opaque-version-hash",
                    "imageCount": 1,
                    "classCount": 1,
                    "annotationCount": 1,
                    "splits": {"train": 0, "val": 1, "test": 0},
                }
            ],
        }
        # Header version alone cannot establish the API request's saved version identity.
        self.header = {
            "type": "dataset",
            "task": "detect",
            "version": 0,
            "class_names": {"0": "fixture-class"},
        }
        self.row = {
            "type": "image",
            "file": "fixture.png",
            "url": "https://storage.example/fixture",
            "width": 80,
            "height": 40,
            "split": "val",
            "annotations": {"boxes": [[0, 0.314159, 0.623456, 0.123456, 0.234567]]},
        }
        self.current = ImageSnapshot(
            "image-id",
            "dataset-id",
            "fixture.png",
            "val",
            80,
            40,
            ("fixture-class",),
            (Label(0, (0.31416, 0.62346, 0.12346, 0.23457)),),
            content=ContentEvidence(sha256(self.pixels), 80, 40),
            storage_hash="opaque-cas",
        )
        self.version = 3
        self.raw = None

    def encoded(self):
        return (json.dumps(self.header) + "\n" + json.dumps(self.row) + "\n").encode()

    def saved_export(self, owner, slug, version):
        self.calls.append((owner, slug, version))
        return SavedExport(
            self.record, self.version, self.encoded() if self.raw is None else self.raw
        )

    def image_by_filename(self, dataset_id, filename):
        assert (dataset_id, filename) == ("dataset-id", "fixture.png")
        return self.current

    def download_image(self, url):
        assert url == "https://storage.example/fixture"
        return self.pixels


def decode_dimensions(data):
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        return image.size


def read(source, **kwargs):
    return read_saved_export(
        source,
        owner="fixture-owner",
        slug="frozen-release",
        dataset_id="dataset-id",
        version=3,
        content_hash="opaque-version-hash",
        decode_dimensions=decode_dimensions,
        **kwargs,
    )


def test_reader_pins_requested_version_hashes_exact_export_and_uses_saved_labels():
    source = Source()
    result = read(source)
    assert source.calls == [("fixture-owner", "frozen-release", 3)]
    assert result.version == 3
    assert result.content_hash == "opaque-version-hash"
    assert result.export_sha256 == sha256(source.encoded())
    assert result.origin == "saved_version_export"
    assert result.images[0].labels[0].bbox == (0.314159, 0.623456, 0.123456, 0.234567)
    assert result.images[0].labels != source.current.labels
    assert result.images[0].content == ContentEvidence(sha256(source.pixels), 80, 40)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "other-dataset"),
        ("owner", "other-owner"),
        ("dataset", "other-slug"),
        ("visibility", "public"),
        ("status", "processing"),
        ("task", "segment"),
        ("classNames", ["renamed-class"]),
    ],
)
def test_wrong_dataset_or_processing_is_not_a_saved_capture(field, value):
    source = Source()
    source.record[field] = value
    with pytest.raises(SyncConflict):
        read(source)


@pytest.mark.parametrize("change", ["api-version", "version-hash", "missing-version", "duplicate"])
def test_wrong_or_ambiguous_saved_version_fails(change):
    source = Source()
    if change == "api-version":
        source.version = 4
    elif change == "version-hash":
        source.record["versions"][0]["contentHash"] = "different-hash"
    elif change == "missing-version":
        source.record["versions"] = []
    else:
        source.record["versions"] *= 2
    with pytest.raises(SyncConflict):
        read(source)


@pytest.mark.parametrize("change", ["split", "labels", "bytes", "missing-content", "dataset"])
def test_filename_lookup_alone_cannot_bind_remote_image_id(change):
    source = Source()
    if change == "split":
        source.current = replace(source.current, split="train")
    elif change == "labels":
        source.current = replace(source.current, labels=())
    elif change == "bytes":
        source.current = replace(source.current, content=ContentEvidence("a" * 64, 80, 40))
    elif change == "missing-content":
        source.current = replace(source.current, content=None)
    else:
        source.current = replace(source.current, dataset_id="another-dataset")
    with pytest.raises(SyncConflict):
        read(source)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}\n",
        b'{"type":"dataset","type":"image"}\n',
        b"[]\n",
        b"not-json\n",
        b"{\n",
        b"\xff\n",
    ],
)
def test_malformed_and_duplicate_key_exports_fail_closed(raw):
    source = Source()
    source.raw = raw
    with pytest.raises(SyncConflict):
        read(source)


@pytest.mark.parametrize("change", ["missing-row", "extra-row", "partial", "mixed-task", "tuple"])
def test_incomplete_or_unsupported_export_is_never_silently_normalized(change):
    source = Source()
    if change == "missing-row":
        source.raw = json.dumps(source.header).encode()
    elif change == "extra-row":
        source.raw = source.encoded() + json.dumps(source.row).encode() + b"\n"
    elif change == "partial":
        source.row["partial"] = True
    elif change == "mixed-task":
        source.row["annotations"]["segments"] = [[0, 0.2, 0.3, 0.4, 0.5]]
    else:
        source.row["annotations"]["boxes"] = [[0, 0.2, 0.3]]
    with pytest.raises(SyncConflict):
        read(source)


@pytest.mark.parametrize("limit", ["max_export_bytes", "max_image_bytes"])
def test_bounded_reader_rejects_large_artifacts(limit):
    with pytest.raises(SyncConflict, match="bounded reader"):
        read(Source(), **{limit: 1})


def test_actual_decoder_dimensions_are_required():
    source = Source()
    output = io.BytesIO()
    Image.new("RGB", (40, 80)).save(output, format="PNG")
    source.pixels = output.getvalue()
    with pytest.raises(SyncConflict, match="raster"):
        read(source)


@pytest.mark.parametrize(
    "field,value",
    [
        ("imageCount", 2),
        ("classCount", 2),
        ("annotationCount", 0),
        ("splits", {"train": 1, "val": 0, "test": 0}),
    ],
)
def test_saved_record_counts_cannot_hide_truncation_or_split_changes(field, value):
    source = Source()
    source.record["versions"][0][field] = value
    with pytest.raises(SyncConflict):
        read(source)


@pytest.mark.parametrize("error_type", [ValueError, SyncConflict])
def test_decoder_exception_cannot_disclose_signed_url(error_type):
    source = Source()

    def bad_decoder(data):
        raise error_type("https://storage.example/private?signed=do-not-log")

    with pytest.raises(SyncConflict) as error:
        read_saved_export(
            source,
            owner="fixture-owner",
            slug="frozen-release",
            dataset_id="dataset-id",
            version=3,
            content_hash="opaque-version-hash",
            decode_dimensions=bad_decoder,
        )
    assert "signed" not in str(error.value)
    assert error.value.__suppress_context__
