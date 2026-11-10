"""Final-report selection is metadata-only; all existing qualification gates still apply."""

from dataclasses import replace
from unittest.mock import call

import pytest
from test_qualification_context_store import setup

from labelloop_api import qualification_context_store as store


def automatic_setup(monkeypatch, side):
    connection, args, context, readers = setup(monkeypatch, side)
    release = readers["load_release"].return_value
    final = readers["stored_report"].return_value
    reports = {
        key: final if key == final.run_id else replace(final, run_id=key, role="development")
        for key in release.evaluation_run_ids
    }
    readers["stored_report"].side_effect = lambda _connection, _release, key: reports[key]
    args.pop("evaluation_key")
    return connection, args, context, readers, release, reports


@pytest.mark.parametrize("side", [320, 640])
def test_assembly_selects_only_linked_final_identity_without_a_caller_key(monkeypatch, side):
    connection, args, expected, readers, release, reports = automatic_setup(monkeypatch, side)
    assert store.load_qualification_context(connection, **args) == expected
    assert readers["stored_report"].call_args_list == [
        call(connection, release, key) for key in release.evaluation_run_ids
    ]
    assert len(reports) > 1  # Development evidence is linked, but cannot be selected as final.
    assert connection.execute.call_count == 1  # Scoped model SELECT only; no state mutation.


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("role", ["development", "calibration"])
def test_missing_final_report_cannot_fall_back_to_development(monkeypatch, side, role):
    connection, args, _, readers, _, reports = automatic_setup(monkeypatch, side)
    for key, report in reports.items():
        reports[key] = replace(report, role=role)
    with pytest.raises(ValueError, match="one unambiguous linked final evaluation"):
        store.load_qualification_context(connection, **args)
    readers["load_final_test"].assert_not_called()
    readers["load_annotation_evidence"].assert_not_called()


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("other_passes", [False, True])
def test_multiple_final_reports_are_not_cherry_picked_by_score(monkeypatch, side, other_passes):
    connection, args, _, readers, release, reports = automatic_setup(monkeypatch, side)
    final = next(report for report in reports.values() if report.role == "locked_final_test")
    second = replace(final, run_id="second-final")
    if not other_passes:
        second = replace(second, overall=replace(second.overall, map50_95=0.0))
    reports[second.run_id] = second
    readers["load_release"].return_value = replace(
        release, evaluation_run_ids=(*release.evaluation_run_ids, second.run_id)
    )
    with pytest.raises(ValueError, match="one unambiguous linked final evaluation"):
        store.load_qualification_context(connection, **args)
    readers["load_final_test"].assert_not_called()
    readers["load_annotation_evidence"].assert_not_called()


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("role", ["development", "locked_final_test"])
@pytest.mark.parametrize(
    "field", ["run_id", "recipe_id", "weights_sha256", "class_map_sha256", "policy", "role"]
)
def test_every_linked_report_must_match_the_selected_model_contract(monkeypatch, side, role, field):
    connection, args, _, readers, release, reports = automatic_setup(monkeypatch, side)
    key = next(key for key, report in reports.items() if report.role == role)
    value = {
        "run_id": "substituted",
        "recipe_id": "foreign-recipe",
        "weights_sha256": "f" * 64,
        "class_map_sha256": "e" * 64,
        "policy": replace(release.policy, conf=0.99),
        "role": "unknown",
    }[field]
    reports[key] = replace(reports[key], **{field: value})
    with pytest.raises(ValueError, match="differs from the selected model contract"):
        store.load_qualification_context(connection, **args)
    readers["load_final_test"].assert_not_called()
    readers["load_annotation_evidence"].assert_not_called()


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("invalidation", ["unexposed", "adapted", "quarantined"])
def test_automatic_selection_never_regrants_invalidated_or_unexposed_final(
    monkeypatch, side, invalidation
):
    connection, args, context, readers, _, _ = automatic_setup(monkeypatch, side)
    state = context.current_final_test
    state = {
        "unexposed": replace(state, assessments=()),
        "adapted": replace(state, adaptation_reasons=("fixture-adaptation",)),
        "quarantined": replace(state, quarantine_reasons=("fixture-conflict",)),
    }[invalidation]
    readers["load_final_test"].return_value = state
    with pytest.raises(ValueError):
        store.load_qualification_context(connection, **args)


@pytest.mark.parametrize("side", [320, 640])
def test_automatic_selection_still_rejects_failed_final_metrics(monkeypatch, side):
    connection, args, _, readers, _, reports = automatic_setup(monkeypatch, side)
    key = next(key for key, report in reports.items() if report.role == "locked_final_test")
    reports[key] = replace(reports[key], overall=replace(reports[key].overall, map50_95=0.0))
    # No matching committed exposure exists for this substituted payload. Selecting
    # the key cannot bypass exposure binding to qualify an arbitrary report.
    with pytest.raises(ValueError, match="one exact committed final-test exposure"):
        store.load_qualification_context(connection, **args)
    readers["load_annotation_evidence"].assert_not_called()
