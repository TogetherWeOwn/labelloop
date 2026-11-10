"""Pure and disposable-SQLite tests for the source-group lifecycle leaf.

No live database, provider call or management-facade import is used. The
disposable database mirrors the delivered canonical tables with portable
DDL so the leaf's transaction boundary (propose read-only, apply
append-only, rollback on audit failure) is exercised for real.
"""

import os
import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import create_engine, insert, select, text

from labelloop_api.revision_store import completeness_context_hash
from labelloop_api.schema import (
    annotation_revision,
    canonical_dataset_release,
    canonical_event,
    ontology_release,
    project,
    similarity_relation,
    source_asset,
    source_split_assignment,
)
from labelloop_api.source_grouping_lifecycle import (
    LifecyclePins,
    PairDecision,
    TrustedObservation,
    apply_regroup,
    compute_proposal,
    observation_from_payload,
    pair_decision_from_payload,
    propose_regroup,
)
from labelloop_core.grouping import GroupingThresholds, assign_splits

DDL = (
    "CREATE TABLE project (id TEXT PRIMARY KEY, project_key TEXT, workspace_id TEXT,"
    " task TEXT, scope TEXT, domain TEXT, access_policy JSON,"
    " created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')))",
    "CREATE TABLE ontology_release (id TEXT PRIMARY KEY, ontology_key TEXT, project_id TEXT,"
    " version INTEGER, classes JSON, class_map JSON,"
    " created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')))",
    "CREATE TABLE source_asset (id TEXT PRIMARY KEY, asset_key TEXT, project_id TEXT,"
    " sha256 TEXT, object_key TEXT, width INTEGER, height INTEGER, exif_orientation INTEGER,"
    " source_uri TEXT, created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')))",
    "CREATE TABLE annotation_revision (id TEXT PRIMARY KEY, asset_id TEXT, revision INTEGER,"
    " parent_revision INTEGER, actor_kind TEXT, actor_id TEXT, objects JSON, label_state TEXT,"
    " approval_state TEXT, review_tier TEXT, scope_completeness TEXT,"
    " training_eligible BOOLEAN, gold_eligible BOOLEAN,"
    " created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')))",
    "CREATE TABLE source_split_assignment (id TEXT PRIMARY KEY, assignment_version TEXT,"
    " asset_id TEXT, group_id TEXT, split TEXT, seed INTEGER, algorithm TEXT, thresholds JSON,"
    " reason TEXT, locked BOOLEAN,"
    " created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')),"
    " UNIQUE(assignment_version, asset_id))",
    "CREATE TABLE similarity_relation (id TEXT PRIMARY KEY, left_asset_id TEXT,"
    " right_asset_id TEXT, reason TEXT, evidence TEXT, embedding_model TEXT,"
    " algorithm_version TEXT, created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')))",
    "CREATE TABLE borderline_review (id TEXT PRIMARY KEY, left_asset_id TEXT, right_asset_id TEXT,"
    " metric TEXT, value TEXT, review_state TEXT, algorithm_version TEXT,"
    " created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')))",
    "CREATE TABLE canonical_dataset_release (id TEXT PRIMARY KEY, release_key TEXT,"
    " project_id TEXT, ontology_id TEXT, assignment_version TEXT, sample_revisions JSON,"
    " ontology_hash TEXT, excluded_asset_ids JSON, hashes JSON, frozen BOOLEAN,"
    " created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')))",
    "CREATE TABLE canonical_event (id TEXT PRIMARY KEY, event_type TEXT, aggregate_id TEXT,"
    " expected_revision INTEGER, payload JSON, actor_kind TEXT, actor_id TEXT,"
    " created_at DATETIME DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')))",
)

CLASSES: list[dict[str, Any]] = [{"stable_id": "item", "name": "item", "definition": "fixture"}]
CLASS_MAP = {"item": 0}
SCOPE = "scope-a"


def walsh(index: int) -> int:
    return sum(((index & bit).bit_count() % 2) << bit for bit in range(64))


def observation(
    index: int,
    *,
    metadata: Mapping[str, str] | None = None,
    embedding: tuple[float, ...] = (),
    embedding_model: str | None = None,
    embedding_version: str | None = None,
) -> TrustedObservation:
    return TrustedObservation(
        asset_id=f"asset-{index:02d}",
        sha256=f"{index + 1:064x}",
        phash=walsh(index),
        embedding=embedding,
        embedding_model=embedding_model,
        embedding_version=embedding_version,
        metadata=dict(metadata or {}),
        class_counts={"item": 1},
    )


def decision(
    left: int,
    right: int,
    kind: str = "same_group",
    actor_kind: str = "human",
) -> PairDecision:
    return PairDecision(
        left_asset_id=f"asset-{left:02d}",
        right_asset_id=f"asset-{right:02d}",
        decision=kind,  # type: ignore[arg-type]
        rationale="fixture adjudication",
        actor_kind=actor_kind,  # type: ignore[arg-type]
        actor_id="reviewer-1",
    )


