"""Offline normalized-port fixtures; no SDK, socket, key or paid training."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

import pytest

from labelloop_core.budget import BudgetGate, BudgetPolicy, BudgetRefused
from labelloop_core.training import InferencePolicy, TrainingBackend, TrainingRequest
from labelloop_platform.cloud_training import (
    CloudPlan,
    CloudRun,
    CloudTrainingRefused,
    PlatformTrainingBackend,
)
from labelloop_platform.training_ports import (
    Charge,
    ConnectionGrant,
    ModelSnapshot,
    StartReceipt,
    TransportError,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures/cloud_training.json").read_text())


def plan(side=320, nms="external"):
    request = TrainingRequest(
        "fixture-run",
        "fixture-project-id",
        f"full-letterbox-{side}-v1",
        "release-7",
        "a" * 64,
        "b" * 64,
        "split-1",
        "c" * 64,
        InferencePolicy(side, nms=nms),
    )
    return CloudPlan(
        request,
        "workspace-1",
        "fixture-owner",
        "fixture-project",
        "release-7-full",
        "fixture-dataset",
        "ul://fixture-owner/base-models/approved-yolo26n",
        "base-model-id",
        "rtx-4090",
        "iteration-1",
        "2026-11",
        "fixture-prices-v1",
        690_000,
        900,
    )


class MemoryRuns:
    """Test-only durable-store stand-in, retained across backend instances."""

    def __init__(self):
        self.value = None
        self.history = []
        self.lock = Lock()

    @contextmanager
    def locked(self, project_id, operation_id):
        with self.lock:
            yield self

    def load(self):
        return deepcopy(self.value)

    def save(self, run):
        self.value = deepcopy(run)
        self.history.append(deepcopy(run))


class Ledger:
    """Exercise the delivered pure ledger, not a replacement accounting model."""

    def __init__(self):
        self.gate = BudgetGate(
            "fixture-project-id", BudgetPolicy(10_000_000, 5_000_000, 2_000_000, True)
        )
        self.authorized = True
        self.dispatches = 0
        self.fail_pause_once = False

    def reserve_and_authorize(self, request):
        if not self.authorized or self.gate.pause_reason:
            raise BudgetRefused("paid_stages_paused")
        self.gate._check_limits(request, extra=0)
        self.gate = self.gate.reserve(request)
        return self.gate.entry(request.operation_id)

    def dispatch(self, request):
        self.gate = self.gate.dispatch(request.operation_id)
        self.dispatches += 1
        return self.gate.entry(request.operation_id)

    def reconcile(self, request, charge):
        self.gate = (
            self.gate.settle(request.operation_id, charge.actual_microusd)
            if charge
            else self.gate.unconfirmed(request.operation_id)
        )
        return self.gate.entry(request.operation_id)

    def pause_insufficient_credits(self, request):
        if self.fail_pause_once:
            self.fail_pause_once = False
            raise RuntimeError("fixture ledger crash")
        self.gate = self.gate.insufficient_credits(request.operation_id)


class Secrets:
    def __init__(self):
        self.allowed = True
        self.changed = False

    def authorize(self, p):
        if not self.allowed:
            raise CloudTrainingRefused("credential_scope_denied")
        return ConnectionGrant(
            p.request.project_id,
            "other-workspace" if self.changed else p.workspace_id,
            p.request.run_id,
            p.configuration_sha256,
        )


class Artifacts:
    def __init__(self):
        self.values = {}

    def store(self, sha256, content):
        assert hashlib.sha256(content).hexdigest() == sha256
        self.values[sha256] = content
        return "sha256/" + sha256


class Transport:
    def __init__(self, p, ledger, runs):
        self.p = p
        self.ledger = ledger
        self.runs = runs
        self.model = None
        self.calls = []
        self.available = True
        self.create_error = None
        self.start_error = None
        self.create_lands = True
        self.start_lands = True
        self.drop_control = None
        self.charge_value = None
        self.content = [b"licence-clean ", b"fixture checkpoint bytes"]
        self.start_bodies = []
        self.after_create = None

    def gpu_available(self, grant, gpu_type):
        self.calls.append("availability")
        assert gpu_type == "rtx-4090"
        return self.available

    def find_model(self, grant, p):
        self.calls.append("find")
        return self.model

    def create_model(self, grant, p):
        self.calls.append("create")
        assert self.runs.value.phase == "create_intent"
        assert self.ledger.gate.entry(p.request.run_id).state == "dispatched"
        if self.create_lands:
            self.model = ModelSnapshot(**FIXTURE["model"], model_slug=p.model_slug)
        if self.after_create:
            self.after_create()
        if self.create_error:
            raise self.create_error
        return self.model

    def start(self, grant, body):
        self.calls.append("start")
        assert self.runs.value.phase == "start_intent"
        assert self.ledger.gate.entry(self.p.request.run_id).state == "dispatched"
        self.start_bodies.append(deepcopy(body))
        if self.start_lands:
            self.progress("starting")
        if self.start_error:
            raise self.start_error
        return StartReceipt(**FIXTURE["receipt"])

    def progress(self, state):
        arguments = self.p.train_arguments()
        if self.drop_control:
            arguments.pop(self.drop_control)
        self.model = replace(
            self.model, state=state, resolved_arguments=arguments, **FIXTURE["progress"]
        )

    def read_model(self, grant, model_id, p):
        self.calls.append("read")
        assert self.model.model_id == model_id
        return self.model

    def charge(self, grant, model_id, provider_run_id):
        self.calls.append("charge")
        return self.charge_value

    def download_best(self, grant, model_id, maximum_bytes):
        self.calls.append("download")
        yield from self.content


def rig(p=None):
    p = p or plan()
    ledger, runs, secrets, artifacts = Ledger(), MemoryRuns(), Secrets(), Artifacts()
    transport = Transport(p, ledger, runs)
    ports = dict(
        transport=transport, budget=ledger, secrets=secrets, runs=runs, artifacts=artifacts
    )
    return SimpleNamespace(
        plan=p,
        ledger=ledger,
        runs=runs,
        secrets=secrets,
        artifacts=artifacts,
        transport=transport,
        ports=ports,
        backend=PlatformTrainingBackend(p, **ports),
    )


def advance(r):
    return r.backend.train(r.plan.request)


@pytest.mark.parametrize("side", [320, 640])
@pytest.mark.parametrize("nms", ["external", "end2end"])
def test_complete_recipe_contract_and_checkpoint_lineage(side, nms):
    r = rig(plan(side, nms))
    backend: TrainingBackend[CloudRun] = r.backend
    accepted = backend.train(r.plan.request)
    assert accepted.state == "accepted"
    assert accepted.dataset_version is None  # The start response has no captured version.
    assert accepted.receipt == StartReceipt(**FIXTURE["receipt"])
    body = r.transport.start_bodies[0]
    assert body["gpuType"] == "rtx-4090"
    assert body["captureDatasetVersion"] is True
    args = body["trainArgs"]
    expected_baseline = {
        "imgsz",
        "rect",
        "tile",
        "nms",
        "iou",
        "conf",
        "max_det",
        "agnostic_nms",
        "epochs",
        "batch",
        "seed",
        "deterministic",
        "multi_scale",
        "mosaic",
        "mixup",
        "cutmix",
        "close_mosaic",
        "scale",
        "translate",
        "degrees",
        "shear",
        "perspective",
        "fliplr",
        "flipud",
        "hsv_h",
        "hsv_s",
        "hsv_v",
        "erasing",
        "model",
        "data",
    }
    assert set(args) == expected_baseline
    assert args["imgsz"] == side and args["nms"] == (nms == "external")
    assert args["data"] == "ul://fixture-owner/datasets/release-7-full"
    assert args["model"] == r.plan.base_model_uri
    assert args["tile"] is False and args["rect"] is False
    for record in r.plan.stage_contracts().values():
        assert all(record[k] == v for k, v in args.items() if k not in {"model", "data"})
    assert r.plan.stage_contracts()["export"]["dynamic"] is False
    r.transport.progress("running")
    active = advance(r)
    assert active.state == "active"
    assert active.dataset_version == 7 and active.dataset_content_hash == "d" * 64
    r.transport.progress("completed")
    completed = advance(r)
    content = b"".join(r.transport.content)
    assert completed.state == "completed"
    assert completed.checkpoint.sha256 == hashlib.sha256(content).hexdigest()
    assert completed.checkpoint.selection == "provider_rect_validation"
    assert completed.request.initialization_sha256 == "c" * 64
    assert "initialization_hash_unverified_on_provider" in completed.limitations
    assert r.artifacts.values[completed.checkpoint.sha256] == content
    entry = r.ledger.gate.entry(r.plan.request.run_id)
    assert entry.actual is None and entry.exposure == 172_500
    # A new process reuses durable identity and never pays for another start.
    r.backend = PlatformTrainingBackend(r.plan, **r.ports)
    assert advance(r).model_id == completed.model_id
    assert r.transport.calls.count("start") == r.transport.calls.count("create") == 1
    assert r.ledger.dispatches == 1


@pytest.mark.parametrize("missing", ["transport", "budget", "secrets", "runs", "artifacts"])
def test_missing_port_disables_all_io(missing):
    r = rig()
    r.ports[missing] = None
    r.backend = PlatformTrainingBackend(r.plan, **r.ports)
    with pytest.raises(CloudTrainingRefused, match="ports_unavailable"):
        advance(r)
    assert r.transport.calls == [] and r.runs.value is None


@pytest.mark.parametrize("denial", ["secret", "grant", "budget", "limits", "manual"])
def test_authorization_denials_never_mutate(denial):
    r = rig()
    if denial == "secret":
        r.secrets.allowed = False
    elif denial == "grant":
        r.secrets.changed = True
    elif denial == "budget":
        r.ledger.authorized = False
    elif denial == "manual":
        r.ledger.gate = replace(r.ledger.gate, policy=BudgetPolicy(999999, 999999, 999999))
    else:
        r.ledger.gate = replace(r.ledger.gate, policy=BudgetPolicy(1, 1, 1, True))
    with pytest.raises((BudgetRefused, CloudTrainingRefused)):
        advance(r)
    assert "start" not in r.transport.calls and "create" not in r.transport.calls


def test_current_authorization_rechecked_between_create_and_start():
    r = rig()
    r.transport.after_create = lambda: setattr(r.ledger, "authorized", False)
    with pytest.raises(BudgetRefused):
        advance(r)
    assert r.runs.value.phase == "model_created" and "start" not in r.transport.calls
    r.ledger.authorized = True
    assert advance(r).state == "accepted"
    assert r.transport.calls.count("create") == 1


@pytest.mark.parametrize("stage", ["create", "start"])
@pytest.mark.parametrize("status", [None, 409, 429])
@pytest.mark.parametrize("lands", [False, True])
def test_uncertain_mutation_reconciles_never_retries(stage, status, lands):
    r = rig()
    setattr(r.transport, stage + "_error", TransportError(status))
    setattr(r.transport, stage + "_lands", lands)
    first = advance(r)
    assert first.state == "uncertain_" + stage
    r.backend = PlatformTrainingBackend(r.plan, **r.ports)
    second = advance(r)
    if lands:
        assert second.state == "accepted"
        assert second.model_id == "fixture-model"
    else:
        assert second.state == first.state
    assert r.transport.calls.count(stage) == 1
    assert len(r.ledger.gate.entries) == 1


@pytest.mark.parametrize("stage", ["create", "start"])
@pytest.mark.parametrize("status", [402, 503])
def test_terminal_for_now_never_retries_and_402_pauses_paid_stages(stage, status):
    r = rig()
    setattr(r.transport, stage + "_error", TransportError(status))
    setattr(r.transport, stage + "_lands", False)
    result = advance(r)
    assert result.state == ("insufficient_credits" if status == 402 else "unavailable")
    for _ in range(3):
        assert advance(r).state == result.state
    assert r.transport.calls.count(stage) == 1
    assert (r.ledger.gate.pause_reason == "insufficient_credits") == (status == 402)
    assert r.ledger.gate.entry(r.plan.request.run_id).actual is None


def test_402_pause_is_recovered_after_ledger_failure_without_provider_retry():
    r = rig()
    r.transport.start_error = TransportError(402)
    r.transport.start_lands = False
    r.ledger.fail_pause_once = True
    with pytest.raises(RuntimeError, match="fixture ledger crash"):
        advance(r)
    assert r.runs.value.state == "insufficient_credits"
    assert advance(r).state == "insufficient_credits"
    assert r.ledger.gate.pause_reason == "insufficient_credits"
    assert r.transport.calls.count("start") == 1


@pytest.mark.parametrize("key", sorted(plan().train_arguments()))
def test_every_missing_resolved_key_is_args_mismatch(key):
    r = rig()
    r.transport.drop_control = key
    advance(r)
    result = advance(r)
    assert result.state == "args_mismatch" and key in result.mismatched_keys
    r.transport.progress("completed")
    assert advance(r).checkpoint is None
    assert r.transport.calls.count("start") == 1


def test_changed_arguments_never_persist_untrusted_secret_values(capsys):
    r = rig()
    advance(r)
    marker = "fixture-sensitive-value-not-a-real-key"
    r.transport.model = replace(
        r.transport.model,
        resolved_arguments={**r.plan.train_arguments(), "model": marker, "unknown": marker},
    )
    result = advance(r)
    assert result.state == "args_mismatch" and "model" in result.mismatched_keys
    assert marker not in repr(result) + json.dumps(asdict(result))
    assert marker not in capsys.readouterr().out
    r.transport.progress("completed")
    assert advance(r).state == "args_mismatch"  # A later correction cannot erase evidence.


@pytest.mark.parametrize(
    "changes",
    [
        {"dataset_id": "other"},
        {"dataset_version": None},
        {"dataset_content_hash": None},
        {"dataset_version": True},
        {"dataset_content_hash": "invalid"},
    ],
)
def test_missing_or_wrong_captured_dataset_blocks_candidate(changes):
    r = rig()
    advance(r)
    r.transport.model = replace(r.transport.model, **changes)
    assert advance(r).state == "args_mismatch"
    assert "download" not in r.transport.calls


def test_version_and_provider_job_cannot_change_under_existing_operation():
    r = rig()
    advance(r)
    assert advance(r).dataset_version == 7
    r.transport.model = replace(r.transport.model, dataset_version=8)
    assert "dataset_identity" in advance(r).mismatched_keys
    r.transport.model = replace(r.transport.model, provider_run_id="different-job")
    assert advance(r).state == "identity_mismatch"
    assert r.runs.value.provider_run_id == "fixture-job"


@pytest.mark.parametrize("state", ["failed", "cancelled", "completed"])
def test_terminal_provider_states_keep_lineage_and_attributable_actual(state):
    r = rig()
    advance(r)
    r.transport.progress(state)
    r.transport.charge_value = Charge(**FIXTURE["charge"])
    result = advance(r)
    assert result.state == state and result.provider_run_id == "fixture-job"
    assert result.model_id == "fixture-model" and result.dataset_version == 7
    assert result.charge.actual_microusd == 120_000
    assert r.ledger.gate.entry(r.plan.request.run_id).exposure == 120_000
    assert (result.checkpoint is not None) == (state == "completed")
    assert advance(r).charge == result.charge
    assert r.transport.calls.count("start") == 1


def test_unattributable_charge_keeps_estimate_until_confirmed():
    r = rig()
    advance(r)
    r.transport.progress("cancelled")
    r.transport.charge_value = replace(Charge(**FIXTURE["charge"]), provider_run_id="other-job")
    assert advance(r).charge is None
    assert r.ledger.gate.entry(r.plan.request.run_id).exposure == 172_500
    r.transport.charge_value = Charge(**FIXTURE["charge"])
    assert advance(r).charge.actual_microusd == 120_000
    assert r.transport.calls.count("start") == 1


def test_changed_plan_and_request_refused_for_existing_operation():
    r = rig()
    advance(r)
    for changed in (
        replace(r.plan, gpu_type="b300"),
        replace(r.plan, dataset_slug="other"),
        replace(r.plan, base_model_id="other"),
        replace(r.plan, estimated_seconds=1800),
    ):
        backend = PlatformTrainingBackend(changed, **r.ports)
        with pytest.raises(CloudTrainingRefused, match="operation_conflict"):
            backend.train(changed.request)
    with pytest.raises(CloudTrainingRefused, match="request_mismatch"):
        r.backend.train(replace(r.plan.request, recipe_id="different"))
    assert r.transport.calls.count("start") == 1


def test_concurrent_duplicate_attempts_have_one_create_and_paid_start():
    r = rig()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: advance(r), range(8)))
    assert all(result.model_id == "fixture-model" for result in results)
    assert r.transport.calls.count("create") == r.transport.calls.count("start") == 1
    assert r.ledger.dispatches == 1


def test_gpu_availability_and_minimum_reservation():
    with pytest.raises(ValueError, match="fifteen minutes"):
        replace(plan(), estimated_seconds=899)
    r = rig()
    r.transport.available = False
    assert advance(r).state == "gpu_unavailable"
    assert "create" not in r.transport.calls and "start" not in r.transport.calls


@pytest.mark.parametrize("content", [[], [b""], [b"too many bytes"], ["not bytes"]])
def test_empty_oversized_or_malformed_checkpoint_is_not_qualified(content):
    r = rig()
    r.backend = PlatformTrainingBackend(r.plan, **r.ports, maximum_checkpoint_bytes=4)
    advance(r)
    r.transport.progress("completed")
    r.transport.content = content
    result = advance(r)
    assert result.checkpoint is None and result.error_code == "invalid_checkpoint"
    assert r.artifacts.values == {}


def test_warm_start_is_a_new_operation_with_explicit_parent_and_new_reservation():
    r = rig()
    advance(r)
    r.transport.progress("cancelled")
    advance(r)
    child_plan = replace(
        r.plan,
        request=replace(r.plan.request, run_id="child-run", initialization_sha256="e" * 64),
        parent_run_id=r.plan.request.run_id,
    )
    child = rig(child_plan)
    child.ledger = r.ledger
    child.ports["budget"] = r.ledger
    child.transport.ledger = r.ledger
    child.backend = PlatformTrainingBackend(child_plan, **child.ports)
    assert advance(child).state == "accepted"
    assert len(r.ledger.gate.entries) == 2
    assert child_plan.model_slug != r.plan.model_slug
    assert child_plan.parent_run_id == "fixture-run"
    assert r.runs.value.provider_state == "cancelled"
