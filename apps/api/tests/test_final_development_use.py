"""Persisted final identity takes precedence over a supplied development role."""

import json
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from qualification_fixture import qualification_context, synthetic_final
from test_m5_integration_contract import make_release, make_report

from labelloop_api import final_test_store as store
from labelloop_core.evaluation_lifecycle import (
    CandidateSelection,
    EvaluationSet,
    FinalTestState,
    select_candidate,
)
from labelloop_core.qualification_wire import dumps
from labelloop_core.registry import PromotionPolicy


def journal(dataset="dev", project="project", *, exposed=False):
    state = FinalTestState(EvaluationSet(dataset, project, ("group",), "locked_final_test"))
    if not exposed:
        return state
    release = replace(make_release(320, "challenger"), project_id=project)
    development = make_report(release)
    final = synthetic_final(development, run_id="final", dataset_id=dataset)
    release = replace(release, evaluation_run_ids=(*release.evaluation_run_ids, final.run_id))
    selection = select_candidate(
        release,
        development,
        EvaluationSet(development.dataset_release_id, project, ("dev-group",), "development"),
        selection_id="selection",
    )
    return state.expose(release, selection, final, expected_revision=0)


@pytest.mark.parametrize("target", ["development", "calibration"])
@pytest.mark.parametrize(
    "state", ["locked", "exposed", "adapted", "quarantined", "adapted-quarantined", "absent"]
)
def test_current_journal_controls_development_use(target, state):
    stored = journal(target, exposed=state == "exposed" or "adapted" in state)
    if "adapted" in state:
        stored = stored.record_adaptation("result guided change", expected_revision=stored.revision)
    if "quarantined" in state:
        stored = stored.quarantine("group integrity", expected_revision=stored.revision)
    connection = MagicMock()
    connection.execute.return_value.scalar_one_or_none.side_effect = [
        json.loads(dumps(stored)) if state != "absent" else None,
        None,
    ]
    args = dict(project_key="project", development_set_key=target)
    if target == "calibration":
        args |= dict(development_set_key="unregistered-dev", calibration_set_key=target)
        connection.execute.return_value.scalar_one_or_none.side_effect = [
            None,
            json.loads(dumps(stored)) if state != "absent" else None,
        ]
    with patch.object(store, "lock_project", return_value="project-id") as lock:
        if state == "absent" or (state == "adapted" and target == "development"):
            store.require_development_uses(connection, **args)
        else:
            with pytest.raises(ValueError, match="quarantined|role forbids"):
                store.require_development_uses(connection, **args)
        lock.assert_called_once_with(connection, "project")
    for call in connection.execute.call_args_list:
        query = call.args[0]
        assert query.is_select
        assert query.compile().params["project_id_1"] == "project-id"
        assert "ORDER BY qualification_final_test_event.revision DESC" in str(query)
        assert query.compile().params["param_1"] == 1


@pytest.mark.parametrize("substitution", ["project", "dataset", "root"])
def test_substituted_journal_cannot_authorize_development(substitution):
    stored = journal(
        dataset="other" if substitution == "dataset" else "dev",
        project="other" if substitution == "project" else "project",
    )
    record = (
        CandidateSelection("selection", "project", "model", "a" * 64, "dev-run", "dev", None, None)
        if substitution == "root"
        else stored
    )
    connection = MagicMock()
    connection.execute.return_value.scalar_one_or_none.return_value = json.loads(dumps(record))
    with patch.object(store, "lock_project", return_value="project-id"):
        with pytest.raises(ValueError, match="identity mismatch|schema or record type"):
            store.require_development_uses(
                connection, project_key="project", development_set_key="dev"
            )


def test_selection_rechecks_journal_before_append():
    connection = MagicMock()
    selection = MagicMock(development_set_id="dev", calibration_set_id="calibration")
    with (
        patch.object(store, "lock_project", return_value="project-id"),
        patch.object(store, "_model"),
        patch.object(store, "stored_report"),
        patch.object(store, "select_candidate", return_value=selection),
        patch.object(
            store, "require_development_uses", side_effect=ValueError("locked test")
        ) as use,
    ):
        with pytest.raises(ValueError, match="locked test"):
            store.record_candidate_selection(
                connection,
                project_key="project",
                model_key="model",
                selection_key="selection",
                development_key="dev-run",
                development_set=EvaluationSet("dev", "project", ("g",), "development"),
                actor_id="admin",
            )
        use.assert_called_once_with(
            connection,
            project_key="project",
            development_set_key="dev",
            calibration_set_key="calibration",
        )
    connection.execute.assert_not_called()


def test_exposure_rechecks_prior_selection_before_returning_any_final_result():
    selection = CandidateSelection(
        "selection", "project", "model", "a" * 64, "dev-run", "dev", None, None
    )
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = json.loads(dumps(selection))
    with (
        patch.object(store, "lock_project", return_value="project-id"),
        patch.object(store, "load_final_test") as load,
        patch.object(store, "_model") as model,
        patch.object(store, "_append") as append,
        patch.object(store, "require_development_uses", side_effect=ValueError("locked test")),
    ):
        with pytest.raises(ValueError, match="locked test"):
            store.expose_final_test(
                connection,
                project_key="project",
                dataset_key="final",
                model_key="model",
                selection_key="selection",
                evaluation_key="final-run",
                expected_revision=0,
                actor_id="admin",
            )
        model.assert_not_called()
        append.assert_not_called()
        load.return_value.expose.assert_not_called()


@pytest.mark.parametrize("failure", [False, True])
def test_current_context_rechecks_development_and_calibration_identities(failure):
    release = make_release(320, "challenger")
    report = synthetic_final(make_report(release), run_id="final", dataset_id="final-set")
    release = replace(release, evaluation_run_ids=(*release.evaluation_run_ids, report.run_id))
    context = qualification_context(release, report, PromotionPolicy(1, 0.9))
    with (
        patch.object(store, "load_final_test", return_value=context.current_final_test),
        patch.object(
            store,
            "require_development_uses",
            side_effect=ValueError("locked test") if failure else None,
        ) as use,
    ):
        if failure:
            with pytest.raises(ValueError, match="locked test"):
                store.current_qualification_context(None, context)
        else:
            assert store.current_qualification_context(None, context) == context
        use.assert_called_once_with(
            None,
            project_key=release.project_id,
            development_set_key=context.evidence.lineage.development.release_id,
            calibration_set_key=None,
        )
