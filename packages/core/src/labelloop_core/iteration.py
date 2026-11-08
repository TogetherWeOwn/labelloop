"""Pure iteration projection over durable job/artifact receipts, not a scheduler.

The trusted store CAS-commits each returned projection together with job intent.
No function here performs I/O. An intent recovered after a crash is reconcile-only;
real workers/adapters remain disabled pending the service integrations.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Literal

from .budget import BudgetGate, identity
from .review import CorrectionLoop
from .training import require_digest

Stage = Literal[
    "freeze",
    "propose_discover",
    "review",
    "validate",
    "adjudicate_audit",
    "release",
    "compile",
    "sync",
    "train",
    "evaluate",
    "promote_retain",
    "select_next",
]
Status = Literal["running", "stopped", "paused", "cancelled"]
StopReason = Literal[
    "sufficient_scoped_quality",
    "no_informative_data",
    "plateau",
    "insufficient_evidence",
    "oscillation",
    "drift_regression",
    "round_limit",
    "insufficient_credits",
]
SHARED: tuple[Stage, ...] = (
    "freeze",
    "propose_discover",
    "review",
    "validate",
    "adjudicate_audit",
    "release",
)
PER_RECIPE: tuple[Stage, ...] = ("compile", "sync", "train", "evaluate", "promote_retain")
STOP_REASONS: frozenset[StopReason] = frozenset(
    {
        "sufficient_scoped_quality",
        "no_informative_data",
        "plateau",
        "insufficient_evidence",
        "oscillation",
        "drift_regression",
        "round_limit",
        "insufficient_credits",
    }
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Immutable store ID plus content hash; never a signed URL or provider secret."""

    artifact_id: str
    sha256: str

    def __post_init__(self) -> None:
        identity(self.artifact_id)
        require_digest(self.sha256)


@dataclass(frozen=True, slots=True)
class SourcePin:
    asset_id: str
    pixels: EvidenceRef
    annotation: EvidenceRef

    def __post_init__(self) -> None:
        identity(self.asset_id)


@dataclass(frozen=True, slots=True)
class RecipePin:
    recipe_id: str
    definition: EvidenceRef
    training_profile: EvidenceRef
    incumbent: EvidenceRef | None = None

    def __post_init__(self) -> None:
        identity(self.recipe_id)


@dataclass(frozen=True, slots=True)
class IterationManifest:
    project_id: str
    source_batch_id: str
    access_scope: EvidenceRef
    sources: tuple[SourcePin, ...]
    ontology: EvidenceRef
    references: EvidenceRef
    teacher: EvidenceRef
    recipes: tuple[RecipePin, ...]
    split_assignment: EvidenceRef
    review_pipeline: EvidenceRef
    # Includes provider/model config, prompt/schema and decoder/encoder versions.
    execution_environment: EvidenceRef
    acceptance_policy: EvidenceRef
    # Preregistered metric/classes/slices/tolerances; qualification owns validation.
    evaluation_protocol: EvidenceRef
    evaluation_release: EvidenceRef
    evidence: EvidenceRef
    policy_renewal: EvidenceRef
    maximum_training_rounds: int
    maximum_correction_passes: int = 2
    backend: Literal["local_ultralytics", "ultralytics_platform"] = "local_ultralytics"
    paid_review: bool = False

    def __post_init__(self) -> None:
        identity(self.project_id)
        identity(self.source_batch_id)
        if type(self.sources) is not tuple or not self.sources:
            raise ValueError("source pins must be a nonempty immutable tuple")
        if type(self.recipes) is not tuple or not self.recipes:
            raise ValueError("recipe pins must be a nonempty immutable tuple")
        for keys in (
            tuple(p.asset_id for p in self.sources),
            tuple(p.recipe_id for p in self.recipes),
        ):
            if keys != tuple(sorted(set(keys))):
                raise ValueError("source/recipe IDs must be unique and sorted")
        if type(self.maximum_training_rounds) is not int or self.maximum_training_rounds < 1:
            raise ValueError("training rounds require a positive ceiling")
        if type(
            self.maximum_correction_passes
        ) is not int or self.maximum_correction_passes not in {1, 2}:
            raise ValueError("correction passes must reuse the existing one/two-pass bound")
        if self.backend not in {"local_ultralytics", "ultralytics_platform"}:
            raise ValueError("unknown training backend")
        if type(self.paid_review) is not bool:
            raise ValueError("paid review must be application policy")

    @property
    def digest(self) -> str:
        return _digest(asdict(self))

    @property
    def round_evidence(self) -> str:
        # Only new evidence, changed recipes or explicit policy renewal justify a round.
        return _digest(
            {
                "sources": [asdict(p) for p in self.sources],
                "evidence": asdict(self.evidence),
                "recipes": [
                    (p.recipe_id, asdict(p.definition), asdict(p.training_profile))
                    for p in self.recipes
                ],
                "renewal": asdict(self.policy_renewal),
            }
        )


