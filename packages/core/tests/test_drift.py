from dataclasses import replace

import pytest
from qualification_fixture import qualification_context
from test_registry import model, report

from labelloop_core.drift import AcceptanceState
from labelloop_core.qualification import QualificationContext
from labelloop_core.qualification_data import ScopeCell
from labelloop_core.qualification_wire import dumps, loads
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
        next_release,
        next_report,
        PromotionPolicy(1, 0.9),
        scope=scopes,
        remediation_for=(paused.incident_reference("camera-change"),),
    )
    assert loads(dumps(paused), AcceptanceState) == paused
    assert loads(dumps(qualified), QualificationContext) == qualified
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


def test_predrift_qualification_or_relabelled_old_canary_cannot_clear_an_incident() -> None:
    first, second = model(320), model(320, "already-qualified-before-drift")
    gate = PromotionPolicy(1, 0.9)
    old = qualification_context(second, report(second), gate)
    first_context = qualification_context(first, report(first), gate)
    paused = AcceptanceState(first.project_id, first.recipe_id).pause(
        first,
        pause_id="camera-drift",
        qualification_id=first_context.evidence.qualification_id,
        scope=first_context.evidence.scope,
        reason="layout changed",
        expected_revision=0,
    )
    with pytest.raises(ValueError, match="post-incident"):
        paused.resume(second, report(second), old, pause_ids=("camera-drift",), expected_revision=1)
    incident = paused.incident_reference("camera-drift")
    # Merely relabelling the qualification cannot reuse a canary that predates the incident.
    with pytest.raises(ValueError, match="same drift incidents"):
        paused.resume(
            second,
            report(second),
            replace(
                old,
                evidence=replace(
                    old.evidence,
                    remediation_for=(incident,),
                ),
            ),
            pause_ids=("camera-drift",),
            expected_revision=1,
        )
    fresh = qualification_context(second, report(second), gate, remediation_for=(incident,))
    for wrong in (
        replace(incident, opened_revision=incident.opened_revision + 1),
        replace(incident, release_sha256="9" * 64),
        replace(incident, pause_id="another-incident"),
    ):
        mismatched = replace(
            fresh,
            evidence=replace(
                fresh.evidence,
                remediation_for=(wrong,),
                canary_audit=replace(fresh.evidence.canary_audit, remediation_for=(wrong,)),
            ),
        )
        with pytest.raises(ValueError, match="post-incident"):
            paused.resume(
                second, report(second), mismatched, pause_ids=("camera-drift",), expected_revision=1
            )
    assert paused.is_paused(first_context.evidence.scope[0])
    resumed = paused.resume(
        second, report(second), fresh, pause_ids=("camera-drift",), expected_revision=1
    )
    resumed.require_acceptance(second, report(second), fresh, first_context.evidence.scope[0])
    with pytest.raises(ValueError, match="post-incident"):
        resumed.require_acceptance(second, report(second), old, first_context.evidence.scope[0])


def test_successive_drift_resumption_requires_all_overlapping_incidents() -> None:
    first, second, third = model(320), model(320, "second"), model(320, "third")
    gate = PromotionPolicy(1, 0.9)
    scope = (
        ScopeCell("object", "camera-a", "ordinary"),
        ScopeCell("object", "camera-b", "ordinary"),
    )
    first_context = qualification_context(first, report(first), gate, scope=scope)
    paused_first = AcceptanceState(first.project_id, first.recipe_id).pause(
        first,
        pause_id="first-drift",
        qualification_id=first_context.evidence.qualification_id,
        scope=(scope[0],),
        reason="first camera shift",
        expected_revision=0,
    )
    first_incident = paused_first.incident_reference("first-drift")
    second_context = qualification_context(
        second, report(second), gate, scope=scope, remediation_for=(first_incident,)
    )
    resumed_first = paused_first.resume(
        second, report(second), second_context, pause_ids=("first-drift",), expected_revision=1
    )
    resumed_first.require_acceptance(second, report(second), second_context, scope[0])
    paused_second = resumed_first.pause(
        second,
        pause_id="second-drift",
        qualification_id=second_context.evidence.qualification_id,
        scope=(scope[0],),
        reason="second camera shift",
        expected_revision=2,
    )
    second_incident = paused_second.incident_reference("second-drift")
    # Neither the latest incident alone nor the historical incident alone is sufficient.
    for incidents in ((second_incident,), (first_incident,)):
        incomplete = qualification_context(
            third, report(third), gate, scope=scope, remediation_for=incidents
        )
        with pytest.raises(ValueError, match="post-incident"):
            paused_second.resume(
                third, report(third), incomplete, pause_ids=("second-drift",), expected_revision=3
            )
        assert paused_second.revision == 3 and paused_second.is_paused(scope[0])
    # A historical incident in another source must not become a global requirement.
    paused_other = paused_second.pause(
        first,
        pause_id="other-source-drift",
        qualification_id=first_context.evidence.qualification_id,
        scope=(scope[1],),
        reason="unrelated source shift",
        expected_revision=3,
    )
    complete = qualification_context(
        third,
        report(third),
        gate,
        scope=scope,
        remediation_for=(first_incident, second_incident),
    )
    restored = loads(dumps(paused_other), AcceptanceState)
    complete = loads(dumps(complete), QualificationContext)
    resumed_second = restored.resume(
        third, report(third), complete, pause_ids=("second-drift",), expected_revision=4
    )
    resumed_second = loads(dumps(resumed_second), AcceptanceState)
    assert resumed_second.revision == 5 and not resumed_second.is_paused(scope[0])
    resumed_second.require_acceptance(third, report(third), complete, scope[0])
    assert resumed_second.is_paused(scope[1])
    with pytest.raises(ValueError, match="paused"):
        resumed_second.require_acceptance(third, report(third), complete, scope[1])
    assert resumed_second.pauses[:1] == resumed_first.pauses
    with pytest.raises(ValueError, match="drift-invalidated"):
        resumed_second.require_acceptance(first, report(first), first_context, scope[0])


def test_resume_rejects_a_historically_drift_invalidated_release_before_mutating_state() -> None:
    first, second = model(320), model(320, "second")
    gate = PromotionPolicy(1, 0.9)
    first_context = qualification_context(first, report(first), gate)
    scope = first_context.evidence.scope
    paused_first = AcceptanceState(first.project_id, first.recipe_id).pause(
        first,
        pause_id="first-drift",
        qualification_id=first_context.evidence.qualification_id,
        scope=scope,
        reason="first camera shift",
        expected_revision=0,
    )
    second_context = qualification_context(
        second,
        report(second),
        gate,
        remediation_for=(paused_first.incident_reference("first-drift"),),
    )
    resumed = paused_first.resume(
        second, report(second), second_context, pause_ids=("first-drift",), expected_revision=1
    )
    paused_second = resumed.pause(
        second,
        pause_id="second-drift",
        qualification_id=second_context.evidence.qualification_id,
        scope=scope,
        reason="second camera shift",
        expected_revision=2,
    )
    # Even a newly incident-linked assessment cannot restore the old invalidated release identity.
    invalid = qualification_context(
        first,
        report(first),
        gate,
        remediation_for=(paused_second.incident_reference("second-drift"),),
    )
    with pytest.raises(ValueError, match="drift-invalidated"):
        paused_second.resume(
            first, report(first), invalid, pause_ids=("second-drift",), expected_revision=3
        )
    assert paused_second.revision == 3 and paused_second.is_paused(scope[0])
