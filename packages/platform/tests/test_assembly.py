"""Composition fixtures, not production principal/persistence or live qualification."""

import json
from dataclasses import fields, replace
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from test_cloud_training import FIXTURE as CLOUD_FIXTURE
from test_cloud_training import Transport, plan, rig

from labelloop_core.budget import BudgetRefused
from labelloop_platform import Operation, Outcome, PlatformAdapter, PlatformRequest, Throttle
from labelloop_platform.assembly import (
    AssemblyRefused,
    ReleaseBinding,
    ReleaseReadback,
    assemble_training,
)
from labelloop_platform.training_ports import Charge, StartReceipt

FIXTURE = json.loads((Path(__file__).parent / "fixtures/assembly.json").read_text())


class SyncFixture:
    def __init__(self, side=320):
        binding = ReleaseBinding(**{**FIXTURE["release"], "recipe_id": f"full-letterbox-{side}-v1"})
        self.value = ReleaseReadback(binding, "verified", "fixture-reconciliation")
        self.calls = []
        self.error = None

    def read(self, p, grant):
        self.calls.append(grant)
        if self.error:
            raise self.error
        return self.value


class WireFixtureTransport(Transport):
    """Test-only normalization glue; NOT a delivered TrainingTransport adapter.

    Exercise the real pinned HTTP adapter for start. Remaining normalized methods
    reuse the cloud producer's recorded fixture ports. No sockets are opened.
    """

    def __init__(self, p, ledger, runs, client):
        super().__init__(p, ledger, runs)
        self.client = client

    def start(self, grant, body):
        super().start(grant, body)  # Assert durable intent and reservation ordering.
        result = self.client.execute(
            PlatformRequest(Operation.START_TRAINING, self.p.request.run_id, body=dict(body))
        )
        assert result.outcome == Outcome.SUCCEEDED
        data = result.data
        cost, billing = data["estimatedCost"], data["billing"]
        return StartReceipt(
            data["modelId"],
            data["gpuType"],
            int(Decimal(str(cost["pricePerHour"])) * 1_000_000),
            cost["gpuMemoryGb"],
            int(Decimal(str(billing["estimatedCostCents"])) * 10_000),
            int(Decimal(str(billing["balanceCents"])) * 10_000),
        )


def composition(side=320):
    p = plan(side)
    r = rig(p)
    sync = SyncFixture(side)
    return p, dict(r.ports, sync=sync), r.ledger, r.runs, r.transport, r.secrets, r.artifacts


@pytest.mark.parametrize("side", [320, 640])
def test_recorded_composition_reuses_transport_backend_and_ledger(side):
    p, ports, ledger, runs, _, _, artifacts = composition(side)
    wire = []

    def respond(request):
        wire.append(request)
        assert runs.value.phase == "start_intent"
        assert ledger.read(p.spend_request()).state == "dispatched"
        assert str(request.url) == "https://platform.ultralytics.com/api/training/start"
        return httpx.Response(200, json=FIXTURE["start_response"])

    client = PlatformAdapter(
        "fixture-platform-bearer-not-a-credential",
        transport=httpx.MockTransport(respond),
        throttle=Throttle(lambda: 0.0),
    )
    try:
        transport = WireFixtureTransport(p, ledger, runs, client)
        ports["transport"] = transport
        backend = assemble_training(p, **ports)
        assert ledger.gate.entries == ()
        assert runs.history == [] and wire == []
        run = backend.train(p.request)
        assert run.state == "accepted"
        assert run.receipt.estimated_cost_microusd == p.spend_request().estimate
        assert run.charge is None
        assert len(wire) == 1
        body = json.loads(wire[0].content)
        assert body["trainArgs"] == p.train_arguments()
        assert body["trainArgs"]["imgsz"] == side
        assert body["captureDatasetVersion"] is True
        transport.progress("completed")
        transport.charge_value = Charge(**CLOUD_FIXTURE["charge"])
        completed = backend.train(p.request)
        assert completed.state == "completed"
        assert completed.dataset_content_hash == FIXTURE["release"]["platform_content_hash"]
        assert completed.checkpoint.sha256 in artifacts.values
        assert ledger.read(p.spend_request()).actual == 120_000
        assert len(wire) == 1 and ledger.dispatches == 1
    finally:
        client.close()


