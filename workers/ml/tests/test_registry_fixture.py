"""The persistence smoke must seed the CPU manifest's real canonical sources."""

import pytest
from load_canonical import FixtureDomain
from registry_smoke import compiled_fixture_members, seed_compiled_sources


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


def test_seed_compiled_sources_preserves_group_keyed_assignment_reasons(
    manifest, canonical_release
):
    class Connection:
        def __init__(self):
            self.assignments = []

        def execute(self, statement):
            if statement.is_insert and statement.table.name == "source_split_assignment":
                self.assignments.append(statement.compile().params)
            return self

        def scalar_one(self):
            return "fixture-asset-id"

    connection = Connection()
    membership = seed_compiled_sources(connection, "fixture-project-id", manifest)
    assert membership == {
        sample.asset.asset_id: sample.revision.revision for sample in canonical_release.samples
    }
    assert len(connection.assignments) == len(canonical_release.samples)
    for sample, assignment in zip(canonical_release.samples, connection.assignments, strict=True):
        assert sample.asset.asset_id not in canonical_release.assignment.reasons
        assert assignment["group_id"] == sample.group_id
        assert assignment["reason"] == canonical_release.assignment.reasons[sample.group_id]
        assert assignment["split"] == sample.split
        assert assignment["locked"] is True


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
