"""Recorded selection/threshold influence; mocked adapters are not DB acceptance."""

import json
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from test_m5_integration_contract import make_release, make_report

from labelloop_api import evaluation_group_store
from labelloop_api import training_group_store as store
from labelloop_core.evaluation_lifecycle import EvaluationSet, select_candidate
from labelloop_core.qualification_wire import dumps


def fixture(monkeypatch, side, calibrated):
    release = make_release(side, "challenger")
    development = make_report(release)
    calibration = replace(
        development,
        run_id="calibration-run",
        dataset_release_id="calibration-set",
        role="calibration",
    )
    if calibrated:
        release = replace(
            release, evaluation_run_ids=(*release.evaluation_run_ids, calibration.run_id)
        )
    datasets = {
        development.dataset_release_id: EvaluationSet(
            development.dataset_release_id,
            release.project_id,
            ("development-group",),
            "development",
        ),
        calibration.dataset_release_id: EvaluationSet(
            calibration.dataset_release_id,
            release.project_id,
            ("calibration-group",),
            "calibration",
        ),
    }
    selection = select_candidate(
        release,
        development,
        datasets[development.dataset_release_id],
        selection_id="selection",
        calibration=calibration if calibrated else None,
        calibration_set=datasets[calibration.dataset_release_id] if calibrated else None,
    )
    row = dict(
        selection_key=selection.selection_id, record=json.loads(dumps(selection)), model_id="model"
    )
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.all.return_value = [row]
    monkeypatch.setattr(store, "load_release", MagicMock(return_value=release))
    reports = {report.run_id: report for report in (development, calibration)}
    monkeypatch.setattr(
        store, "stored_report", MagicMock(side_effect=lambda c, r, key: reports[key])
    )
    reader = MagicMock(
        side_effect=lambda c, *, project_key, dataset_key, role: datasets[dataset_key]
    )
    monkeypatch.setattr(evaluation_group_store, "load_evaluation_set", reader)
    return connection, row, selection, reader


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("calibrated", [False, True])
@pytest.mark.parametrize("historical_digest", [False, True])
def test_reloads_canonical_groups_for_all_selections_in_the_run_closure(
    monkeypatch, side, calibrated, historical_digest
):
    connection, row, selection, reader = fixture(monkeypatch, side, calibrated)
    if historical_digest:
        # Later report links extend the release digest, but cannot erase prior influence.
        row["record"] = json.loads(dumps(replace(selection, release_sha256="f" * 64)))
    assert store._recorded_selection_groups(connection, "project-id", {"candidate", "teacher"}) == (
        ("calibration-group", "development-group") if calibrated else ("development-group",)
    )
    statement = connection.execute.call_args.args[0]
    params = statement.compile().params
    assert params["project_id_1"] == "project-id"
    assert set(params["training_run_id_1"]) == {"candidate", "teacher"}
    assert "model_release.project_id = qualification_candidate_selection.project_id" in str(
        statement
    )
    assert reader.call_count == 1 + calibrated
    assert [call.kwargs["role"] for call in reader.call_args_list] == (
        ["development", "calibration"] if calibrated else ["development"]
    )


@pytest.mark.parametrize(
    "mutation", ["key", "project", "model", "root", "dataset", "report", "missing", "groups"]
)
def test_recorded_substitution_and_missing_evidence_fail_closed(monkeypatch, mutation):
    connection, row, selection, reader = fixture(monkeypatch, 320, True)
    if mutation == "key":
        row["selection_key"] = "other"
    elif mutation in {"project", "model", "dataset"}:
        field = {"project": "project_id", "model": "release_id", "dataset": "development_set_id"}[
            mutation
        ]
        row["record"] = json.loads(dumps(replace(selection, **{field: "other"})))
        if mutation == "dataset":
            reader.side_effect = ValueError("missing canonical release")
    elif mutation == "root":
        row["record"]["record_type"] = "FinalTestState"
    elif mutation == "report":
        original = store.stored_report.side_effect
        store.stored_report.side_effect = lambda c, r, key: replace(
            original(c, r, key), role="final"
        )
    elif mutation == "missing":
        store.load_release.return_value = None
    else:
        reader.side_effect = ValueError("current canonical relation conflict")
    with pytest.raises(ValueError):
        store._recorded_selection_groups(connection, "project-id", {"candidate"})
    assert connection.execute.call_count == 1


def test_unrelated_run_selections_are_excluded_by_the_database_query(monkeypatch):
    connection, _, _, reader = fixture(monkeypatch, 320, False)
    connection.execute.return_value.mappings.return_value.all.return_value = []
    assert store._recorded_selection_groups(connection, "project-id", {"candidate"}) == ()
    reader.assert_not_called()
    store.load_release.assert_not_called()
