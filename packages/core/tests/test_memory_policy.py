"""Synthetic, pixel-free fixtures for two independent non-game catalogs."""

import hashlib
from dataclasses import replace
from itertools import permutations

import pytest

from labelloop_core.domain import (
    AnnotationRevision,
    ClassDefinition,
    DatasetObject,
    OntologyRelease,
    ReferenceExample,
    SourceAsset,
    SourceSplitAssignment,
    Split,
)
from labelloop_core.evidence import EvidenceScope
from labelloop_core.geometry import Box
from labelloop_core.invalidation import (
    EvidenceArtifact,
    append_invalidation,
    plan_invalidation,
)
from labelloop_core.lineage import LineageGraph
from labelloop_core.references import (
    MemoryExample,
    ReferenceImport,
    ReferencePack,
    ReferenceQuery,
    collection_distributions,
    retrieve_references,
)
from labelloop_core.selection import (
    SIGNALS,
    SelectionCandidate,
    SelectionPolicy,
    SelectionSignals,
    select_evidence,
)


def scope_for(project: str = "office", class_id: str = "keyboard") -> EvidenceScope:
    sources = []
    for name in ("zero", "missing", "easy-a", "easy-b", "novel", "holdout", "test"):
        digest = hashlib.sha256(f"{project}:{name}".encode()).hexdigest()
        sources.append(SourceAsset(name, project, digest, f"sha256/{digest[:2]}/{digest}", 100, 80))
    splits: dict[str, Split] = {asset.asset_id: "train" for asset in sources}
    splits["holdout"] = "val"
    splits["test"] = "test"
    return EvidenceScope(
        OntologyRelease(
            "catalog",
            project,
            1,
            (ClassDefinition(class_id, class_id.title(), "visible physical object"),),
            {class_id: 0},
        ),
        SourceSplitAssignment(
            "split-v1", splits, splits.copy(), {name: name for name in splits}, 3, "fixture", {}
        ),
        tuple(sources),
        {"tile": "holdout", "resized-tile": "tile", "train-tile": "easy-a"},
    )


def revision_for(scope: EvidenceScope, *, auto: bool = False) -> AnnotationRevision:
    class_id = next(iter(scope.ontology.class_map))
    return AnnotationRevision(
        1,
        (DatasetObject("object", class_id, Box(1, 2, 15, 20), occluded=True),),
        "system" if auto else "human",
        approval_state="auto_accepted" if auto else "human_accepted",
        review_tier="machine_verified_no_human_review" if auto else "adjudicated",
        scope_completeness="exhaustive_for_scope",
        training_eligible=True,
    )


def signals(**values: float) -> SelectionSignals:
    return SelectionSignals(**{key: values.get(key, 0.0) for key in SIGNALS})


def policy() -> SelectionPolicy:
    return SelectionPolicy(
        "policy-v1",
        "signals-v1",
        7,
        dict.fromkeys(SIGNALS, 1.0),
        4,
        100,
        8,
        1,
        1,
        1,
        0.5,
        0.1,
        1,
        2,
        0.5,
    )


def candidates_for(scope: EvidenceScope) -> tuple[SelectionCandidate, ...]:
    revision = revision_for(scope, auto=True)
    return tuple(
        SelectionCandidate(
            name,
            revision,
            f"evidence:{name}",
            "signals-v1",
            score,
            detections,
            name,
            "teacher-2",
            2,
        )
        for name, score, detections in (
            ("zero", signals(), 0),
            ("missing", signals(omission=0.9), 2),
            ("easy-a", signals(), 1),
            ("easy-b", signals(), 1),
            ("novel", signals(novelty=0.9), 1),
        )
    )