# ---------------------------------------------------------------------------
# Pure closure tests
# ---------------------------------------------------------------------------


def test_transitive_closure_preserved_and_unrelated_decision_cannot_remove_it() -> None:
    # a-b share a session, b-c share an exact hash: one transitive group.
    observations = [
        observation(0, metadata={"session": "first"}),
        observation(1, metadata={"session": "first"}),
        TrustedObservation(
            asset_id="asset-02",
            sha256=observation(1).sha256,
            phash=walsh(2),
            class_counts={"item": 1},
        ),
        observation(3),
        observation(4),
    ]
    proposal = compute_proposal(
        observations,
        [decision(0, 2, "different_group")],
        old_groups={
            "asset-00": "g0",
            "asset-01": "g0",
            "asset-02": "g1",
            "asset-03": "g2",
            "asset-04": "g3",
        },
        old_splits={
            "asset-00": "train",
            "asset-01": "train",
            "asset-02": "train",
            "asset-03": "train",
            "asset-04": "val",
        },
    )
    assert proposal.groups["asset-00"] == proposal.groups["asset-01"]
    assert proposal.groups["asset-00"] == proposal.groups["asset-02"]
    assert ("asset-00", "asset-02") in proposal.conflicting_separations
    assert {relation.reason for relation in proposal.relations} >= {"metadata", "exact_hash"}


def test_related_same_group_decision_merges_and_unrelated_is_noop() -> None:
    observations = [observation(index) for index in range(5)]
    proposal = compute_proposal(
        observations,
        [decision(0, 1, "same_group"), decision(0, 3, "different_group")],
    )
    assert proposal.groups["asset-00"] == proposal.groups["asset-01"]
    assert proposal.groups["asset-00"] != proposal.groups["asset-03"]
    assert proposal.conflicting_separations == ()
    assert proposal.requires_fresh_holdout is False


def test_metadata_revision_changes_grouping() -> None:
    observations = [observation(index) for index in range(5)]
    revised = [
        observation(0, metadata={"session": "revised"}),
        observation(1, metadata={"session": "revised"}),
        observation(2),
        observation(3),
        observation(4),
    ]
    baseline = compute_proposal(observations)
    updated = compute_proposal(revised)
    assert baseline.groups["asset-00"] != baseline.groups["asset-01"]
    assert updated.groups["asset-00"] == updated.groups["asset-01"]
    assert updated.new_assignment_version != baseline.new_assignment_version


def test_absent_embedding_stays_absent_and_stale_embedding_rejected() -> None:
    observations = [observation(index) for index in range(5)]
    proposal = compute_proposal(observations)
    assert not any(item.reason == "embedding_cosine" for item in proposal.relations)

    vectors = [(1.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.0, 1.0), (1.0, 1.0)]
    embedded = [
        observation(
            index,
            embedding=vectors[index],
            embedding_model="approved-emb",
            embedding_version="v2",
        )
        for index in range(5)
    ]
    with pytest.raises(ValueError, match="approved pinned model"):
        compute_proposal(embedded)
    with pytest.raises(ValueError, match="stale or unapproved embedding version"):
        compute_proposal(
            embedded,
            thresholds=GroupingThresholds(embedding_model="approved-emb"),
            approved_embedding_version="v3",
        )
    accepted = compute_proposal(
        embedded,
        thresholds=GroupingThresholds(embedding_model="approved-emb"),
        approved_embedding_version="v2",
    )
    assert any(item.reason == "embedding_cosine" for item in accepted.relations)


def test_payload_mapping_rejects_unknown_fields_and_model_actors() -> None:
    item = observation_from_payload(
        {
            "asset_id": "asset-00",
            "sha256": "1" * 64,
            "phash": 7,
            "metadata": {"session": "s"},
            "class_counts": {"item": 2},
        }
    )
    assert item.asset_id == "asset-00"
    with pytest.raises(ValueError, match="unknown fields"):
        observation_from_payload({"asset_id": "x", "extra": 1})
    with pytest.raises(ValueError, match="model output"):
        pair_decision_from_payload(
            {
                "left_asset_id": "a",
                "right_asset_id": "b",
                "decision": "same_group",
                "rationale": "r",
                "actor_kind": "model",
                "actor_id": "worker",
            }
        )


def test_pure_proposal_is_deterministic_and_version_binds_decisions() -> None:
    observations = [observation(index) for index in range(5)]
    first = compute_proposal(observations, [decision(0, 1)])
    second = compute_proposal(list(reversed(observations)), [decision(1, 0)])
    assert first.groups == second.groups
    assert first.new_assignment_version == second.new_assignment_version
    assert compute_proposal(observations).new_assignment_version != first.new_assignment_version


# ---------------------------------------------------------------------------
# Disposable canonical database tests
# ---------------------------------------------------------------------------