@dataclass(frozen=True, slots=True)
class StageKey:
    stage: Stage
    recipe_id: str = ""

    def __post_init__(self) -> None:
        if self.stage not in (*SHARED, *PER_RECIPE, "select_next"):
            raise ValueError("unknown iteration stage")
        if (self.stage in PER_RECIPE) != bool(self.recipe_id):
            raise ValueError("only per-recipe stages have recipe identity")


def stage_keys(manifest: IterationManifest) -> tuple[StageKey, ...]:
    return (
        *(StageKey(s) for s in SHARED),
        *(StageKey(s, p.recipe_id) for p in manifest.recipes for s in PER_RECIPE),
        StageKey("select_next"),
    )


def prerequisites(manifest: IterationManifest, key: StageKey) -> tuple[StageKey, ...]:
    if key.stage in SHARED:
        i = SHARED.index(key.stage)
        return () if i == 0 else (StageKey(SHARED[i - 1]),)
    if key.stage in PER_RECIPE:
        i = PER_RECIPE.index(key.stage)
        return (StageKey("release"),) if i == 0 else (StageKey(PER_RECIPE[i - 1], key.recipe_id),)
    return tuple(StageKey("promote_retain", p.recipe_id) for p in manifest.recipes)


@dataclass(frozen=True, slots=True)
class StageReceipt:
    """Trusted validator receipt, not a model response or an existence-only cache hit.

    The store checks actual artifact bytes, identity, lineage and stage-specific
    domain invariants before recording this receipt. Resume revalidates the exact
    ordered receipt set; no public API may deserialize this as authorization.
    """

    operation_id: str
    input_sha256: str
    artifact: EvidenceRef
    validator: EvidenceRef
    outcome: Literal["complete", "failed"] = "complete"
    correction_loops: tuple[tuple[str, CorrectionLoop], ...] = ()
    decision: Literal["promote", "retain"] | None = None
    selected_model: EvidenceRef | None = None

    def __post_init__(self) -> None:
        identity(self.operation_id)
        require_digest(self.input_sha256)
        if self.outcome not in {"complete", "failed"}:
            raise ValueError("unknown stage outcome")
        if type(self.correction_loops) is not tuple:
            raise ValueError("correction history must be immutable")
        if len({asset for asset, _ in self.correction_loops}) != len(self.correction_loops):
            raise ValueError("one correction loop per source")


@dataclass(frozen=True, slots=True)
class StageRecord:
    key: StageKey
    operation_id: str
    input_sha256: str
    receipt: StageReceipt | None = None
    # Preserve validation scope even if its result arrives after invalidation.
    source_ids: tuple[str, ...] = ()
    maximum_correction_passes: int = 2


@dataclass(frozen=True, slots=True)
class BatchPolicy:
    source_batch_id: str
    maximum_training_rounds: int
    renewal: EvidenceRef


