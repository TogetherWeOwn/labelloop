from dataclasses import replace
from typing import Any

import pytest
from qualification_fixture import qualification_context
from test_registry import model, report

from labelloop_core.evaluation_lifecycle import EvaluationSet, FinalTestState, select_candidate
from labelloop_core.registry import PromotionPolicy


@pytest.mark.parametrize(
    "purpose",
    [
        "training",
        "checkpoint_selection",
        "threshold_tuning",
        "reference_retrieval",
    ],
)
def test_locked_final_evidence_rejects_every_development_use(purpose: Any) -> None:
    dataset = EvaluationSet("final", "project-1", ("holdout",), "locked_final_test")
    with pytest.raises(ValueError, match="forbids"):
        dataset.require_use(purpose)


def test_selection_and_threshold_tuning_have_separate_roles_and_isolated_groups() -> None:
    release = model(320)
    final = report(release)
    dev_set = EvaluationSet("dev", release.project_id, ("dev-group",), "development")
    dev = replace(
        final, run_id=release.evaluation_run_ids[1], dataset_release_id="dev", role="development"
    )
    with pytest.raises(ValueError):
        select_candidate(release, final, dev_set, selection_id="selected-on-final")
    calibration = replace(dev, role="calibration", dataset_release_id="cal")
    cal_set = EvaluationSet("cal", release.project_id, ("cal-group",), "calibration")
    selection = select_candidate(
        release,
        dev,
        dev_set,
        selection_id="pre-final",
        calibration=calibration,
        calibration_set=cal_set,
    )
    assert selection.calibration_set_id == "cal"
    with pytest.raises(ValueError, match="calibration evidence"):
        select_candidate(
            release,
            dev,
            dev_set,
            selection_id="pre-final",
            calibration=calibration,
            calibration_set=replace(cal_set, group_ids=("dev-group",)),
        )
    with pytest.raises(ValueError, match="forbids"):
        select_candidate(
            release,
            dev,
            dev_set,
            selection_id="pre-final",
            calibration=final,
            calibration_set=replace(cal_set, role="locked_final_test"),
        )


@pytest.mark.parametrize(
    "reason",
    [
        "aggregate score guided a recipe change",
        "pass/fail guided a threshold change",
        "detailed failures guided a checkpoint change",
    ],
)
def test_adaptation_spends_the_whole_test_without_erasing_historical_results(reason: str) -> None:
    release = model(320)
    final = report(release)
    context = qualification_context(release, final, PromotionPolicy(1, 0.9))
    historical = context.evidence.final_test
    assert historical.effective_role == "locked_final_test"
    historical.require_independent(release, final)
    adapted = historical.record_adaptation(reason, expected_revision=1)
    assert adapted.effective_role == "development"
    assert adapted.dataset.group_ids == historical.dataset.group_ids
    assert adapted.assessments == historical.assessments
    assert adapted.assessments[0].report_json == historical.assessments[0].report_json
    with pytest.raises(ValueError, match="stale or mismatched"):
        replace(context, current_final_test=adapted).require_qualified(release, final)
    with pytest.raises(ValueError, match="independence was invalidated"):
        replace(
            context,
            evidence=replace(context.evidence, final_test=adapted),
            current_final_test=adapted,
        ).require_qualified(release, final)
    # Historical success remains immutable and reportable, not reusable for later qualification.
    assert historical.revision == 1 and adapted.revision == 2


def test_quarantine_and_late_selection_cannot_reopen_or_cherry_pick_a_final_test() -> None:
    release = model(320)
    final = report(release)
    context = qualification_context(release, final, PromotionPolicy(1, 0.9))
    previous = context.current_final_test
    selection = previous.assessments[0].selection
    with pytest.raises(ValueError, match="already exposed"):
        previous.expose(release, selection, final, expected_revision=1)
    with pytest.raises(ValueError, match="stale"):
        previous.quarantine("new relation joins holdout and training", expected_revision=0)
    quarantined = previous.quarantine(
        "new relation joins holdout and training", expected_revision=1
    )
    assert quarantined.assessments == previous.assessments
    with pytest.raises(ValueError, match="invalidated"):
        quarantined.require_independent(release, final)
    with pytest.raises(ValueError, match="quarantined"):
        quarantined.expose(release, selection, final, expected_revision=2)
    unopened = FinalTestState(previous.dataset)
    with pytest.raises(ValueError, match="unexposed"):
        unopened.record_adaptation("unobserved score", expected_revision=0)
    with pytest.raises(ValueError, match="pinned"):
        unopened.expose(
            replace(release, policy=replace(release.policy, conf=0.7)),
            selection,
            replace(final, policy=replace(final.policy, conf=0.7)),
            expected_revision=0,
        )


@pytest.mark.parametrize("which", ["training", "development", "references"])
def test_holdout_group_leakage_invalidates_qualification(which: str) -> None:
    release = model(320)
    final = report(release)
    context = qualification_context(release, final, PromotionPolicy(1, 0.9))
    lineage = context.evidence.lineage
    if which == "training":
        lineage = replace(lineage, training_group_ids=("final-group",))
    elif which == "development":
        lineage = replace(
            lineage, development=replace(lineage.development, group_ids=("final-group",))
        )
    else:
        lineage = replace(lineage, reference_group_ids=("final-group",))
    with pytest.raises(ValueError, match="overlap|holdouts"):
        replace(context, evidence=replace(context.evidence, lineage=lineage)).require_qualified(
            release, final
        )