class Fixture:
    def __init__(self, count: int = 5) -> None:
        self.engine = create_engine("sqlite://")
        with self.engine.begin() as connection:
            for statement in DDL:
                connection.execute(text(statement))
        self.project_id = uuid.uuid4()
        self.ontology_id = uuid.uuid4()
        self.keys = [f"asset-{index:02d}" for index in range(count)]
        self.asset_ids = {key: uuid.uuid4() for key in self.keys}
        from labelloop_core.grouping import AssetEvidence

        seed = assign_splits(
            [
                AssetEvidence(
                    asset_id=key,
                    sha256=f"{index + 1:064x}",
                    phash=walsh(index),
                    embedding=(),
                    metadata={},
                    class_counts={"item": 1},
                )
                for index, key in enumerate(self.keys)
            ]
        )
        self.version = seed.assignment_version
        self.seed_splits = dict(seed.asset_splits)
        with self.engine.begin() as connection:
            connection.execute(
                insert(project).values(
                    id=self.project_id,
                    project_key="domain-a",
                    workspace_id=uuid.uuid4(),
                    task="detect",
                    scope=SCOPE,
                    domain="fixture",
                    access_policy={},
                )
            )
            connection.execute(
                insert(ontology_release).values(
                    id=self.ontology_id,
                    ontology_key="onto",
                    project_id=self.project_id,
                    version=1,
                    classes=CLASSES,
                    class_map=CLASS_MAP,
                )
            )
            for index, key in enumerate(self.keys):
                connection.execute(
                    insert(source_asset).values(
                        id=self.asset_ids[key],
                        asset_key=key,
                        project_id=self.project_id,
                        sha256=f"{index + 1:064x}",
                        object_key=f"sha256/{index + 1:064x}"[:9] + "/" + f"{index + 1:064x}",
                        width=100,
                        height=100,
                        exif_orientation=1,
                        source_uri="fixture",
                    )
                )
                connection.execute(
                    insert(annotation_revision).values(
                        id=uuid.uuid4(),
                        asset_id=self.asset_ids[key],
                        revision=1,
                        actor_kind="human",
                        actor_id="reviewer-1",
                        objects=[],
                        approval_state="draft",
                        review_tier="unreviewed",
                        scope_completeness="unreviewed",
                        training_eligible=False,
                        gold_eligible=False,
                    )
                )
                connection.execute(
                    insert(source_split_assignment).values(
                        id=uuid.uuid4(),
                        assignment_version=self.version,
                        asset_id=self.asset_ids[key],
                        group_id=seed.group_ids[key],
                        split=seed.asset_splits[key],
                        seed=seed.seed,
                        algorithm=seed.algorithm,
                        thresholds={},
                        reason=seed.reasons[seed.group_ids[key]],
                        locked=True,
                    )
                )
            connection.execute(
                insert(canonical_dataset_release).values(
                    id=uuid.uuid4(),
                    release_key="release-1",
                    project_id=self.project_id,
                    ontology_id=self.ontology_id,
                    assignment_version=self.version,
                    sample_revisions={key: 1 for key in self.keys},
                    ontology_hash="0" * 64,
                    excluded_asset_ids={},
                    hashes={"manifest": "fixture-hash"},
                    frozen=True,
                )
            )

    def counts(self) -> dict[str, int]:
        with self.engine.connect() as connection:
            return {
                table: connection.execute(select(text(f"COUNT(*) FROM {table}"))).scalar_one()
                for table in (
                    "source_split_assignment",
                    "similarity_relation",
                    "borderline_review",
                    "canonical_event",
                    "canonical_dataset_release",
                )
            }

    def release_state(self) -> tuple[Any, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    canonical_dataset_release.c.sample_revisions,
                    canonical_dataset_release.c.hashes,
                )
            ).one()
            return row[0], row[1]

    def old_rows(self) -> list[Any]:
        with self.engine.connect() as connection:
            return connection.execute(
                select(
                    source_split_assignment.c.assignment_version,
                    source_split_assignment.c.group_id,
                    source_split_assignment.c.split,
                )
                .where(source_split_assignment.c.assignment_version == self.version)
                .order_by(source_split_assignment.c.group_id)
            ).all()


def observations(count: int = 5, **_changes: Any) -> list[TrustedObservation]:
    return [observation(index) for index in range(count)]


def pins_for(fixture: Fixture) -> LifecyclePins:
    return LifecyclePins(
        expected_assignment_version=fixture.version,
        expected_ontology_hash=completeness_context_hash(CLASS_MAP, CLASSES, SCOPE),
        expected_annotation_revisions={key: 1 for key in fixture.keys},
    )


def cross_split_merge_case() -> tuple[Fixture, list[TrustedObservation]]:
    fixture = Fixture()
    train_key = next(key for key in fixture.keys if fixture.seed_splits[key] == "train")
    val_key = next(key for key in fixture.keys if fixture.seed_splits[key] == "val")
    revised = [
        observation(index, metadata={"session": "cross-split"})
        if key in {train_key, val_key}
        else observation(index)
        for index, key in enumerate(fixture.keys)
    ]
    return fixture, revised