@dataclass(frozen=True, slots=True)
class RoundStamp:
    iteration_id: str
    source_batch_id: str
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class IterationState:
    iteration_id: str
    manifest: IterationManifest
    generation: int = 0
    records: tuple[StageRecord, ...] = ()
    rounds: tuple[RoundStamp, ...] = ()
    correction_loops: tuple[tuple[str, CorrectionLoop], ...] = ()
    status: Status = "running"
    reason: str = ""
    # Invalidation preserves old intents/receipts for lineage and reconciliation.
    retired: tuple[StageRecord, ...] = ()
    batch_policies: tuple[BatchPolicy, ...] = ()
    # The original round inputs survive manifest invalidation and subsequent stops.
    started_round_evidence: str = ""
    admitted_iteration_ids: tuple[str, ...] = ()
    restart_required: bool = False

    def __post_init__(self) -> None:
        identity(self.iteration_id)
        if not self.batch_policies:
            if self.rounds or self.retired:
                raise ValueError("retained round history requires per-batch policy history")
            object.__setattr__(
                self,
                "batch_policies",
                (
                    BatchPolicy(
                        self.manifest.source_batch_id,
                        self.manifest.maximum_training_rounds,
                        self.manifest.policy_renewal,
                    ),
                ),
            )
        if not self.started_round_evidence:
            object.__setattr__(self, "started_round_evidence", self.manifest.round_evidence)
        require_digest(self.started_round_evidence)
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("invalid generation")
        if self.status not in {"running", "stopped", "paused", "cancelled"}:
            raise ValueError("invalid iteration status")
        if any(
            type(items) is not tuple
            for items in (
                self.records,
                self.rounds,
                self.correction_loops,
                self.retired,
                self.batch_policies,
                self.admitted_iteration_ids,
            )
        ):
            raise ValueError("iteration history requires immutable tuples")
        if not self.admitted_iteration_ids:
            if self.rounds or self.retired:
                raise ValueError("retained history requires complete iteration identity history")
            object.__setattr__(self, "admitted_iteration_ids", (self.iteration_id,))
        if (
            len(set(self.admitted_iteration_ids)) != len(self.admitted_iteration_ids)
            or self.iteration_id not in self.admitted_iteration_ids
            or any(r.iteration_id not in self.admitted_iteration_ids for r in self.rounds)
        ):
            raise ValueError("iteration identity history must be complete and unique")
        for admitted_id in self.admitted_iteration_ids:
            identity(admitted_id)
        if type(self.restart_required) is not bool:
            raise ValueError("invalidation state must be application policy")
        if len(dict(self.correction_loops)) != len(self.correction_loops):
            raise ValueError("duplicate correction history")
        all_records = (*self.records, *self.retired)
        if len({r.operation_id for r in all_records}) != len(all_records):
            raise ValueError("operation identities cannot repeat across generations")
        for record in all_records:
            require_digest(record.operation_id)
            require_digest(record.input_sha256)
            if record.receipt is not None and (
                record.receipt.operation_id != record.operation_id
                or record.receipt.input_sha256 != record.input_sha256
            ):
                raise ValueError("stored receipt does not match intent")
        if len({r.key for r in self.records}) != len(self.records):
            raise ValueError("duplicate stage record")
        if len({r.iteration_id for r in self.rounds}) != len(self.rounds):
            raise ValueError("duplicate training round")
        if any(r.key not in stage_keys(self.manifest) for r in self.records):
            raise ValueError("stage outside manifest")

    def record(self, key: StageKey) -> StageRecord | None:
        return next((r for r in self.records if r.key == key), None)

    def input_digest(self, key: StageKey) -> str:
        if key not in stage_keys(self.manifest):
            raise ValueError("stage outside manifest")
        if key.stage == "promote_retain":
            failures = self.recipe_failures(key.recipe_id)
            if failures:
                return _digest(
                    {
                        "manifest": self.manifest.digest,
                        "stage": asdict(key),
                        "retain_after_failure": [
                            asdict(r.receipt) for r in failures if r.receipt is not None
                        ],
                    }
                )
        parents = []
        for parent in prerequisites(self.manifest, key):
            record = self.record(parent)
            if record is None or record.receipt is None:
                raise ValueError("prerequisite incomplete")
            # Failed recipe chains may only advance to an explicit retain decision.
            if record.receipt.outcome == "failed":
                raise ValueError("prerequisite failed")
            parents.append(asdict(record.receipt))
        return _digest({"manifest": self.manifest.digest, "stage": asdict(key), "parents": parents})

    def recipe_failures(self, recipe_id: str) -> tuple[StageRecord, ...]:
        return tuple(
            r
            for r in self.records
            if r.key.recipe_id == recipe_id
            and r.key.stage != "promote_retain"
            and r.receipt is not None
            and r.receipt.outcome == "failed"
        )

    def ready(self) -> tuple[StageKey, ...]:
        if self.status != "running" or any(r.receipt is None for r in self.retired):
            return ()
        ready = []
        for key in stage_keys(self.manifest):
            if self.record(key) is not None:
                continue
            try:
                self.input_digest(key)
            except ValueError:
                continue
            ready.append(key)
        return tuple(ready)


