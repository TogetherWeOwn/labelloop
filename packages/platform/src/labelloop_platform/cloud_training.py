"""One durable cloud-training advance, with reconcile-only uncertain mutations.

No production wiring, polling loop, provider credentials or live calls. Repeated
train(request) advances one operation, never restarts it. A new experiment needs a
new run ID (and budget authorization), with its checkpoint parent recorded.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from typing import Literal

from labelloop_core.budget import LedgerEntry, SpendRequest, identity, nonnegative
from labelloop_core.training import TrainingRequest, argument_diff
from labelloop_platform.training_ports import (
    ArtifactPort,
    BudgetPort,
    Charge,
    ConnectionGrant,
    ModelSnapshot,
    ProviderState,
    RunSession,
    RunStore,
    SecretPort,
    StartReceipt,
    TrainingTransport,
    TransportError,
)

CloudState = Literal[
    "prepared",
    "model_created",
    "accepted",
    "active",
    "completed",
    "failed",
    "cancelled",
    "uncertain_create",
    "uncertain_start",
    "args_mismatch",
    "identity_mismatch",
    "insufficient_credits",
    "unavailable",
    "gpu_unavailable",
]
Phase = Literal[
    "prepared",
    "dispatch_intent",
    "create_intent",
    "model_created",
    "start_intent",
    "started",
    "terminal",
]
_TERMINAL_PROVIDER = {"completed", "failed", "cancelled"}
_PROVIDER_STATES = {"untrained", "pending", "starting", "running", *_TERMINAL_PROVIDER}
_LOCAL_ONLY = {"device", "amp", "workers", "save", "save_period", "plots"}


class CloudTrainingRefused(ValueError):
    pass


@dataclass(frozen=True)
class CloudPlan:
    request: TrainingRequest
    workspace_id: str
    owner: str
    project_slug: str
    dataset_slug: str
    dataset_id: str
    base_model_uri: str
    base_model_id: str
    gpu_type: str
    iteration_id: str
    month: str
    price_version: str
    hourly_rate_microusd: int
    estimated_seconds: int
    expected_dataset_content_hash: str
    parent_run_id: str | None = None

    def __post_init__(self) -> None:
        for value in (self.owner, self.project_slug, self.dataset_slug, self.gpu_type):
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}", value):
                raise ValueError("invalid Platform resource identity")
        if not self.workspace_id or not self.dataset_id or not self.base_model_id:
            raise ValueError("resolved workspace/dataset/base model IDs are required")
        if not re.fullmatch(
            r"ul://[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+", self.base_model_uri
        ):
            raise ValueError("approved base model requires a supported unversioned URI")
        # Platform contentHash comes from verified synchronization, not the compiler
        # manifest hash. The captured version can be new; its content must still match.
        identity(self.expected_dataset_content_hash)
        nonnegative(self.hourly_rate_microusd)
        if self.hourly_rate_microusd == 0 or type(self.estimated_seconds) is not int:
            raise ValueError("training needs a positive pinned price and duration")
        if self.estimated_seconds < 900:
            raise ValueError("reserve at least fifteen minutes")
        if self.parent_run_id == self.request.run_id:
            raise ValueError("a run cannot be its own checkpoint parent")
        self.spend_request()  # Validate iteration, month, price version and estimate.

    @property
    def model_slug(self) -> str:
        # A supported resource lookup key, NOT a vendor idempotency key.
        binding = json.dumps([self.request.project_id, self.request.run_id], separators=(",", ":"))
        digest = hashlib.sha256(binding.encode()).hexdigest()
        return "ll-" + digest[:32]

    @property
    def configuration_sha256(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def spend_request(self) -> SpendRequest:
        estimate = (self.hourly_rate_microusd * self.estimated_seconds + 3599) // 3600
        return SpendRequest(
            self.request.run_id,
            self.request.project_id,
            self.iteration_id,
            self.month,
            estimate,
            self.price_version,
            self.configuration_sha256,
        )

    def train_arguments(self) -> dict[str, object]:
        return {
            **{k: v for k, v in self.request.arguments().items() if k not in _LOCAL_ONLY},
            "model": self.base_model_uri,
            "data": f"ul://{self.owner}/datasets/{self.dataset_slug}",
        }

    def stage_contracts(self) -> dict[str, dict[str, object]]:
        """Requested downstream records, not proof cloud validation ran at this shape.

        Retain baseline controls as release metadata on every stage. Only training
        consumes augmentation knobs; do not forward them as val/predict/export kwargs.
        Controlled downstream workers still need tensor/export-signature evidence.
        """
        shared = {k: v for k, v in self.train_arguments().items() if k not in {"model", "data"}}
        return {
            stage: {**shared, **({"dynamic": False} if stage == "export" else {})}
            for stage in ("train", "val", "predict", "export")
        }


@dataclass(frozen=True)
class CloudCheckpoint:
    sha256: str
    size: int
    artifact_ref: str
    selection: Literal["provider_rect_validation"] = "provider_rect_validation"


@dataclass(frozen=True)
class CloudRun:
    request: TrainingRequest
    configuration_sha256: str
    spend: SpendRequest
    plan: CloudPlan
    phase: Phase = "prepared"
    state: CloudState = "prepared"
    model_id: str | None = None
    provider_run_id: str | None = None
    provider_state: ProviderState | None = None
    dataset_version: int | None = None
    dataset_content_hash: str | None = None
    receipt: StartReceipt | None = None
    pricing_unconfirmed: bool = False
    mismatched_keys: tuple[str, ...] = ()
    resolved_arguments: tuple[tuple[str, object], ...] = ()
    checkpoint: CloudCheckpoint | None = None
    charge: Charge | None = None
    error_code: str | None = None
    # These remain true even with a locally supplied initialization digest.
    limitations: tuple[str, ...] = (
        "initialization_hash_unverified_on_provider",
        "provider_rect_checkpoint_selection",
        "provider_environment_and_dataloader_unverified",
        "downstream_fixed_shape_evaluation_required",
    )


class PlatformTrainingBackend:
    """Implements TrainingBackend[CloudRun], without inventing fixed-shape selection.

    Each port must be supplied by trusted composition. Default construction cannot
    mutate anything. Tests supply fixture capabilities; no production enable flag
    or user-provided 'authorized' boolean substitutes for the ports' enforcement.
    """

    def __init__(
        self,
        plan: CloudPlan,
        *,
        transport: TrainingTransport | None = None,
        budget: BudgetPort | None = None,
        secrets: SecretPort | None = None,
        runs: RunStore | None = None,
        artifacts: ArtifactPort | None = None,
        maximum_checkpoint_bytes: int = 128 * 1024 * 1024,
    ) -> None:
        if type(maximum_checkpoint_bytes) is not int or maximum_checkpoint_bytes < 1:
            raise ValueError("checkpoint bound must be positive")
        self.plan = plan
        self.transport = transport
        self.budget = budget
        self.secrets = secrets
        self.runs = runs
        self.artifacts = artifacts
        self.maximum_checkpoint_bytes = maximum_checkpoint_bytes

    def train(self, request: TrainingRequest) -> CloudRun:
        if request != self.plan.request:
            raise CloudTrainingRefused("request_mismatch")
        if any(
            port is None
            for port in (self.transport, self.budget, self.secrets, self.runs, self.artifacts)
        ):
            raise CloudTrainingRefused("ports_unavailable")
        assert self.secrets is not None and self.runs is not None and self.budget is not None
        grant = self.secrets.authorize(self.plan)
        expected_grant = ConnectionGrant(
            request.project_id,
            self.plan.workspace_id,
            request.run_id,
            self.plan.configuration_sha256,
        )
        if grant != expected_grant:
            raise CloudTrainingRefused("authorization_mismatch")
        spend = self.plan.spend_request()
        with self.runs.locked(request.project_id, request.run_id) as session:
            run = session.load()
            if run is not None and (
                run.request != request
                or run.configuration_sha256 != self.plan.configuration_sha256
                or run.spend != spend
            ):
                raise CloudTrainingRefused("operation_conflict")
            if run is None:
                entry = self.budget.reserve_and_authorize(spend)
                self._validate_entry(entry, spend, "reserved")
                run = CloudRun(request, self.plan.configuration_sha256, spend, self.plan)
                session.save(run)
            self._observe_estimate(run)
            # A bound job can have a final charge before model readback reports
            # completion (or while that endpoint is down). Reconcile it once here.
            if run.provider_run_id:
                run = self._settle(session, run, grant)
            if run.phase == "terminal":
                self._release_undispatched(run)
                if run.state == "insufficient_credits":
                    self.budget.pause_insufficient_credits(run.spend)
                if run.state == "unavailable":
                    return self._recover_unavailable(session, run, grant)
                return run  # Terminal-for-now never authorizes another mutation.
            return self._advance(session, run, grant)

    def _release_undispatched(self, run: CloudRun) -> None:
        assert self.budget is not None
        entry = self.budget.read(run.spend)
        if entry.request != run.spend:
            raise CloudTrainingRefused("budget_authorization_mismatch")
        if entry.state == "reserved":
            self._validate_entry(self.budget.release(run.spend), run.spend, "cancelled")

    def _observe_estimate(self, run: CloudRun) -> None:
        assert self.budget is not None
        if run.pricing_unconfirmed:
            # Readback has no authoritative pricing. Keep further paid work paused
            # even when the accepted start response or its durable save was lost.
            self.budget.pause_pricing_uncertain(run.spend)
        if run.receipt is not None:
            rate_estimate = (
                run.receipt.price_per_hour_microusd * self.plan.estimated_seconds + 3599
            ) // 3600
            estimate = max(rate_estimate, run.receipt.estimated_cost_microusd)
            if estimate > run.spend.estimate:
                # Receipt is already durable. A failed pause is retried before any
                # subsequent operation advancement, never by restarting the job.
                self.budget.pause_estimate_overrun(run.spend, estimate)

    def _recover_unavailable(
        self, session: RunSession, run: CloudRun, grant: ConnectionGrant
    ) -> CloudRun:
        assert self.transport is not None
        if run.model_id is None:
            try:
                model = self.transport.find_model(grant, self.plan)
            except Exception:
                return self._save(session, run, error_code="reconcile_unavailable")
            if model is None:
                return run
            if not self._identity(model, run):
                return self._save(session, run, state="identity_mismatch")
            run = self._save(session, run, model_id=model.model_id)
        # No call to _advance: a 503 never authorizes create or a first/repeated start.
        return self._reconcile(session, run, grant)

    @staticmethod
    def _validate_entry(entry: LedgerEntry, spend: SpendRequest, state: str) -> None:
        if entry.request != spend or entry.state != state:
            raise CloudTrainingRefused("budget_authorization_mismatch")

    def _save(self, session: RunSession, run: CloudRun, **changes: object) -> CloudRun:
        updated = replace(run, **changes)
        session.save(updated)
        return updated

    def _identity(self, model: ModelSnapshot, run: CloudRun) -> bool:
        return bool(
            model.model_id
            and model.owner == self.plan.owner
            and model.project_slug == self.plan.project_slug
            and model.model_slug == self.plan.model_slug
            and (run.model_id is None or model.model_id == run.model_id)
            and model.state in _PROVIDER_STATES
            and (run.provider_run_id is None or model.provider_run_id == run.provider_run_id)
        )

    def _advance(self, session: RunSession, run: CloudRun, grant: ConnectionGrant) -> CloudRun:
        assert self.transport is not None and self.budget is not None
        if run.phase == "prepared":
            # Read-only prerequisites before committing any mutation intent.
            try:
                available = self.transport.gpu_available(grant, self.plan.gpu_type)
                existing = self.transport.find_model(grant, self.plan)
            except Exception:
                return self._save(session, run, error_code="preflight_unavailable")
            if available is not True or existing is not None:
                # Definitive refusal before any dispatch can safely release the hold.
                run = self._save(
                    session,
                    run,
                    phase="terminal",
                    state="gpu_unavailable" if available is not True else "identity_mismatch",
                )
                self._release_undispatched(run)
                return run
            run = self._save(session, run, phase="dispatch_intent")
        if run.phase == "dispatch_intent":
            # No provider call is possible in this phase. A refusal or a crash after
            # ledger commit can be recovered from authoritative ledger state.
            entry = self.budget.read(run.spend)
            if entry.request != run.spend:
                raise CloudTrainingRefused("budget_authorization_mismatch")
            if entry.state == "reserved":
                entry = self.budget.dispatch(run.spend)
            else:
                # Recheck current limits/authority if dispatch committed before crash.
                entry = self.budget.reserve_and_authorize(run.spend)
            self._validate_entry(entry, run.spend, "dispatched")
            run = self._save(session, run, phase="create_intent", state="uncertain_create")
            try:
                model = self.transport.create_model(grant, self.plan)
            except Exception as error:
                return self._mutation_error(session, run, error)
            if not self._identity(model, run) or model.state != "untrained":
                return self._save(session, run, phase="terminal", state="identity_mismatch")
            run = self._save(
                session, run, phase="model_created", state="model_created", model_id=model.model_id
            )
        elif run.phase == "create_intent":
            try:
                model = self.transport.find_model(grant, self.plan)
            except Exception:
                return self._save(session, run, error_code="reconcile_unavailable")
            if model is None:
                return run  # Absence is not proof an uncertain create never happened.
            if not self._identity(model, run) or model.state != "untrained":
                return self._save(session, run, phase="terminal", state="identity_mismatch")
            run = self._save(
                session, run, phase="model_created", state="model_created", model_id=model.model_id
            )
        if run.phase == "model_created":
            try:
                available = self.transport.gpu_available(grant, self.plan.gpu_type)
            except Exception:
                return self._save(session, run, error_code="availability_unconfirmed")
            if available is not True:
                # No paid start intent exists. Keep a recoverable model, not a
                # terminal operation with a permanently stranded dispatched hold.
                return self._save(session, run, state="gpu_unavailable")
            # Trusted application clock/authority, not the frozen plan, decides
            # whether this first paid start still belongs to the reserved month.
            entry = self.budget.authorize_start(run.spend)
            self._validate_entry(entry, run.spend, "dispatched")
            run = self._save(
                session,
                run,
                phase="start_intent",
                state="uncertain_start",
                pricing_unconfirmed=True,
            )
            return self._start(session, run, grant)
        return self._reconcile(session, run, grant)

    def _start(self, session: RunSession, run: CloudRun, grant: ConnectionGrant) -> CloudRun:
        assert self.transport is not None
        body = {
            "modelId": run.model_id,
            "gpuType": self.plan.gpu_type,
            "captureDatasetVersion": True,
            "trainArgs": self.plan.train_arguments(),
        }
        try:
            try:
                receipt = self.transport.start(grant, body)
            except Exception as error:
                return self._mutation_error(session, run, error)
            # Save receipt before any subsequent remote read.
            if receipt.model_id != run.model_id or receipt.gpu_type != self.plan.gpu_type:
                return self._save(session, run, error_code="receipt_identity_mismatch")
            for amount in (
                receipt.price_per_hour_microusd,
                receipt.estimated_cost_microusd,
                receipt.balance_microusd,
                receipt.gpu_memory_gb,
            ):
                if type(amount) is not int or amount < 0:
                    return self._save(session, run, error_code="invalid_receipt")
            run = self._save(
                session,
                run,
                phase="started",
                state="accepted",
                receipt=receipt,
                pricing_unconfirmed=False,
                error_code=None,
            )
            # Accepted is distinct from active; readback occurs on the next advance.
            return run
        finally:
            # Also fail closed when receipt persistence raises. If the process
            # dies first, durable start_intent triggers the same pause on recovery.
            self._observe_estimate(run)

    def _mutation_error(self, session: RunSession, run: CloudRun, error: Exception) -> CloudRun:
        assert self.budget is not None
        status = error.status if isinstance(error, TransportError) else None
        if status == 402:
            # Persist terminal intent before the pause write; recovery retries the
            # idempotent ledger pause, never the provider mutation.
            run = self._save(
                session, run, phase="terminal", state="insufficient_credits", error_code="402"
            )
            self.budget.pause_insufficient_credits(run.spend)
            return run
        if status == 503:
            return self._save(session, run, phase="terminal", state="unavailable", error_code="503")
        # 409, 429, timeout, malformed response and all other uncertain mutations:
        # retain the intent and IDs. Only read-only reconciliation can advance it.
        return self._save(session, run, error_code="mutation_unconfirmed")

    def _reconcile(self, session: RunSession, run: CloudRun, grant: ConnectionGrant) -> CloudRun:
        assert self.transport is not None and self.budget is not None
        assert run.model_id is not None
        try:
            model = self.transport.read_model(grant, run.model_id, self.plan)
        except Exception:
            return self._save(session, run, error_code="reconcile_unavailable")
        if not self._identity(model, run):
            # Original-job billing was reconciled before this read. Quarantine
            # replacement artifacts without querying that charge a second time.
            return self._save(session, run, phase="terminal", state="identity_mismatch")
        if model.state == "untrained":
            return run  # Including a 409 cancelled-before-start: NEVER repeat start.
        newly_bound = run.provider_run_id is None and model.provider_run_id is not None
        run = self._save(
            session,
            run,
            phase="started",
            provider_state=model.state,
            provider_run_id=model.provider_run_id,
            error_code=None,
        )
        diff = argument_diff(self.plan.train_arguments(), model.resolved_arguments)
        mismatch = tuple(sorted(set(run.mismatched_keys) | diff.keys()))
        state: CloudState = {
            "pending": "accepted",
            "starting": "accepted",
            "running": "active",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }[model.state]
        # Capture version from model readback, never from the start response/URI.
        valid_dataset = (
            model.dataset_id == self.plan.dataset_id
            and type(model.dataset_version) is int
            and model.dataset_version >= 1
            and isinstance(model.dataset_content_hash, str)
            and model.dataset_content_hash == self.plan.expected_dataset_content_hash
            and (run.dataset_version is None or run.dataset_version == model.dataset_version)
            and (
                run.dataset_content_hash is None
                or run.dataset_content_hash == model.dataset_content_hash
            )
        )
        if not valid_dataset:
            mismatch = tuple(sorted(set(mismatch) | {"dataset_identity"}))
        # Only matching, allowlisted values are retained. A malicious response can
        # echo a key in an argument; never persist its value or exception text.
        resolved = tuple(
            (key, value) for key, value in self.plan.train_arguments().items() if key not in diff
        )
        run = self._save(
            session,
            run,
            state="args_mismatch" if mismatch else state,
            mismatched_keys=mismatch,
            resolved_arguments=resolved,
            dataset_version=model.dataset_version if valid_dataset else run.dataset_version,
            dataset_content_hash=(
                model.dataset_content_hash if valid_dataset else run.dataset_content_hash
            ),
        )
        # Only the first validated job binding has not been reconciled on entry.
        if newly_bound:
            run = self._settle(session, run, grant)
        if model.state == "completed" and not mismatch and run.checkpoint is None:
            run = self._checkpoint(session, run, grant)
        return run

    def _settle(self, session: RunSession, run: CloudRun, grant: ConnectionGrant) -> CloudRun:
        assert self.transport is not None and self.budget is not None and run.model_id is not None
        # Settlement and attribution are one ledger transaction. Recover the durable
        # result if a crash lost our run-store save and the provider is now unavailable.
        charge = self.budget.settled_charge(run.spend)
        if charge is None:
            charge = run.charge
        if charge is None:
            try:
                charge = (
                    self.transport.charge(grant, run.model_id, run.provider_run_id)
                    if run.provider_run_id
                    else None
                )
            except Exception:
                charge = None
        if charge is not None and (
            charge.model_id != run.model_id
            or charge.provider_run_id != run.provider_run_id
            or not charge.transaction_id
            or type(charge.actual_microusd) is not int
            or charge.actual_microusd < 0
        ):
            charge = None
        entry = self.budget.reconcile(run.spend, charge)
        if (
            entry.request != run.spend
            or (charge is not None and entry.actual != charge.actual_microusd)
            or (charge is None and entry.actual is not None)
        ):
            raise CloudTrainingRefused("budget_reconciliation_mismatch")
        return self._save(session, run, charge=charge)

    def _checkpoint(self, session: RunSession, run: CloudRun, grant: ConnectionGrant) -> CloudRun:
        assert (
            self.transport is not None and self.artifacts is not None and run.model_id is not None
        )
        try:
            content = bytearray()
            digest = hashlib.sha256()
            for chunk in self.transport.download_best(
                grant, run.model_id, self.maximum_checkpoint_bytes
            ):
                if (
                    not isinstance(chunk, bytes)
                    or len(content) + len(chunk) > self.maximum_checkpoint_bytes
                ):
                    return self._save(session, run, error_code="invalid_checkpoint")
                digest.update(chunk)
                content.extend(chunk)
            if not content:
                return self._save(session, run, error_code="invalid_checkpoint")
            sha256 = digest.hexdigest()
            artifact_ref = self.artifacts.store(sha256, bytes(content))
        except Exception:
            return self._save(session, run, error_code="checkpoint_unavailable")
        return self._save(
            session, run, checkpoint=CloudCheckpoint(sha256, len(content), artifact_ref)
        )