@pytest.mark.parametrize(("project", "class_id"), [("office", "keyboard"), ("wildlife", "ibis")])
def test_selection_is_stable_input_bound_and_keeps_missing_zero_and_random_strata(
    project: str, class_id: str
) -> None:
    scope = scope_for(project, class_id)
    candidates = candidates_for(scope)
    result = select_evidence(scope, candidates, policy())
    assert result.used_units == 8
    assert [item.stratum for item in result.items] == [
        "zero_detection",
        "missing_object",
        "ordinary_random",
        "ranked",
    ]
    assert result.items[0].asset_id == "zero"
    assert result.items[1].asset_id == "missing"
    assert result.items[2].asset_id in {"easy-a", "easy-b"}
    assert result.items[3].asset_id == "novel"
    for reordered in permutations(candidates):
        assert select_evidence(scope, reordered, policy()) == result
    reordered_scope = replace(scope, sources=tuple(reversed(scope.sources)))
    assert select_evidence(reordered_scope, candidates, policy()) == result
    assert select_evidence(scope, candidates, replace(policy(), version="v2")).input_sha256 != (
        result.input_sha256
    )
    with pytest.raises(ValueError, match="evidence version"):
        select_evidence(scope, candidates, replace(policy(), signal_version="v2"))
    changed = (replace(candidates[0], evidence_id="new-probe"), *candidates[1:])
    assert select_evidence(scope, changed, policy()).input_sha256 != result.input_sha256


def test_random_audit_is_seeded_not_lowest_confidence_or_input_order() -> None:
    scope = scope_for()
    candidates = candidates_for(scope)
    results = {
        select_evidence(scope, candidates, replace(policy(), seed=seed)).items[2].asset_id
        for seed in range(20)
    }
    assert results == {"easy-a", "easy-b"}
    assert all(item.revision.training_eligible for item in candidates)


@pytest.mark.parametrize("signal", sorted(SIGNALS))
def test_every_declared_signal_changes_ranked_selection(signal: str) -> None:
    scope = scope_for()
    candidates = candidates_for(scope)
    changed = (*candidates[:4], replace(candidates[4], signals=signals(**{signal: 1.0})))
    result = select_evidence(scope, changed, policy())
    chosen = next(item for item in result.items if item.asset_id == "novel")
    assert chosen.stratum == ("missing_object" if signal == "omission" else "ranked")


@pytest.mark.parametrize(
    "asset_id", ["holdout", "test", "tile", "resized-tile", "train-tile", "absent"]
)
def test_selection_refuses_holdouts_derivatives_and_missing_provenance(asset_id: str) -> None:
    scope = scope_for()
    candidates = candidates_for(scope)
    contaminated = (replace(candidates[0], asset_id=asset_id), *candidates[1:])
    with pytest.raises(ValueError, match="evaluation|derivative|missing canonical"):
        select_evidence(scope, contaminated, policy())


def test_additional_holdout_quarantines_the_entire_source_group() -> None:
    scope = scope_for()
    assignment = replace(
        scope.assignment, group_ids={**scope.assignment.group_ids, "easy-b": "easy-a"}
    )
    quarantined = replace(scope, assignment=assignment, evaluation_asset_ids=frozenset({"easy-b"}))
    with pytest.raises(ValueError, match="evaluation"):
        quarantined.require_training_source("easy-a")
    with pytest.raises(ValueError, match="evaluation"):
        select_evidence(quarantined, candidates_for(scope), policy())


def test_scope_refuses_duplicate_hashes_and_incomplete_or_cyclic_inventory() -> None:
    scope = scope_for()
    duplicate = replace(
        scope.sources[-1], sha256=scope.sources[0].sha256, object_key=scope.sources[0].object_key
    )
    with pytest.raises(ValueError, match="duplicates"):
        replace(scope, sources=(*scope.sources[:-1], duplicate))
    with pytest.raises(ValueError, match="complete"):
        replace(scope, sources=scope.sources[:-1])
    with pytest.raises(ValueError, match="acyclic"):
        replace(scope, derivative_sources={"a": "b", "b": "a"})
    with pytest.raises(ValueError, match="masquerade"):
        replace(scope, derivative_sources={"easy-a": "holdout"})
    with pytest.raises(ValueError, match="locked"):
        replace(scope, assignment=replace(scope.assignment, locked=False))