def test_propose_is_read_only_and_reports_quarantine() -> None:
    fixture, revised = cross_split_merge_case()
    before = fixture.counts()
    with fixture.engine.connect() as connection:
        proposal = propose_regroup(
            connection,
            project_id=fixture.project_id,
            observations=revised,
            pins=pins_for(fixture),
        )
    assert fixture.counts() == before
    assert proposal.requires_fresh_holdout is True
    assert proposal.cross_split_pairs
    assert [item.release_key for item in proposal.quarantined_releases] == ["release-1"]
    assert proposal.new_assignment_version != fixture.version


def test_apply_publishes_new_version_and_preserves_history() -> None:
    fixture, revised = cross_split_merge_case()
    release_before = fixture.release_state()
    old_before = fixture.old_rows()
    with fixture.engine.begin() as connection:
        published = apply_regroup(
            connection,
            project_id=fixture.project_id,
            observations=revised,
            pins=pins_for(fixture),
            actor_id="integrator-1",
        )
    assert published.published is True
    assert published.assignment_version != fixture.version
    assert fixture.release_state() == release_before
    assert fixture.old_rows() == old_before
    with fixture.engine.connect() as connection:
        new_rows = connection.execute(
            select(source_split_assignment.c.asset_id).where(
                source_split_assignment.c.assignment_version == published.assignment_version
            )
        ).all()
        events = connection.execute(
            select(canonical_event.c.event_type).where(
                canonical_event.c.event_type == "source_grouping_published"
            )
        ).all()
    assert len(new_rows) == len(fixture.keys)
    assert events == [("source_grouping_published",)]


def test_later_ingest_admission() -> None:
    fixture = Fixture(count=4)
    new_key = "asset-04"
    new_id = uuid.uuid4()
    with fixture.engine.begin() as connection:
        connection.execute(
            insert(source_asset).values(
                id=new_id,
                asset_key=new_key,
                project_id=fixture.project_id,
                sha256="5" * 64,
                object_key="sha256/55/" + "5" * 64,
                width=100,
                height=100,
                exif_orientation=1,
                source_uri="fixture",
            )
        )
        connection.execute(
            insert(annotation_revision).values(
                id=uuid.uuid4(),
                asset_id=new_id,
                revision=1,
                actor_kind="human",
                actor_id="reviewer-1",
                objects=[],
                approval_state="draft",
                review_tier="unreviewed",
                scope_completeness="unreviewed",
                training_eligible=False,
                gold_eligible=False,
            )
        )
    admitted = [
        *observations(4),
        TrustedObservation(
            asset_id=new_key, sha256="5" * 64, phash=walsh(4), class_counts={"item": 1}
        ),
    ]
    with fixture.engine.begin() as connection:
        published = apply_regroup(
            connection,
            project_id=fixture.project_id,
            observations=admitted,
            pins=LifecyclePins(expected_assignment_version=fixture.version),
            actor_id="integrator-1",
        )
    assert published.published is True
    assert published.groups[new_key]


def test_same_split_admission_changes_membership_without_quarantine() -> None:
    # A new source joining a train group changes that group's membership but
    # creates no training/holdout connection, so frozen releases (release-1)
    # must not be quarantined.
    fixture = Fixture(count=4)
    new_key = "asset-04"
    new_id = uuid.uuid4()
    with fixture.engine.begin() as connection:
        connection.execute(
            insert(source_asset).values(
                id=new_id,
                asset_key=new_key,
                project_id=fixture.project_id,
                sha256="5" * 64,
                object_key="sha256/55/" + "5" * 64,
                width=100,
                height=100,
                exif_orientation=1,
                source_uri="fixture",
            )
        )
    train_index = next(
        index for index, key in enumerate(fixture.keys) if fixture.seed_splits[key] == "train"
    )
    admitted = [
        *observations(4),
        TrustedObservation(
            asset_id=new_key, sha256="5" * 64, phash=walsh(4), class_counts={"item": 1}
        ),
    ]
    with fixture.engine.connect() as connection:
        proposal = propose_regroup(
            connection,
            project_id=fixture.project_id,
            observations=admitted,
            decisions=[decision(train_index, 4, "same_group")],
            pins=pins_for(fixture),
        )
    assert proposal.changed_memberships != ()
    assert proposal.cross_split_pairs == ()
    assert proposal.split_transitions == ()
    assert proposal.quarantined_releases == ()
    assert proposal.requires_fresh_holdout is False


def test_stale_pins_rejected() -> None:
    fixture = Fixture()
    with fixture.engine.connect() as connection:
        with pytest.raises(ValueError, match="stale assignment pin"):
            propose_regroup(
                connection,
                project_id=fixture.project_id,
                observations=observations(),
                pins=LifecyclePins(expected_assignment_version="stale"),
            )
        with pytest.raises(ValueError, match="stale ontology pin"):
            propose_regroup(
                connection,
                project_id=fixture.project_id,
                observations=observations(),
                pins=LifecyclePins(
                    expected_assignment_version=fixture.version,
                    expected_ontology_hash="f" * 64,
                ),
            )
        with pytest.raises(ValueError, match="stale annotation pin"):
            propose_regroup(
                connection,
                project_id=fixture.project_id,
                observations=observations(),
                pins=LifecyclePins(
                    expected_assignment_version=fixture.version,
                    expected_annotation_revisions={fixture.keys[0]: 99},
                ),
            )


