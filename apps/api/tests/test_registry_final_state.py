"""Registry adapter checks; actual stale-context vetoes run on Actions PostgreSQL."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from labelloop_api import final_test_store, model_store, qualification_policy_store


@pytest.mark.parametrize(
    "scenario",
    ["current", "missing", "missing-policy", "foreign-project", "foreign-recipe", "foreign-final"],
)
def test_registry_requires_current_scoped_final_journal_before_promotion(scenario):
    pipeline = SimpleNamespace(project_id="project", recipe_id="recipe")
    dataset = SimpleNamespace(project_id="project")
    historical = SimpleNamespace(
        evidence=SimpleNamespace(pipeline=pipeline, final_test=SimpleNamespace(dataset=dataset))
    )
    if scenario == "foreign-project":
        pipeline.project_id = "other"
    elif scenario == "foreign-recipe":
        pipeline.recipe_id = "other"
    elif scenario == "foreign-final":
        dataset.project_id = "other"
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.one.side_effect = [
        {"revision": 0, "champion_id": None, "rollback_id": None},
        {"id": "candidate-id"},
    ]
    state = MagicMock()
    state.champion = None
    changed = state.promote.return_value
    changed.revision = 1
    current = object()
    policy = object()
    candidate = object()
    report = object()
    with (
        patch.object(model_store, "lock_project", return_value="project-id"),
        patch.object(model_store, "require_no_annotation_veto"),
        patch.object(model_store, "load_release", return_value=candidate),
        patch.object(model_store, "stored_report", return_value=report),
        patch.object(model_store, "RegistryState", return_value=state),
        patch.object(model_store, "json_record", return_value={}),
        patch.object(model_store, "asdict", return_value={}),
        patch.object(model_store, "qualification_json", return_value='{"current":true}'),
        patch.object(
            qualification_policy_store, "current_policy_context", side_effect=lambda _, value: value
        ) as policy_reload,
        patch.object(
            final_test_store, "current_qualification_context", return_value=current
        ) as reload,
    ):
        kwargs = dict(
            project_key="project",
            recipe_key="recipe",
            actor_id="admin",
            expected_revision=0,
            candidate_key="candidate",
            evaluation_key="evaluation",
            policy=policy,
            qualification=historical,
        )
        if scenario.startswith("foreign"):
            with pytest.raises(ValueError, match="cross project or recipe"):
                model_store.change_registry(connection, **kwargs)
            reload.assert_not_called()
        elif scenario == "missing-policy":
            policy_reload.side_effect = ValueError("missing current qualification policy")
            with pytest.raises(ValueError, match="missing current qualification policy"):
                model_store.change_registry(connection, **kwargs)
            reload.assert_not_called()
        elif scenario == "missing":
            reload.side_effect = sa.exc.NoResultFound("no final journal")
            with pytest.raises(sa.exc.NoResultFound, match="no final journal"):
                model_store.change_registry(connection, **kwargs)
            reload.assert_called_once_with(connection, historical, model_id="candidate-id")
        else:
            assert model_store.change_registry(connection, **kwargs) is changed
            reload.assert_called_once_with(connection, historical, model_id="candidate-id")
            state.promote.assert_called_once_with(
                candidate,
                report,
                policy,
                expected_revision=0,
                incumbent_report=None,
                qualification=current,
            )
            event = connection.execute.call_args.args[0]
            assert event.table.name == "promotion_event"
            assert event.compile().params["record"]["qualification"] == {"current": True}
        if scenario != "current":
            state.promote.assert_not_called()
            assert not any(
                getattr(call.args[0], "is_update", False)
                or getattr(getattr(call.args[0], "table", None), "name", None) == "promotion_event"
                for call in connection.execute.call_args_list
            )
