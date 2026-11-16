"""A fake transport asserts persisted intent precedes every external mutation."""

from dataclasses import replace

import pytest
from test_sync_reconciliation import intent as intent
from test_sync_reconciliation import reconcile
from test_sync_reconciliation import remote as remote

from labelloop_platform.release_sync import (
    Action,
    DatasetEvidence,
    MutationOutcome,
    Phase,
    ReleaseSync,
    UploadSession,
    dispatch_once,
)
from labelloop_platform.sync_records import SyncConflict


class Journal:
    def __init__(self, state):
        self.state = state
        self.records = [state]

    def load(self, operation_id):
        assert operation_id == self.state.intent.operation_id
        return self.state

    def compare_and_set(self, expected, updated):
        if expected != self.state:
            return False
        self.state = updated
        self.records.append(updated)
        return True


class Transport:
    def __init__(self, journal, outcomes):
        self.journal = journal
        self.outcomes = iter(outcomes)
        self.commands = []

    def execute(self, command):
        assert self.journal.state.phase == Phase.IN_FLIGHT
        assert self.journal.state.pending == command
        self.commands.append(command)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_upload_intent_outcome_queued_complete_and_frozen_are_distinct(intent, remote):
    journal = Journal(ReleaseSync(intent, "fixture", "release-7-full320"))
    transport = Transport(
        journal,
        [
            MutationOutcome("ok", dataset_id="dataset-1"),
            MutationOutcome("ok", session=UploadSession("session-1", 43200)),
            MutationOutcome("ok"),
            MutationOutcome("queued", job_id="job-1"),
        ],
    )
    for phase in (Phase.DATASET, Phase.SIGNED, Phase.UPLOADED, Phase.PROCESSING):
        assert dispatch_once(journal, transport, "sync-1", now=0).phase == phase
    assert [command.action for command in transport.commands] == [
        Action.CREATE,
        Action.SIGN,
        Action.PUT,
        Action.INGEST,
    ]
    assert "targetSplit" not in transport.commands[-1].payload_json
    with pytest.raises(SyncConflict, match="no mutation"):
        dispatch_once(journal, transport, "sync-1", now=0)
    complete = journal.state.processing_finished("job-1", succeeded=True)
    assert complete.phase == Phase.RECONCILE
    frozen = complete.freeze(reconcile(intent, remote))
    assert frozen.phase == Phase.FROZEN
    with pytest.raises(SyncConflict):
        frozen.prepare(0)
    assert frozen.frozen.images[0].revision == 2


def test_uncertain_create_reconciles_returned_id_without_duplicate_create(intent):
    journal = Journal(ReleaseSync(intent, "fixture", "release-7-full320"))
    transport = Transport(journal, [RuntimeError("untrusted response body")])
    uncertain = dispatch_once(journal, transport, "sync-1", now=0)
    assert uncertain.phase == Phase.UNCERTAIN
    assert "untrusted response" not in repr(uncertain)
    with pytest.raises(SyncConflict):
        dispatch_once(journal, transport, "sync-1", now=0)
    evidence = DatasetEvidence(
        "dataset-1",
        "fixture",
        "release-7-full320",
        "private",
        "detect",
        intent.class_names,
        intent.operation_id,
        intent.manifest_sha256,
        intent.archive_sha256,
    )
    recovered = uncertain.recover_dataset(evidence)
    assert recovered.phase == Phase.DATASET
    assert recovered.dataset_id == "dataset-1"
    assert len(transport.commands) == 1
    with pytest.raises(SyncConflict):
        uncertain.recover_dataset(
            replace(evidence, class_names=tuple(reversed(intent.class_names)))
        )


def test_restart_after_intent_before_response_cannot_dispatch_again(intent):
    before = ReleaseSync(intent, "fixture", "release-7-full320")
    persisted, _ = before.prepare(0)
    restarted = Journal(persisted)
    transport = Transport(restarted, [])
    with pytest.raises(SyncConflict, match="reconcile"):
        dispatch_once(restarted, transport, "sync-1", now=0)
    assert not transport.commands