@dataclass(frozen=True, slots=True)
class DurableBudgetAuthorization:
    """Produced by the future trusted store after its ledger/intent transaction.

    A pure BudgetGate reservation or caller-supplied 'approved' flag is insufficient.
    This receipt is not itself an authentication or durable persistence mechanism.
    """

    commit: EvidenceRef
    ledger: BudgetGate
    operation_id: str

    def validate(self, state: IterationState, operation_id: str, inputs: str) -> None:
        entry = self.ledger.entry(operation_id)
        request = entry.request
        if (
            self.operation_id != operation_id
            or self.ledger.project_id != state.manifest.project_id
            or request.iteration_id != state.iteration_id
            or request.configuration_sha256 != inputs
            or entry.state != "dispatched"
            or self.ledger.pause_reason is not None
        ):
            raise ValueError("durable budget authorization does not match stage intent")
        # Reuse all-limit and manual-click enforcement without dispatching a second time.
        self.ledger._check_limits(request, extra=0)
        if not self.ledger.policy.autonomous_spend and entry.approval is None:
            raise ValueError("authenticated approval required")


def operation_id(state: IterationState, key: StageKey) -> str:
    return _digest((state.iteration_id, state.generation, asdict(key), state.input_digest(key)))


def begin_stage(
    state: IterationState, key: StageKey, *, budget: DurableBudgetAuthorization | None = None
) -> IterationState:
    """Authorize a prepared intent for atomic ledger/job commit before worker I/O.

    The trusted store preflights with prepare_stage before budget dispatch, then
    commits this result and that ledger together. Existing intents are reconcile-only.
    """
    prepared = prepare_stage(state, key)
    record = prepared.record(key)
    if record is None or state.record(key) is not None:
        return prepared
    paid = (key.stage == "review" and state.manifest.paid_review) or (
        key.stage in {"sync", "train"} and state.manifest.backend == "ultralytics_platform"
    )
    if paid:
        if budget is None:
            raise ValueError("durable budget authorization required")
        budget.validate(state, record.operation_id, record.input_sha256)
    return prepared