@pytest.mark.parametrize("missing", ["sync", "transport", "budget", "secrets", "runs", "artifacts"])
def test_missing_producer_never_reads_or_reserves(missing):
    p, ports, ledger, runs, transport, _, _ = composition()
    sync = ports["sync"]
    ports[missing] = None
    with pytest.raises(AssemblyRefused, match="bindings_unavailable"):
        assemble_training(p, **ports)
    assert sync.calls == [] and ledger.gate.entries == ()
    assert runs.history == [] and transport.calls == []


@pytest.mark.parametrize("changed", [field.name for field in fields(ReleaseBinding)])
def test_each_release_identity_must_match_before_reservation(changed):
    p, ports, ledger, runs, transport, _, _ = composition()
    sync = ports["sync"]
    sync.value = replace(sync.value, binding=replace(sync.value.binding, **{changed: "other"}))
    with pytest.raises(AssemblyRefused, match="sync_binding_mismatch"):
        assemble_training(p, **ports)
    assert ledger.gate.entries == () and runs.history == [] and transport.calls == []


@pytest.mark.parametrize("state", ["pending", "incomplete", "conflicted", "unexpected"])
def test_incomplete_sync_does_not_become_training_ready(state):
    p, ports, ledger, _, transport, _, _ = composition()
    ports["sync"].value = replace(ports["sync"].value, state=state)
    with pytest.raises(AssemblyRefused, match="sync_incomplete"):
        assemble_training(p, **ports)
    assert ledger.gate.entries == () and transport.calls == []


@pytest.mark.parametrize("missing_record", [True, False])
def test_mapping_and_persisted_reconciliation_are_required(missing_record):
    p, ports, _, _, transport, _, _ = composition()
    sync = ports["sync"]
    sync.value = None if missing_record else replace(sync.value, reconciliation_id="")
    with pytest.raises(AssemblyRefused, match="sync_mapping_missing|sync_incomplete"):
        assemble_training(p, **ports)
    assert transport.calls == []


@pytest.mark.parametrize("changed_scope", [True, False])
def test_missing_or_mismatched_auth_never_reads_sync(changed_scope):
    p, ports, ledger, _, transport, secrets, _ = composition()
    secrets.changed = changed_scope
    secrets.allowed = changed_scope
    with pytest.raises(AssemblyRefused, match="connection_binding_mismatch|connection_unavailable"):
        assemble_training(p, **ports)
    assert ports["sync"].calls == [] and transport.calls == [] and ledger.gate.entries == ()


def test_sync_persistence_failure_is_closed_and_sanitized():
    p, ports, ledger, _, transport, _, _ = composition()
    ports["sync"].error = RuntimeError("fixture-secret-must-not-escape")
    with pytest.raises(AssemblyRefused, match="sync_persistence_unavailable") as failure:
        assemble_training(p, **ports)
    assert "fixture-secret" not in str(failure.value)
    assert ledger.gate.entries == () and transport.calls == []


def test_run_persistence_failure_prevents_provider_dispatch(monkeypatch):
    p, ports, ledger, runs, transport, _, _ = composition()
    backend = assemble_training(p, **ports)

    def fail(run):
        raise RuntimeError("fixture persistence unavailable")

    monkeypatch.setattr(runs, "save", fail)
    with pytest.raises(RuntimeError, match="persistence unavailable"):
        backend.train(p.request)
    assert ledger.read(p.spend_request()).state == "reserved"
    assert transport.calls == []


def test_assembly_does_not_cache_authority_or_bypass_budget():
    p, ports, ledger, _, transport, secrets, _ = composition()
    backend = assemble_training(p, **ports)
    secrets.allowed = False
    with pytest.raises(ValueError, match="credential_scope_denied"):
        backend.train(p.request)
    assert ledger.gate.entries == () and transport.calls == []
    secrets.allowed = True
    ledger.authorized = False
    with pytest.raises(BudgetRefused, match="paid_stages_paused"):
        backend.train(p.request)
    assert transport.calls == []