def test_repeat_publication_noop_and_old_pins_become_stale() -> None:
    fixture, revised = cross_split_merge_case()
    with fixture.engine.begin() as connection:
        first = apply_regroup(
            connection,
            project_id=fixture.project_id,
            observations=revised,
            pins=pins_for(fixture),
            actor_id="integrator-1",
        )
    before = fixture.counts()
    with fixture.engine.begin() as connection:
        repeat = apply_regroup(
            connection,
            project_id=fixture.project_id,
            observations=revised,
            pins=LifecyclePins(expected_assignment_version=first.assignment_version),
            actor_id="integrator-1",
        )
    assert repeat.published is False
    assert repeat.assignment_version == first.assignment_version
    assert fixture.counts() == before
    # Concurrent publication that raced with the first commit is stale.
    with fixture.engine.begin() as connection:
        with pytest.raises(ValueError, match="stale assignment pin"):
            apply_regroup(
                connection,
                project_id=fixture.project_id,
                observations=revised,
                pins=pins_for(fixture),
                actor_id="integrator-1",
            )


def test_rollback_on_audit_failure() -> None:
    fixture, revised = cross_split_merge_case()
    before = fixture.counts()

    def fail() -> None:
        raise RuntimeError("audit unavailable")

    with fixture.engine.begin() as connection:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            apply_regroup(
                connection,
                project_id=fixture.project_id,
                observations=revised,
                pins=pins_for(fixture),
                actor_id="integrator-1",
                audit_hook=fail,
            )
    assert fixture.counts() == before
    assert fixture.old_rows()


def test_persisted_decisions_reused_without_resupply() -> None:
    fixture = Fixture()
    with fixture.engine.begin() as connection:
        published = apply_regroup(
            connection,
            project_id=fixture.project_id,
            observations=observations(),
            decisions=[decision(0, 1, "same_group")],
            pins=pins_for(fixture),
            actor_id="integrator-1",
        )
    assert published.groups["asset-00"] == published.groups["asset-01"]
    with fixture.engine.connect() as connection:
        proposal = propose_regroup(
            connection,
            project_id=fixture.project_id,
            observations=observations(),
            pins=LifecyclePins(expected_assignment_version=published.assignment_version),
        )
    assert proposal.groups["asset-00"] == proposal.groups["asset-01"]
    assert proposal.new_assignment_version == published.assignment_version


def test_unrelated_different_group_apply_never_removes_automatic_relation() -> None:
    fixture = Fixture()
    revised = [
        observation(0, metadata={"session": "auto"}),
        observation(1, metadata={"session": "auto"}),
        observation(2),
        observation(3),
        observation(4),
    ]
    with fixture.engine.begin() as connection:
        published = apply_regroup(
            connection,
            project_id=fixture.project_id,
            observations=revised,
            decisions=[decision(0, 1, "different_group")],
            pins=pins_for(fixture),
            actor_id="integrator-1",
        )
    assert published.groups["asset-00"] == published.groups["asset-01"]
    with fixture.engine.connect() as connection:
        relations = connection.execute(select(similarity_relation.c.reason)).all()
    assert ("metadata",) in relations


# ---------------------------------------------------------------------------
# QA repair regressions (findings 1-10)
# ---------------------------------------------------------------------------


def test_unchanged_memberships_preserve_splits_including_test_holdout() -> None:
    observations = [observation(index) for index in range(5)]
    old_splits = {
        "asset-00": "train",
        "asset-01": "train",
        "asset-02": "train",
        "asset-03": "val",
        "asset-04": "test",
    }
    old_groups = {key: f"g-{index}" for index, key in enumerate(old_splits)}
    proposal = compute_proposal(
        observations, old_groups=old_groups, old_splits=old_splits, seed=999
    )
    assert proposal.assignment is not None
    assert dict(proposal.assignment.asset_splits) == old_splits
    assert proposal.split_transitions == ()
    assert proposal.requires_fresh_holdout is False
    assert proposal.changed_memberships == ()


def test_invalid_validation_fraction_rejected_even_with_locked_splits() -> None:
    observations = [observation(index) for index in range(5)]
    old_splits = {f"asset-{index:02d}": "train" for index in range(4)}
    old_splits["asset-04"] = "val"
    old_groups = {key: key for key in old_splits}
    for fraction in (0.0, 1.0):
        with pytest.raises(ValueError, match="validation_fraction"):
            compute_proposal(
                observations,
                old_groups=old_groups,
                old_splits=old_splits,
                validation_fraction=fraction,
            )