def prepare_stage(state: IterationState, key: StageKey) -> IterationState:
    """Pure eligibility preview, never authorization to persist or dispatch a job.

    A refusal prepares no intent. The store must not dispatch its ledger entry in
    that case, and must discard the ledger transition if the atomic CAS fails.
    """
    if state.status != "running":
        raise ValueError("iteration is not running")
    if state.record(key) is not None:
        return state
    if key not in state.ready():
        raise ValueError("stage is not ready")
    inputs = state.input_digest(key)
    op = operation_id(state, key)
    rounds = state.rounds
    if key.stage == "train" and not any(r.iteration_id == state.iteration_id for r in rounds):
        count = sum(r.source_batch_id == state.manifest.source_batch_id for r in rounds)
        if count >= state.manifest.maximum_training_rounds:
            return replace(state, status="paused", reason="round_limit", restart_required=True)
        if any(
            r.source_batch_id == state.manifest.source_batch_id
            and r.evidence_sha256 == state.manifest.round_evidence
            for r in rounds
        ):
            raise ValueError("training round requires new evidence, recipe or policy renewal")
        rounds = (
            *rounds,
            RoundStamp(
                state.iteration_id,
                state.manifest.source_batch_id,
                state.manifest.round_evidence,
            ),
        )
    record = StageRecord(
        key,
        op,
        inputs,
        source_ids=tuple(p.asset_id for p in state.manifest.sources)
        if key.stage == "validate"
        else (),
        maximum_correction_passes=state.manifest.maximum_correction_passes,
    )
    return replace(state, rounds=rounds, records=(*state.records, record))


def finish_stage(state: IterationState, key: StageKey, receipt: StageReceipt) -> IterationState:
    record = state.record(key)
    if record is None:
        raise ValueError("no committed stage intent")
    if receipt.operation_id != record.operation_id or receipt.input_sha256 != record.input_sha256:
        raise ValueError("receipt does not match committed intent")
    if record.receipt is not None:
        if record.receipt != receipt:
            raise ValueError("conflicting stage result")
        return state
    if state.input_digest(key) != record.input_sha256:
        raise ValueError("stale stage inputs")
    loops = _merge_corrections(state, record, receipt)
    if receipt.decision is not None and key.stage != "promote_retain":
        raise ValueError("only recipe decisions select models")
    if key.stage == "promote_retain" and receipt.outcome == "failed":
        if receipt.decision is not None or receipt.selected_model is not None:
            raise ValueError("a failed registry transaction cannot select a model")
    elif key.stage == "promote_retain":
        incumbent = next(
            p.incumbent for p in state.manifest.recipes if p.recipe_id == key.recipe_id
        )
        if receipt.outcome != "complete" or receipt.decision not in {"promote", "retain"}:
            raise ValueError("every recipe requires an explicit promote or retain decision")
        if state.recipe_failures(key.recipe_id) and receipt.decision != "retain":
            raise ValueError("a failed recipe can only retain its incumbent")
        if receipt.decision == "retain" and receipt.selected_model != incumbent:
            raise ValueError("retain must preserve this recipe's pinned incumbent")
        if receipt.decision == "promote" and (
            receipt.selected_model is None or receipt.selected_model == incumbent
        ):
            raise ValueError("promotion requires a separately qualified candidate")
    changed = replace(record, receipt=receipt)
    return replace(
        state,
        records=tuple(changed if r.key == key else r for r in state.records),
        correction_loops=loops,
    )


def _merge_corrections(
    state: IterationState,
    record: StageRecord,
    receipt: StageReceipt,
) -> tuple[tuple[str, CorrectionLoop], ...]:
    loops = dict(state.correction_loops)
    if receipt.correction_loops and record.key.stage != "validate":
        raise ValueError("only validation records correction loops")
    for asset, loop in receipt.correction_loops:
        if asset not in record.source_ids:
            raise ValueError("correction loop outside source batch")
        previous = loops.get(asset)
        if loop.maximum_passes != record.maximum_correction_passes:
            raise ValueError("correction policy mismatch")
        if previous is not None and (
            loop.passes < previous.passes
            or loop.object_hashes[: len(previous.object_hashes)] != previous.object_hashes
        ):
            raise ValueError("correction history cannot reset on resume")
        loops[asset] = loop
    if record.key.stage == "validate" and receipt.outcome == "complete":
        if set(dict(receipt.correction_loops)) != set(record.source_ids):
            raise ValueError("validation must account for every source correction loop")
        if any(loop.status == "running" for _, loop in receipt.correction_loops):
            raise ValueError("settle bounded correction before adjudication/audit")
    return tuple(sorted(loops.items()))