@pytest.mark.parametrize(
    "change", ["no-zero", "no-omission", "no-ordinary", "cost", "cluster", "group"]
)
def test_insufficient_evidence_or_resources_never_weakens_mandatory_quotas(change: str) -> None:
    scope = scope_for()
    candidates = candidates_for(scope)
    selected_policy = policy()
    if change == "no-zero":
        candidates = tuple(replace(item, detection_count=1) for item in candidates)
    elif change == "no-omission":
        candidates = tuple(replace(item, signals=signals()) for item in candidates)
    elif change == "no-ordinary":
        candidates = tuple(
            replace(item, revision=AnnotationRevision(1, (), "model")) for item in candidates
        )
    elif change == "cost":
        selected_policy = replace(selected_policy, budget_units=5)
    elif change == "cluster":
        candidates = tuple(replace(item, cluster_id="same") for item in candidates)
        selected_policy = replace(selected_policy, max_per_cluster=1)
    else:
        scope = replace(
            scope,
            assignment=replace(
                scope.assignment,
                group_ids={
                    **scope.assignment.group_ids,
                    **dict.fromkeys((item.asset_id for item in candidates), "zero"),
                },
            ),
        )
    with pytest.raises(ValueError, match="insufficient"):
        select_evidence(scope, candidates, selected_policy)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_malformed_signals_are_not_evidence(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        signals(omission=value)


def memory_for(scope: EvidenceScope, asset_id: str = "easy-a") -> MemoryExample:
    return MemoryExample(
        ReferenceExample(
            f"ref:{asset_id}",
            asset_id,
            next(iter(scope.ontology.class_map)),
            "independent-review-1",
            True,
            "physical-scene",
            Box(0, 0, 30, 30),
        ),
        revision_for(scope),
        "anchor",
        None,
        "indoor",
        asset_id,
        frozenset({"daylight", "front"}),
        "positive",
        0.9,
    )


def query_for(scope: EvidenceScope) -> ReferenceQuery:
    return ReferenceQuery(
        frozenset(scope.ontology.class_map), "physical-scene", frozenset({"daylight"}), 3, 2, 1
    )


def test_reference_enrollment_preserves_collections_teacher_and_distributions() -> None:
    scope = scope_for()
    anchor = memory_for(scope)
    automatic = replace(
        memory_for(scope, "easy-b"),
        revision=revision_for(scope, auto=True),
        collection="auto_expanded",
        teacher_generation="teacher-4",
    )
    gold = replace(
        memory_for(scope, "holdout"),
        collection="gold",
        revision=replace(revision_for(scope), gold_eligible=True),
    )
    pack = ReferencePack("approved", "1", scope, (anchor, automatic, gold))
    distributions = collection_distributions(pack, small_max_px=16, medium_max_px=32)
    assert [row.collection for row in distributions] == ["anchor", "auto_expanded", "gold"]
    assert [row.examples for row in distributions] == [1, 1, 1]
    assert distributions[1].teacher_counts == {"teacher-4": 1}
    assert distributions[0].class_counts == {"keyboard": 1}
    assert distributions[0].scale_counts == {"small": 1}
    assert distributions[0].scene_counts == {"indoor": 1}
    assert distributions[0].occluded_objects == 1
    retrieved = retrieve_references(scope, (pack,), query_for(scope))
    assert {item.reference_id for item in retrieved} == {"ref:easy-a", "ref:easy-b"}
    assert (
        retrieve_references(
            scope, (replace(pack, examples=tuple(reversed(pack.examples))),), query_for(scope)
        )
        == retrieved
    )


@pytest.mark.parametrize("asset_id", ["holdout", "test", "tile", "train-tile"])
def test_holdouts_and_derivatives_cannot_be_enrolled_as_training_references(asset_id: str) -> None:
    scope = scope_for()
    with pytest.raises(ValueError, match="evaluation|canonical"):
        ReferencePack("bad", "1", scope, (memory_for(scope, asset_id),))


def test_predictions_and_unapproved_or_wrong_scope_references_are_never_retrieved() -> None:
    scope = scope_for()
    anchor = memory_for(scope)
    with pytest.raises(ValueError, match="eligibility"):
        replace(anchor, revision=replace(anchor.revision, training_eligible=False))
    with pytest.raises(ValueError, match="teacher"):
        replace(anchor, collection="auto_expanded", revision=revision_for(scope, auto=True))
    for ref in (
        replace(anchor.reference, approved=False),
        replace(anchor.reference, validity_scope="hud"),
    ):
        pack = ReferencePack("pack", "1", scope, (replace(anchor, reference=ref),))
        with pytest.raises(ValueError, match="insufficient"):
            retrieve_references(scope, (pack,), query_for(scope))
    pack = ReferencePack("pack", "1", scope, (anchor,))
    with pytest.raises(ValueError, match="insufficient"):
        retrieve_references(
            scope, (pack,), replace(query_for(scope), required_tags=frozenset({"night"}))
        )


def import_for(source: EvidenceScope, target: EvidenceScope) -> ReferenceImport:
    return ReferenceImport(
        "import",
        "1",
        source.ontology.project_id,
        "pack",
        "1",
        target.ontology.project_id,
        target.ontology.ontology_id,
        target.ontology.version,
        {next(iter(source.ontology.class_map)): next(iter(target.ontology.class_map))},
        "review-principal",
    )


def test_cross_project_reuse_requires_exact_versioned_import_and_complete_mapping() -> None:
    source = scope_for()
    target = scope_for("wildlife", "ibis")
    pack = ReferencePack("pack", "1", source, (memory_for(source),))
    approved = import_for(source, target)
    for imports in (
        (),
        (replace(approved, pack_version="old"),),
        (replace(approved, target_ontology_version=2),),
    ):
        with pytest.raises(ValueError, match="versioned import"):
            retrieve_references(target, (pack,), query_for(target), imports)
    with pytest.raises(ValueError, match="complete source"):
        retrieve_references(
            target,
            (pack,),
            query_for(target),
            (replace(approved, class_mapping={"wrong": "ibis"}),),
        )
    result = retrieve_references(target, (pack,), query_for(target), (approved,))
    assert result[0].target_class_id == "ibis"
    assert result[0].import_id == "import" and result[0].import_version == "1"
    assert result[0].pack_version == "1"


def test_import_cannot_launder_target_evaluation_pixels() -> None:
    source = scope_for()
    target = scope_for("wildlife", "ibis")
    evaluation = next(item for item in target.sources if item.asset_id == "holdout")
    sources = tuple(
        replace(item, sha256=evaluation.sha256, object_key=evaluation.object_key)
        if item.asset_id == "easy-a"
        else item
        for item in source.sources
    )
    source = replace(source, sources=sources)
    pack = ReferencePack("pack", "1", source, (memory_for(source),))
    with pytest.raises(ValueError, match="evaluation"):
        retrieve_references(target, (pack,), query_for(target), (import_for(source, target),))


def test_confusion_negatives_remain_typed_and_diversity_caps_are_explicit() -> None:
    scope = scope_for()
    positive = memory_for(scope)
    negative = replace(memory_for(scope, "easy-b"), kind="confusion_negative", utility=0.8)
    pack = ReferencePack("pack", "1", scope, (positive, negative))
    result = retrieve_references(scope, (pack,), query_for(scope))
    assert [item.kind for item in result] == ["positive", "confusion_negative"]
    assert len(retrieve_references(scope, (pack,), replace(query_for(scope), max_per_class=1))) == 1
    clustered = replace(
        pack, examples=(positive, replace(negative, cluster_id=positive.cluster_id))
    )
    assert len(retrieve_references(scope, (clustered,), query_for(scope))) == 1


def lineage_fixture() -> tuple[LineageGraph, tuple[EvidenceArtifact, ...]]:
    revision = revision_for(scope_for())
    artifacts = (
        EvidenceArtifact("label:1", "source_label", revision),
        EvidenceArtifact("reference:1", "reference"),
        EvidenceArtifact("release", "canonical_release"),
        EvidenceArtifact("derived", "derived_sample"),
        EvidenceArtifact("variant:320", "dataset_variant"),
        EvidenceArtifact("variant:640", "dataset_variant"),
        EvidenceArtifact("train", "training_run"),
        EvidenceArtifact("model", "model"),
        EvidenceArtifact("propose", "proposal_run"),
        EvidenceArtifact("generated", "generated_annotation", revision_for(scope_for(), auto=True)),
        EvidenceArtifact("adjudicated", "generated_annotation", revision),
        EvidenceArtifact("unrelated", "model"),
    )
    graph = LineageGraph()
    for child, parents in (
        ("release", ("label:1",)),
        ("derived", ("release",)),
        ("variant:320", ("derived",)),
        ("variant:640", ("release",)),
        ("train", ("variant:320", "variant:640")),
        ("model", ("train",)),
        ("propose", ("model", "reference:1")),
        ("generated", ("propose",)),
        ("adjudicated", ("generated",)),
    ):
        graph.add(child, *parents)
    return graph, artifacts


def test_bad_sources_trace_all_variants_runs_models_and_annotations_without_rewriting() -> None:
    graph, artifacts = lineage_fixture()
    original_parents = graph.parents.copy()
    event = plan_invalidation(
        event_id="audit-1",
        reason="wrong identity",
        bad_evidence_ids=frozenset({"label:1", "reference:1"}),
        graph=graph,
        artifacts=artifacts,
    )
    marks = {mark.artifact_id: mark for mark in event.marks}
    assert set(marks) == {item.artifact_id for item in artifacts} - {"unrelated"}
    assert marks["variant:320"].action == marks["variant:640"].action == "rebuild"
    assert marks["model"].action == marks["train"].action == "retrain"
    assert marks["generated"].action == "review"
    assert marks["adjudicated"].action == "adjudication_required"
    assert marks["adjudicated"].expected_revision == 1
    assert marks["generated"].affected_by == ("label:1", "reference:1")
    assert graph.parents == original_parents
    assert artifacts[-2].revision == revision_for(scope_for())
    assert append_invalidation((), event) == (event,)
    assert append_invalidation((event,), event) == (event,)
    with pytest.raises(ValueError, match="overwrite"):
        append_invalidation((event,), replace(event, reason="replaced history"))
    reordered = plan_invalidation(
        event_id="audit-1",
        reason="wrong identity",
        bad_evidence_ids=frozenset({"reference:1", "label:1"}),
        graph=graph,
        artifacts=tuple(reversed(artifacts)),
    )
    assert reordered == event


def test_invalidation_refuses_missing_inventory_bad_roots_and_persisted_cycles() -> None:
    graph, artifacts = lineage_fixture()
    for records, roots in (
        (artifacts[1:], frozenset({"label:1"})),
        (artifacts, frozenset({"unknown"})),
        (artifacts, frozenset({"model"})),
    ):
        with pytest.raises(ValueError, match="incomplete|source revision"):
            plan_invalidation(
                event_id="audit",
                reason="bad",
                bad_evidence_ids=roots,
                graph=graph,
                artifacts=records,
            )
    graph.parents["label:1"] = ("model",)
    with pytest.raises(ValueError, match="acyclic"):
        plan_invalidation(
            event_id="audit",
            reason="bad",
            bad_evidence_ids=frozenset({"label:1"}),
            graph=graph,
            artifacts=artifacts,
        )
