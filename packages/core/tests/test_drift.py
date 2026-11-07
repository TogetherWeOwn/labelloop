from dataclasses import replace

import pytest
from qualification_fixture import qualification_context
from test_registry import model, report

from labelloop_core.drift import AcceptanceState
from labelloop_core.qualification_data import ScopeCell
from labelloop_core.registry import PromotionPolicy


def test_drift_pauses_only_affected_scope_and_resume_requires_new_qualified_release() -> None:
    release = model(320)
    final = report(release)
    scopes = (
        ScopeCell("object", "camera-a", "ordinary"),
        ScopeCell("object", "camera-b", "ordinary"),
    )
    context = qualification_context(release, final, PromotionPolicy(1, 0.9), scope=scopes)
    initial = AcceptanceState(release.project_id, release.recipe_id)
    paused = initial.pause(
        release,
        pause_id="camera-change",
        qualification_id=context.evidence.qualification_id,
        scope=(scopes[0],),
        reason="camera/layout shift in source a",
        expected_revision=0,
    )
    assert paused.is_paused(scopes[0]) and not paused.is_paused(scopes[1])
    with pytest.raises(ValueError, match="paused"):
        paused.require_acceptance(release, final, context, scopes[0])
    paused.require_acceptance(release, final, context, scopes[1])
    with pytest.raises(ValueError, match="new qualified release"):
        paused.resume(release, final, context, pause_ids=("camera-change",), expected_revision=1)
    next_release = model(320, "new-qualified-release")
    next_report = report(next_release)
    qualified = qualification_context(
        next_release, next_report, PromotionPolicy(1, 0.9), scope=scopes
    )
    resumed = paused.resume(
        next_release,
        next_report,
        qualified,
        pause_ids=("camera-change",),
        expected_revision=1,
    )
    assert not resumed.is_paused(scopes[0])
    assert resumed.pauses[0].reason == paused.pauses[0].reason
    assert resumed.pauses[0].resumed_by_release_id == next_release.release_id
    assert resumed.pauses[0].resumed_by_qualification_id == qualified.evidence.qualification_id
    resumed.require_acceptance(next_release, next_report, qualified, scopes[0])
    assert paused.is_paused(scopes[0]) and initial.revision == 0
    # Acceptance state and the release payload remain independent at 640.
    large = model(640)
    large_context = qualification_context(
        large, report(large), PromotionPolicy(1, 0.9), scope=scopes
    )
    AcceptanceState(large.project_id, large.recipe_id).require_acceptance(
        large,
        report(large),
        large_context,
        scopes[0],
    )


def test_unqualified_out_of_scope_stale_and_cross_recipe_resumption_are_rejected() -> None:
    release = model(320)
    context = qualification_context(release, report(release), PromotionPolicy(1, 0.9))
    scope = context.evidence.scope[0]
    state = AcceptanceState(release.project_id, release.recipe_id).pause(
        release,
        pause_id="drift",
        qualification_id=context.evidence.qualification_id,
        scope=(scope,),
        reason="increased audit errors",
        expected_revision=0,
    )
    with pytest.raises(ValueError, match="stale"):
        state.resume(release, report(release), context, pause_ids=("drift",), expected_revision=0)
    with pytest.raises(ValueError, match="recipe boundaries"):
        state.resume(
            model(640), report(model(640)), context, pause_ids=("drift",), expected_revision=1
        )
    next_release = model(320, "next")
    final = report(next_release)
    qualified = qualification_context(next_release, final, PromotionPolicy(1, 0.9))
    with pytest.raises(ValueError, match="new qualified release"):
        state.resume(
            next_release,
            final,
            replace(
                qualified,
                evidence=replace(
                    qualified.evidence,
                    qualification_id=context.evidence.qualification_id,
                ),
            ),
            pause_ids=("drift",),
            expected_revision=1,
        )
    with pytest.raises(ValueError, match="outside qualified scope"):
        AcceptanceState(release.project_id, release.recipe_id).require_acceptance(
            release,
            report(release),
            context,
            replace(scope, source_id="unknown-camera"),
        )
    with pytest.raises(ValueError):
        state.resume(
            next_release,
            final,
            replace(
                qualified,
                evidence=replace(
                    qualified.evidence,
                    targets=replace(qualified.evidence.targets, min_map50_95=None),
                ),
            ),
            pause_ids=("drift",),
            expected_revision=1,
        )


def test_class_specific_drift_does_not_disable_other_classes_in_the_same_source() -> None:
    release = replace(model(320), class_ids=("object", "other"))
    final = report(release, truth_classes=release.class_ids)
    context = qualification_context(release, final, PromotionPolicy(1, 0.9))
    first, second = context.evidence.scope
    state = AcceptanceState(release.project_id, release.recipe_id).pause(
        release,
        pause_id="class-drift",
        qualification_id=context.evidence.qualification_id,
        scope=(first,),
        reason="new variant",
        expected_revision=0,
    )
    assert state.is_paused(first) and not state.is_paused(second)
    state.require_acceptance(release, final, context, second)