def recover_stage(state: IterationState, key: StageKey) -> Literal["reuse", "reconcile"]:
    record = state.record(key)
    if record is None:
        raise ValueError("unknown stage intent")
    return "reuse" if record.receipt is not None else "reconcile"


def resume(state: IterationState, validated: tuple[StageReceipt, ...]) -> IterationState:
    """Require fresh artifact validation of every completed stage before reuse."""
    expected = tuple(r.receipt for r in state.records if r.receipt is not None)
    if validated != expected:
        raise ValueError("resume requires exact artifact/validator/lineage receipts")
    for record in state.records:
        if record.input_sha256 != state.input_digest(record.key):
            raise ValueError("stale stage cache")
    return state


@dataclass(frozen=True, slots=True)
class PauseClearance:
    """Trusted credit-clearance evidence, never public/model-supplied permission.

    The authenticated store verifies current credit/ledger policy before issuing
    this binding. It is not budget dispatch authorization or a retry instruction.
    """

    iteration_id: str
    generation: int
    manifest_sha256: str
    pause_reason: str
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        identity(self.iteration_id)
        require_digest(self.manifest_sha256)
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("invalid clearance generation")


def continue_iteration(
    state: IterationState,
    validated: tuple[StageReceipt, ...],
    *,
    clearance: PauseClearance,
) -> IterationState:
    """Continue only a cleared credit pause with unchanged, settled inputs.

    Other stop gates require a fresh iteration with new evidence/renewal. Failed
    and uncertain attempts never become retryable through this transition.
    """
    if state.status != "paused" or not state.reason.startswith("insufficient_credits:"):
        raise ValueError("continuation requires an explicit credit pause")
    if (
        clearance.iteration_id != state.iteration_id
        or clearance.generation != state.generation
        or clearance.manifest_sha256 != state.manifest.digest
        or clearance.pause_reason != state.reason
    ):
        raise ValueError("credit clearance does not match paused iteration")
    if state.restart_required:
        raise ValueError("invalidated inputs or prior stop gates require a fresh iteration")
    if any(r.receipt is None for r in state.retired) or any(
        r.receipt is None or r.receipt.outcome == "failed" for r in state.records
    ):
        raise ValueError("pending or failed intents cannot be continued")
    resume(state, validated)
    return replace(state, status="running", reason="")


def _policy_transition(
    state: IterationState,
    manifest: IterationManifest,
) -> tuple[BatchPolicy, ...]:
    history = tuple(
        p for p in state.batch_policies if p.source_batch_id == manifest.source_batch_id
    )
    policy = BatchPolicy(
        manifest.source_batch_id,
        manifest.maximum_training_rounds,
        manifest.policy_renewal,
    )
    if (
        history
        and policy.maximum_training_rounds > history[-1].maximum_training_rounds
        and (any(p.renewal == policy.renewal for p in history))
    ):
        raise ValueError("extending a round ceiling requires explicit policy renewal")
    sources = {p.asset_id for p in manifest.sources}
    if any(
        asset in sources and loop.passes > manifest.maximum_correction_passes
        for asset, loop in state.correction_loops
    ):
        raise ValueError("new ceiling is below consumed correction passes")
    if manifest.maximum_correction_passes < state.manifest.maximum_correction_passes and any(
        r.key.stage == "validate" and r.receipt is None for r in (*state.records, *state.retired)
    ):
        raise ValueError("cannot tighten correction ceiling with pending validation")
    return (
        (*state.batch_policies, policy)
        if not history or policy != history[-1]
        else state.batch_policies
    )


