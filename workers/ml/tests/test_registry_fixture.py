"""The persistence smoke must seed the CPU manifest's real canonical sources."""

import pytest
from load_canonical import FixtureDomain
from registry_smoke import compiled_fixture_members


@pytest.fixture(scope="module")
def canonical_release():
    return FixtureDomain("cc0-interface").release


@pytest.fixture
def manifest(canonical_release):
    return {
        "split_assignment_version": canonical_release.assignment.assignment_version,
        "class_map": canonical_release.ontology.class_map,
        "samples": [
            {
                "asset_id": sample.asset.asset_id,
                "source_sha256": sample.asset.sha256,
                "revision": sample.revision.revision,
                "group_id": sample.group_id,
                "split": sample.split,
            }
            for sample in canonical_release.samples
        ],
    }


def test_compiled_fixture_uses_real_canonical_members_and_allows_derivatives(
    manifest, canonical_release
):
    manifest["samples"].append(dict(manifest["samples"][0]))
    members = compiled_fixture_members(manifest, canonical_release)
    assert set(members) == {sample.asset.asset_id for sample in canonical_release.samples}
    assert {sample.split for sample in members.values()} >= {"train", "val"}
    assert not any("synthetic-ordinary" in key for key in members)


@pytest.mark.parametrize(
    "field,value",
    [
        ("asset_id", "synthetic-ordinary-positive"),
        ("source_sha256", "f" * 64),
        ("revision", 999),
        ("group_id", "invented-group"),
        ("split", "unassigned"),
    ],
)
def test_compiled_fixture_rejects_substituted_source_lineage(
    manifest, canonical_release, field, value
):
    manifest["samples"][0][field] = value
    with pytest.raises((AssertionError, KeyError)):
        compiled_fixture_members(manifest, canonical_release)


@pytest.mark.parametrize(
    "field,value",
    [("split_assignment_version", "foreign-version"), ("class_map", {}), ("samples", [])],
)
def test_compiled_fixture_rejects_substituted_manifest(manifest, canonical_release, field, value):
    manifest[field] = value
    with pytest.raises(AssertionError):
        compiled_fixture_members(manifest, canonical_release)
