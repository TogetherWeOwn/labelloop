"""Server-selected promotion inputs; mocks do not establish PostgreSQL acceptance."""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from qualification_fixture import qualification_context, synthetic_final
from test_m5_integration_contract import make_release, make_report

from labelloop_api import promotion_service as service
from labelloop_api import qualification_context_store as contexts
from labelloop_core.registry import PromotionPolicy, RegistryState


def setup(monkeypatch, side=320, incumbent=True):
    candidate = make_release(side, "challenger")
    report = synthetic_final(make_report(candidate), run_id="final", dataset_id="final-set")
    candidate = replace(candidate, evaluation_run_ids=(*candidate.evaluation_run_ids, "final"))
    context = qualification_context(candidate, report, PromotionPolicy(1, 0.9))
    champion = make_release(side, "champion") if incumbent else None
    registry = RegistryState(candidate.project_id, candidate.recipe_id, 7, champion)
    connection = MagicMock()
    readers = {
        "lock_project": MagicMock(),
        "load_qualification_context": MagicMock(return_value=context),
        "select_final_report": MagicMock(return_value=replace(report, run_id="comparison")),
    }
    for name, reader in readers.items():
        monkeypatch.setattr(service, name, reader)
    monkeypatch.setattr(service.model_store, "load_registry", MagicMock(return_value=registry))
    writer = MagicMock(return_value="promoted")
    monkeypatch.setattr(service.model_store, "change_registry", writer)
    args = dict(
        project_key=candidate.project_id,
        recipe_key=candidate.recipe_id,
        model_key=candidate.release_id,
        annotation_release_key="ordinary-population",
        canary_release_key="fresh-population",
        actor_id="session-actor",
        expected_revision=7,
    )
    return connection, args, context, registry, readers, writer


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("incumbent", [False, True])
def test_derives_every_gate_and_report_before_atomic_registry_write(monkeypatch, side, incumbent):
    connection, args, context, registry, readers, writer = setup(monkeypatch, side, incumbent)
    assert service.promote_candidate(connection, **args) == "promoted"
    readers["lock_project"].assert_called_once_with(connection, args["project_key"])
    service.model_store.load_registry.assert_called_once_with(
        connection, args["project_key"], args["recipe_key"]
    )
    loaded = readers["load_qualification_context"].call_args.kwargs
    assert loaded.pop("qualification_key")
    assert loaded == {key: args[key] for key in ("project_key", "recipe_key", "model_key")} | {
        "annotation": contexts.AnnotationOutcomeRef(args["annotation_release_key"]),
        "canary": contexts.AnnotationOutcomeRef(args["canary_release_key"]),
    }
    if incumbent:
        readers["select_final_report"].assert_called_once_with(
            connection, registry.champion, dataset_key="final-set"
        )
    else:
        readers["select_final_report"].assert_not_called()
    writer.assert_called_once_with(
        connection,
        **{
            key: args[key] for key in ("project_key", "recipe_key", "actor_id", "expected_revision")
        },
        candidate_key=args["model_key"],
        evaluation_key="final",
        policy=PromotionPolicy(1, 0.9),
        incumbent_evaluation_key="comparison" if incumbent else None,
        qualification=context,
    )
    connection.commit.assert_not_called()


@pytest.mark.parametrize(
    "field",
    [
        "project_key",
        "recipe_key",
        "model_key",
        "annotation_release_key",
        "canary_release_key",
        "actor_id",
    ],
)
def test_invalid_identity_rejects_before_database_access(monkeypatch, field):
    connection, args, _, _, readers, writer = setup(monkeypatch)
    args[field] = " "
    with pytest.raises(ValueError):
        service.promote_candidate(connection, **args)
    readers["lock_project"].assert_not_called()
    writer.assert_not_called()


@pytest.mark.parametrize("revision", [-1, True, "7"])
def test_invalid_revision_rejects_before_database_access(monkeypatch, revision):
    connection, args, _, _, readers, writer = setup(monkeypatch)
    args["expected_revision"] = revision
    with pytest.raises(ValueError):
        service.promote_candidate(connection, **args)
    readers["lock_project"].assert_not_called()
    writer.assert_not_called()


@pytest.mark.parametrize("mutation", ["stale", "already-champion"])
def test_stale_or_repeated_promotion_does_not_reselect_evidence(monkeypatch, mutation):
    connection, args, _, registry, readers, writer = setup(monkeypatch)
    if mutation == "stale":
        args["expected_revision"] -= 1
    else:
        args["model_key"] = registry.champion.release_id
    with pytest.raises(ValueError):
        service.promote_candidate(connection, **args)
    readers["load_qualification_context"].assert_not_called()
    writer.assert_not_called()


@pytest.mark.parametrize("reader", ["load_qualification_context", "select_final_report"])
def test_missing_ambiguous_or_invalid_evidence_never_reaches_writer(monkeypatch, reader):
    connection, args, _, _, readers, writer = setup(monkeypatch)
    readers[reader].side_effect = ValueError("stored evidence rejected")
    with pytest.raises(ValueError, match="stored evidence rejected"):
        service.promote_candidate(connection, **args)
    writer.assert_not_called()


def test_atomic_writer_failure_propagates_without_commit_or_retry(monkeypatch):
    connection, args, _, _, _, writer = setup(monkeypatch)
    writer.side_effect = ValueError("current veto or failed event append")
    with pytest.raises(ValueError, match="current veto or failed event append"):
        service.promote_candidate(connection, **args)
    assert writer.call_count == 1
    connection.commit.assert_not_called()


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("matches", [0, 1, 2])
def test_incumbent_selector_counts_every_same_population_final(monkeypatch, side, matches):
    release = make_release(side, "champion")
    unrelated = synthetic_final(make_report(release), run_id="unrelated", dataset_id="old-set")
    reports = [unrelated] + [
        replace(
            unrelated,
            run_id=f"comparison-{i}",
            dataset_release_id="candidate-set",
            overall=replace(unrelated.overall, map50_95=0.0 if i else 1.0),
        )
        for i in range(matches)
    ]
    release = replace(release, evaluation_run_ids=tuple(item.run_id for item in reports))
    reader = MagicMock(side_effect=reports)
    monkeypatch.setattr(contexts, "stored_report", reader)
    if matches == 1:
        assert (
            contexts.select_final_report(None, release, dataset_key="candidate-set") == reports[1]
        )
    else:
        with pytest.raises(ValueError, match="one unambiguous linked final evaluation"):
            contexts.select_final_report(None, release, dataset_key="candidate-set")
    assert reader.call_count == len(reports)


def test_even_unrelated_linked_contract_substitution_rejects(monkeypatch):
    release = make_release(320, "champion")
    report = synthetic_final(make_report(release), run_id="unrelated", dataset_id="old-set")
    release = replace(release, evaluation_run_ids=(report.run_id,))
    monkeypatch.setattr(
        contexts, "stored_report", lambda *args: replace(report, weights_sha256="a" * 64)
    )
    with pytest.raises(ValueError, match="differs from the selected model contract"):
        contexts.select_final_report(None, release, dataset_key="candidate-set")
