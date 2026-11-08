"""Strict sample recovery and mandatory consumers, not domain-quality evidence."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import MagicMock, call

import pytest
from test_audit_sample_store import StoredSample
from test_original_acceptance_sampling import population
from test_qualification_context_store import setup

from labelloop_api import audit_sample_store as store
from labelloop_api import evaluation_group_store, final_test_store, training_group_store
from labelloop_api import qualification_context_store as assembler
from labelloop_core.qualification_data import record_sha256


def strict_sample(monkeypatch, side):
    connection, reader, original, arguments = population(monkeypatch, side)
    frozen = store.freeze_automatic_audit_sample(connection, **arguments)
    saved = connection.execute.call_args.args[0].compile().params
    reader.reset_mock()
    return saved, frozen, reader, original, arguments["pipeline"]


@pytest.mark.parametrize("side", [320, 640])
def test_strict_recovery_rechecks_unselected_originals(monkeypatch, side):
    saved, frozen, reader, _, pipeline = strict_sample(monkeypatch, side)
    connection = StoredSample(saved)
    assert (
        store.load_audit_sample(
            connection,
            project_id="project-uuid",
            sample_key=frozen.sample.sample_id,
            original_pipeline=pipeline,
        )
        == frozen
    )
    assert reader.call_args_list == [
        call(connection, project_key=pipeline.project_id, asset_key=key, revision=2)
        for key in ("first", "second")
    ]


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize(
    "failure", ["legacy", "changed", "fingerprint", "missing", "later", "foreign"]
)
def test_strict_recovery_never_backfills_or_filters(monkeypatch, side, failure):
    saved, frozen, reader, original, pipeline = strict_sample(monkeypatch, side)
    saved = deepcopy(saved)
    if failure == "legacy":
        del saved["record"]["original_pipeline_sha256"]
        saved["population_sha256"] = record_sha256(
            {k: v for k, v in saved["record"].items() if k != "sample"}
        )
    elif failure == "changed":
        pipeline = replace(pipeline, reviewer_model_version="different")
    elif failure == "fingerprint":
        saved["record"]["original_pipeline_sha256"] = "b" * 64
    elif failure == "missing":
        reader.side_effect = ValueError("missing original decision")
    elif failure == "later":
        reader.side_effect = [original, ValueError("missing unselected original")]
    with pytest.raises(ValueError):
        store.load_audit_sample(
            StoredSample(saved),
            project_id="foreign" if failure == "foreign" else "project-uuid",
            sample_key=frozen.sample.sample_id,
            original_pipeline=pipeline,
        )
    if failure in {"legacy", "changed", "foreign", "fingerprint"}:
        reader.assert_not_called()
    if failure == "legacy":
        # Historical adverse inspection stays available, but is not provenance proof.
        assert store.load_audit_sample(
            StoredSample(saved), project_id="project-uuid", sample_key=frozen.sample.sample_id
        ) == replace(frozen, population_sha256=saved["population_sha256"])
        assert saved["population_sha256"] != frozen.population_sha256


@pytest.mark.parametrize("failure", [False, True])
def test_qualification_sample_guard_reloads_current_groups_and_exact_selection(
    monkeypatch, failure
):
    _, frozen, _, _, pipeline = strict_sample(monkeypatch, 320)
    sample = frozen.sample
    if failure:
        sample = replace(sample, seed=sample.seed + 1)
    loader = MagicMock(return_value=frozen)
    monkeypatch.setattr(store, "load_audit_sample", loader)
    if failure:
        with pytest.raises(ValueError, match="committed automatic audit"):
            store.require_automatic_audit_samples(None, pipeline=pipeline, samples=(sample,))
    else:
        store.require_automatic_audit_samples(None, pipeline=pipeline, samples=(sample, sample))
    assert loader.call_args == call(
        None,
        project_id="project-uuid",
        sample_key=sample.sample_id,
        require_current_groups=True,
        original_pipeline=pipeline,
    )
    assert loader.call_count == (1 if failure else 2)


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("entry", ["assembly", "current-consumer"])
@pytest.mark.parametrize("fixture_only", [False, True])
def test_real_consumers_require_both_original_samples_without_promoting_fixture_truth(
    monkeypatch, side, entry, fixture_only
):
    connection, args, context, readers = setup(monkeypatch, side)
    policy = readers["load_qualification_policy"].return_value
    targets = replace(context.evidence.targets, fixture_only=fixture_only)
    pipeline = replace(context.evidence.pipeline, acceptance_policy_sha256=record_sha256(targets))
    context = replace(
        context,
        current_pipeline=pipeline,
        evidence=replace(context.evidence, pipeline=pipeline, targets=targets),
    )
    readers["load_qualification_policy"].return_value = replace(
        policy, policy=replace(policy.policy, pipeline=pipeline, targets=targets)
    )
    guard = MagicMock(side_effect=ValueError("unproven original population"))
    monkeypatch.setattr(assembler, "require_automatic_audit_samples", guard)
    monkeypatch.setattr(store, "require_automatic_audit_samples", guard)
    if entry == "assembly":

        def invoke():
            return assembler.load_qualification_context(connection, **args)
    else:
        for name in ("lock_project", "require_development_uses", "load_final_test"):
            monkeypatch.setattr(final_test_store, name, readers[name])
        monkeypatch.setattr(
            evaluation_group_store, "load_evaluation_set", readers["load_evaluation_set"]
        )
        monkeypatch.setattr(
            training_group_store, "model_input_groups", readers["model_input_groups"]
        )

        def invoke():
            return final_test_store.current_qualification_context(
                connection, context, model_id="model-id"
            )

    if fixture_only:
        assert invoke() == context
        guard.assert_not_called()
    else:
        with pytest.raises(ValueError, match="unproven original population"):
            invoke()
        guard.assert_called_once_with(
            connection,
            pipeline=context.evidence.pipeline,
            samples=(
                context.evidence.annotation_audit.sample,
                context.evidence.canary_audit.sample,
            ),
        )