def invalidate(state: IterationState, manifest: IterationManifest) -> IterationState:
    """Conservatively invalidate all descendants, retaining history and bounds."""
    if manifest.project_id != state.manifest.project_id:
        raise ValueError("cannot move an iteration across projects")
    if manifest == state.manifest:
        return state
    policies = _policy_transition(state, manifest)
    if state.status == "cancelled":
        raise ValueError("cancelled iterations cannot restart")
    return replace(
        state,
        manifest=manifest,
        generation=state.generation + 1,
        records=(),
        retired=(*state.retired, *state.records),
        status="paused",
        reason="inputs_changed",
        restart_required=True,
        batch_policies=policies,
    )


def reconcile_retired(state: IterationState, receipt: StageReceipt) -> IterationState:
    """Record readback of an invalidated intent without making its result reusable.

    A confirmed absence/cancellation is a failed receipt. Unknown is not absence:
    leave the intent unresolved and hold. There is deliberately no retry transition.
    """
    record = next((r for r in state.retired if r.operation_id == receipt.operation_id), None)
    if record is None or record.input_sha256 != receipt.input_sha256:
        raise ValueError("unknown retired intent")
    if record.receipt is not None:
        if record.receipt != receipt:
            raise ValueError("conflicting reconciliation")
        return state
    loops = _merge_corrections(state, record, receipt)
    return replace(
        state,
        correction_loops=loops,
        retired=tuple(
            replace(r, receipt=receipt) if r.operation_id == receipt.operation_id else r
            for r in state.retired
        ),
    )


def cancel(state: IterationState) -> IterationState:
    return replace(state, status="cancelled", reason="cancelled")


def stop(state: IterationState, reason: StopReason, evidence: EvidenceRef) -> IterationState:
    """Trusted policy evaluation supplies scoped evidence, never LLM stop commands.

    Quality is deliberately only a stop disposition, NOT independent verification.
    Qualification and the registry remain the authorities for that separate claim.
    """
    if reason not in STOP_REASONS:
        raise ValueError("unknown stop condition")
    if state.status == "cancelled":
        raise ValueError("cancelled iteration is terminal")
    status: Status = (
        "stopped"
        if reason
        in {
            "sufficient_scoped_quality",
            "no_informative_data",
            "plateau",
        }
        else "paused"
    )
    return replace(
        state,
        status=status,
        reason=f"{reason}:{evidence.artifact_id}:{evidence.sha256}",
        restart_required=state.restart_required or reason != "insufficient_credits",
    )


def next_iteration(
    state: IterationState, iteration_id: str, manifest: IterationManifest
) -> IterationState:
    selection = state.record(StageKey("select_next"))
    if state.status == "running" and (
        selection is None or selection.receipt is None or selection.receipt.outcome != "complete"
    ):
        raise ValueError("finish or pause the current iteration first")
    if any(r.receipt is None for r in (*state.records, *state.retired)):
        raise ValueError("uncertain intents require reconciliation before a new iteration")
    if (
        manifest.project_id != state.manifest.project_id
        or iteration_id in state.admitted_iteration_ids
    ):
        raise ValueError("new iteration requires the same project and a fresh ID")
    if (
        manifest.source_batch_id == state.manifest.source_batch_id
        and manifest.round_evidence == state.started_round_evidence
    ) or any(
        r.source_batch_id == manifest.source_batch_id
        and r.evidence_sha256 == manifest.round_evidence
        for r in state.rounds
    ):
        raise ValueError("new round requires new evidence, recipe or explicit policy renewal")
    policies = _policy_transition(state, manifest)
    return IterationState(
        iteration_id,
        manifest,
        generation=state.generation + 1,
        rounds=state.rounds,
        correction_loops=state.correction_loops,
        retired=(*state.retired, *state.records),
        batch_policies=policies,
        admitted_iteration_ids=(*state.admitted_iteration_ids, iteration_id),
    )