@pytest.mark.parametrize("kind", ["expired", "precondition_failed"])
def test_create_only_put_recovery_requires_new_signed_session(intent, kind):
    state = ReleaseSync(
        intent,
        "fixture",
        "release-7-full320",
        phase=Phase.SIGNED,
        dataset_id="dataset-1",
        session=UploadSession("old-session", 43200),
    )
    journal = Journal(state)
    transport = Transport(journal, [MutationOutcome(kind)])
    recovered = dispatch_once(journal, transport, "sync-1", now=0)
    assert recovered.phase == Phase.DATASET and recovered.session is None
    assert recovered.retired_sessions == ("old-session",)
    prepared, command = recovered.prepare(0)
    assert command.action == Action.SIGN
    with pytest.raises(SyncConflict, match="retired session"):
        prepared.observe(
            command, MutationOutcome("ok", session=UploadSession("old-session", 50000))
        )


def test_expired_or_uncertain_put_never_reuses_storage_target(intent):
    signed = ReleaseSync(
        intent,
        "fixture",
        "release-7-full320",
        phase=Phase.SIGNED,
        dataset_id="dataset-1",
        session=UploadSession("session-1", 10),
    )
    with pytest.raises(SyncConflict, match="expired"):
        signed.prepare(10)
    assert signed.retire_session().phase == Phase.DATASET
    journal = Journal(signed)
    transport = Transport(journal, [MutationOutcome("uncertain")])
    uncertain = dispatch_once(journal, transport, "sync-1", now=0)
    assert uncertain.retire_session().phase == Phase.DATASET


def test_uncertain_ingest_reconciles_membership_never_requeues(intent, remote):
    uploaded = ReleaseSync(
        intent,
        "fixture",
        "release-7-full320",
        phase=Phase.UPLOADED,
        dataset_id="dataset-1",
        session=UploadSession("session-1", 43200),
    )
    journal = Journal(uploaded)
    transport = Transport(journal, [MutationOutcome("uncertain")])
    uncertain = dispatch_once(journal, transport, "sync-1", now=0)
    with pytest.raises(SyncConflict, match="never repeat"):
        uncertain.retire_session()
    with pytest.raises(SyncConflict):
        dispatch_once(journal, transport, "sync-1", now=0)
    frozen = uncertain.freeze(reconcile(intent, remote))
    assert frozen.phase == Phase.FROZEN and len(transport.commands) == 1


def test_cas_failure_prevents_parallel_mutation(intent):
    class Contended(Journal):
        def compare_and_set(self, expected, updated):
            return False

    journal = Contended(ReleaseSync(intent, "fixture", "release-7-full320"))
    transport = Transport(journal, [])
    with pytest.raises(SyncConflict, match="another writer"):
        dispatch_once(journal, transport, "sync-1", now=0)
    assert not transport.commands


def test_throttled_deferral_records_deadline_and_gates_reprepare(intent):
    journal = Journal(ReleaseSync(intent, "fixture", "release-7-full320"))
    transport = Transport(journal, [MutationOutcome("deferred", retry_at=60.0)])
    state = dispatch_once(journal, transport, "sync-1", now=0)
    assert state.phase == Phase.NEW
    assert state.pending is None
    assert state.retry_at == 60.0
    records = len(journal.records)
    with pytest.raises(SyncConflict, match="throttled"):
        dispatch_once(journal, transport, "sync-1", now=30)
    # Refusal happens before any journal write: no hot-spin history growth.
    assert len(journal.records) == records
    assert len(transport.commands) == 1
    resumed, command = state.prepare(60)
    assert resumed.phase == Phase.IN_FLIGHT
    assert resumed.pending == command
    assert resumed.retry_at is None


def test_failed_and_malformed_outcomes_are_not_retried(intent):
    for outcome, phase in [
        (MutationOutcome("failed"), Phase.FAILED),
        (MutationOutcome("ok"), Phase.UNCERTAIN),
    ]:
        journal = Journal(ReleaseSync(intent, "fixture", "release-7-full320"))
        transport = Transport(journal, [outcome])
        assert dispatch_once(journal, transport, "sync-1", now=0).phase == phase
        with pytest.raises(SyncConflict):
            dispatch_once(journal, transport, "sync-1", now=0)


def test_stale_ingest_completion_and_wrong_mapping_fail(intent, remote):
    state = ReleaseSync(
        intent,
        "fixture",
        "release-7-full320",
        phase=Phase.PROCESSING,
        dataset_id="dataset-1",
        job_id="job-1",
    )
    with pytest.raises(SyncConflict):
        state.processing_finished("stale-job", succeeded=True)
    assert state.processing_finished("job-1", succeeded=False).phase == Phase.FAILED
    completed = state.processing_finished("job-1", succeeded=True)
    with pytest.raises(SyncConflict, match="different release intent"):
        completed.freeze(replace(reconcile(intent, remote), archive_sha256="f" * 64))