def test_synthetic_session_tag_cannot_merge_unrelated_singleton() -> None:
    observations = [
        observation(0),
        observation(1),
        observation(2, metadata={"session": "__lifecycle_decision_0"}),
        observation(3, metadata={"session": "independent"}),
    ]
    proposal = compute_proposal(observations, [decision(0, 1, "same_group")])
    assert proposal.groups["asset-00"] == proposal.groups["asset-01"]
    assert proposal.groups["asset-02"] != proposal.groups["asset-00"]
    assert proposal.groups["asset-03"] != proposal.groups["asset-00"]
    assert proposal.groups["asset-02"] != proposal.groups["asset-03"]


def test_complete_collapse_reports_quarantine_without_assignment() -> None:
    observations = [
        observation(0, metadata={"session": "all"}),
        observation(1, metadata={"session": "all"}),
    ]
    proposal = compute_proposal(
        observations,
        old_groups={"asset-00": "g0", "asset-01": "g1"},
        old_splits={"asset-00": "train", "asset-01": "val"},
    )
    assert proposal.assignment is None
    assert proposal.new_assignment_version is None
    assert proposal.cross_split_pairs == (("asset-00", "asset-01"),)
    assert proposal.requires_fresh_holdout is True


def test_borderline_band_and_embedding_pin_change_publication_identity() -> None:
    base = [
        TrustedObservation(asset_id=f"asset-0{i}", sha256=f"{i + 1:064x}", phash=0)
        for i in range(3)
    ]
    banded = [
        base[0],
        TrustedObservation(asset_id="asset-01", sha256=base[1].sha256, phash=0b1111111111),
        base[2],
    ]
    outside = [
        base[0],
        TrustedObservation(
            asset_id="asset-01", sha256=base[1].sha256, phash=0b11111111111111111111
        ),
        base[2],
    ]
    assert (
        compute_proposal(banded).new_assignment_version
        != compute_proposal(outside).new_assignment_version
    )

    vectors = [(1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]

    def embedded_for(version: str) -> list[TrustedObservation]:
        return [
            observation(
                index,
                embedding=vectors[index],
                embedding_model="approved-emb",
                embedding_version=version,
            )
            for index in range(3)
        ]

    thresholds = GroupingThresholds(embedding_model="approved-emb")
    assert (
        compute_proposal(
            embedded_for("v2"), thresholds=thresholds, approved_embedding_version="v2"
        ).new_assignment_version
        != compute_proposal(
            embedded_for("v3"), thresholds=thresholds, approved_embedding_version="v3"
        ).new_assignment_version
    )


def test_unversioned_embeddings_rejected() -> None:
    vectors = [(1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
    thresholds = GroupingThresholds(embedding_model="approved-emb")
    for version in (None, ""):
        unversioned = [
            observation(
                index,
                embedding=vectors[index],
                embedding_model="approved-emb",
                embedding_version=version,
            )
            for index in range(3)
        ]
        with pytest.raises(ValueError, match="pinned embedding version"):
            compute_proposal(unversioned, thresholds=thresholds, approved_embedding_version="v2")
    with pytest.raises(ValueError, match="pinned embedding version"):
        compute_proposal(
            [
                observation(
                    index,
                    embedding=vectors[index],
                    embedding_model="approved-emb",
                    embedding_version="v2",
                )
                for index in range(3)
            ],
            thresholds=thresholds,
        )


def test_duplicate_decisions_are_idempotent() -> None:
    observations = [observation(index) for index in range(5)]
    single = compute_proposal(observations, [decision(0, 1)])
    duplicate = compute_proposal(observations, [decision(0, 1), decision(1, 0)])
    assert single.new_assignment_version == duplicate.new_assignment_version


def test_historical_release_quarantined_across_assignment_versions() -> None:
    fixture = Fixture()
    with fixture.engine.begin() as connection:
        v2 = apply_regroup(
            connection,
            project_id=fixture.project_id,
            observations=observations(),
            decisions=[decision(0, 1, "same_group")],
            pins=pins_for(fixture),
            actor_id="integrator-1",
        )
    assert v2.assignment_version != fixture.version
    train_key = next(key for key in fixture.keys if fixture.seed_splits[key] == "train")
    val_key = next(key for key in fixture.keys if fixture.seed_splits[key] == "val")
    revised = [
        observation(index, metadata={"session": "cross-split"})
        if key in {train_key, val_key}
        else observation(index)
        for index, key in enumerate(fixture.keys)
    ]
    with fixture.engine.connect() as connection:
        proposal = propose_regroup(
            connection,
            project_id=fixture.project_id,
            observations=revised,
            pins=LifecyclePins(expected_assignment_version=v2.assignment_version),
        )
    # release-1 is pinned to V1 (fixture.version), not the immediate
    # predecessor V2, and must still be quarantined.
    assert [item.release_key for item in proposal.quarantined_releases] == ["release-1"]
    assert proposal.requires_fresh_holdout is True


def test_canonical_hash_mismatch_rejected_and_exact_duplicate_grouped() -> None:
    fixture = Fixture()
    forged = [
        TrustedObservation(asset_id="asset-00", sha256="f" * 64, phash=walsh(0))
        if index == 0
        else observation(index)
        for index in range(5)
    ]
    with fixture.engine.connect() as connection:
        with pytest.raises(ValueError, match="canonical source hash"):
            propose_regroup(
                connection,
                project_id=fixture.project_id,
                observations=forged,
                pins=pins_for(fixture),
            )
    # Two canonical sources with equal stored SHA-256 must gain the
    # mandatory exact-hash relation.
    with fixture.engine.begin() as connection:
        duplicated_hash = "0" * 63 + "1"
        connection.execute(
            text("UPDATE source_asset SET sha256 = :draft WHERE asset_key = 'asset-01'"),
            {"draft": duplicated_hash},
        )
    duplicated = [
        TrustedObservation(
            asset_id="asset-01",
            sha256="0" * 63 + "1",
            phash=walsh(1),
            class_counts={"item": 1},
        )
        if index == 1
        else observation(index)
        for index in range(5)
    ]
    with fixture.engine.connect() as connection:
        proposal = propose_regroup(
            connection,
            project_id=fixture.project_id,
            observations=duplicated,
            pins=pins_for(fixture),
        )
    assert proposal.groups["asset-00"] == proposal.groups["asset-01"]
    assert any(
        item.reason == "exact_hash"
        and {item.left_asset_id, item.right_asset_id} == {"asset-00", "asset-01"}
        for item in proposal.relations
    )


def test_apply_fails_closed_on_complete_collapse() -> None:
    fixture = Fixture()
    collapsed = [observation(index, metadata={"session": "all"}) for index in range(5)]
    with fixture.engine.connect() as connection:
        proposal = propose_regroup(
            connection,
            project_id=fixture.project_id,
            observations=collapsed,
            pins=pins_for(fixture),
        )
    assert proposal.assignment is None
    assert proposal.requires_fresh_holdout is True
    before = fixture.counts()
    with fixture.engine.begin() as connection:
        with pytest.raises(ValueError, match="usable train/val"):
            apply_regroup(
                connection,
                project_id=fixture.project_id,
                observations=collapsed,
                pins=pins_for(fixture),
                actor_id="integrator-1",
            )
    assert fixture.counts() == before


def test_duplicate_decision_repeat_apply_is_noop() -> None:
    fixture = Fixture()
    with fixture.engine.begin() as connection:
        first = apply_regroup(
            connection,
            project_id=fixture.project_id,
            observations=observations(),
            decisions=[decision(0, 1), decision(0, 1)],
            pins=pins_for(fixture),
            actor_id="integrator-1",
        )
    before = fixture.counts()
    with fixture.engine.begin() as connection:
        repeat = apply_regroup(
            connection,
            project_id=fixture.project_id,
            observations=observations(),
            decisions=[decision(0, 1), decision(0, 1)],
            pins=LifecyclePins(expected_assignment_version=first.assignment_version),
            actor_id="integrator-1",
        )
    assert repeat.published is False
    assert repeat.assignment_version == first.assignment_version
    assert fixture.counts() == before


def test_return_to_historical_grouping_reuses_immutable_version() -> None:
    fixture = Fixture()
    with fixture.engine.begin() as connection:
        a = apply_regroup(
            connection,
            project_id=fixture.project_id,
            observations=observations(),
            pins=pins_for(fixture),
            actor_id="integrator-1",
        )
        b = apply_regroup(
            connection,
            project_id=fixture.project_id,
            observations=observations(),
            seed=20261108,
            pins=LifecyclePins(expected_assignment_version=a.assignment_version),
            actor_id="integrator-1",
        )
        assert b.assignment_version != a.assignment_version
        before = fixture.counts()
        restored = apply_regroup(
            connection,
            project_id=fixture.project_id,
            observations=observations(),
            pins=LifecyclePins(expected_assignment_version=b.assignment_version),
            actor_id="integrator-1",
        )
    assert restored.assignment_version == a.assignment_version
    assert restored.published is True
    assert fixture.counts()["source_split_assignment"] == before["source_split_assignment"]
    with fixture.engine.connect() as connection:
        proposal = propose_regroup(
            connection,
            project_id=fixture.project_id,
            observations=observations(),
            pins=LifecyclePins(expected_assignment_version=a.assignment_version),
        )
    assert proposal.new_assignment_version == a.assignment_version


# ---------------------------------------------------------------------------
# Disposable canonical PostgreSQL coverage (Actions queue-postgres job)
# ---------------------------------------------------------------------------

PG_ONLY = pytest.mark.skipif(
    os.environ.get("LABELLOOP_QUEUE_TESTS") != "1",
    reason="requires disposable Actions PostgreSQL",
)


class PgFixture:
    def __init__(self, count: int = 5) -> None:
        from labelloop_api.schema import metadata as canonical_metadata

        url = os.environ["DATABASE_URL"]
        self.engine = create_engine(url)
        # Reset via schema DROP CASCADE: plain metadata.drop_all fails on
        # the forward FKs (training_run/evaluation_run -> yolo_dataset_variant)
        # when the disposable database is reused across tests.
        with self.engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        canonical_metadata.create_all(self.engine)
        self.project_id = uuid.uuid4()
        self.ontology_id = uuid.uuid4()
        self.keys = [f"asset-{index:02d}" for index in range(count)]
        self.asset_ids = {key: uuid.uuid4() for key in self.keys}
        from labelloop_core.grouping import AssetEvidence

        seed = assign_splits(
            [
                AssetEvidence(
                    asset_id=key,
                    sha256=f"{index + 1:064x}",
                    phash=walsh(index),
                    embedding=(),
                    metadata={},
                    class_counts={"item": 1},
                )
                for index, key in enumerate(self.keys)
            ]
        )
        self.version = seed.assignment_version
        with self.engine.begin() as connection:
            connection.execute(
                insert(project).values(
                    id=self.project_id,
                    project_key="domain-pg",
                    workspace_id=uuid.uuid4(),
                    task="detect",
                    scope=SCOPE,
                    domain="fixture",
                    access_policy={},
                )
            )
            connection.execute(
                insert(ontology_release).values(
                    id=self.ontology_id,
                    ontology_key="onto",
                    project_id=self.project_id,
                    version=1,
                    classes=CLASSES,
                    class_map=CLASS_MAP,
                )
            )
            for index, key in enumerate(self.keys):
                sha = f"{index + 1:064x}"
                connection.execute(
                    insert(source_asset).values(
                        id=self.asset_ids[key],
                        asset_key=key,
                        project_id=self.project_id,
                        sha256=sha,
                        object_key=f"sha256/{sha[:2]}/{sha}",
                        width=100,
                        height=100,
                        exif_orientation=1,
                        source_uri="fixture",
                    )
                )
                connection.execute(
                    insert(source_split_assignment).values(
                        id=uuid.uuid4(),
                        assignment_version=self.version,
                        asset_id=self.asset_ids[key],
                        group_id=seed.group_ids[key],
                        split=seed.asset_splits[key],
                        seed=seed.seed,
                        algorithm=seed.algorithm,
                        thresholds={},
                        reason=seed.reasons[seed.group_ids[key]],
                        locked=True,
                    )
                )

    def dispose(self) -> None:
        self.engine.dispose()


@PG_ONLY
def test_postgres_two_publications_one_transaction_second_is_current() -> None:
    fixture = PgFixture()
    try:
        with fixture.engine.begin() as connection:
            first = apply_regroup(
                connection,
                project_id=fixture.project_id,
                observations=observations(),
                pins=LifecyclePins(expected_assignment_version=fixture.version),
                actor_id="integrator-1",
            )
            second = apply_regroup(
                connection,
                project_id=fixture.project_id,
                observations=observations(),
                seed=20261108,
                pins=LifecyclePins(expected_assignment_version=first.assignment_version),
                actor_id="integrator-1",
            )
            # Both publications share now(); the second must still be
            # current (publication sequence, not timestamp/lexical order).
            # The seed is bound into the version identity, so the proposal
            # must replay the second publication's seed to prove the
            # published version is the current one.
            proposal = propose_regroup(
                connection,
                project_id=fixture.project_id,
                observations=observations(),
                seed=20261108,
                pins=LifecyclePins(expected_assignment_version=second.assignment_version),
            )
            assert proposal.new_assignment_version == second.assignment_version
            with pytest.raises(ValueError, match="stale assignment pin"):
                propose_regroup(
                    connection,
                    project_id=fixture.project_id,
                    observations=observations(),
                    pins=LifecyclePins(expected_assignment_version=first.assignment_version),
                )
    finally:
        fixture.dispose()


@PG_ONLY
def test_postgres_return_to_historical_version_respects_uniqueness() -> None:
    fixture = PgFixture()
    try:
        with fixture.engine.begin() as connection:
            a = apply_regroup(
                connection,
                project_id=fixture.project_id,
                observations=observations(),
                pins=LifecyclePins(expected_assignment_version=fixture.version),
                actor_id="integrator-1",
            )
            b = apply_regroup(
                connection,
                project_id=fixture.project_id,
                observations=observations(),
                seed=20261108,
                pins=LifecyclePins(expected_assignment_version=a.assignment_version),
                actor_id="integrator-1",
            )
            restored = apply_regroup(
                connection,
                project_id=fixture.project_id,
                observations=observations(),
                pins=LifecyclePins(expected_assignment_version=b.assignment_version),
                actor_id="integrator-1",
            )
            assert restored.assignment_version == a.assignment_version
            assert restored.published is True
            rows = connection.execute(
                select(source_split_assignment.c.assignment_version).where(
                    source_split_assignment.c.assignment_version == a.assignment_version
                )
            ).all()
            assert len(rows) == len(fixture.keys)
    finally:
        fixture.dispose()
