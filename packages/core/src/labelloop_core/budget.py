"""Pure USD ledger transitions. A reservation is not permission to call a provider.

The durable adapter must serialize transitions per project, authenticate approvals,
commit dispatch intent before I/O, and never redispatch an uncertain operation.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import date
from typing import Literal

OperationState = Literal[
    "reserved", "dispatched", "unconfirmed", "settled", "cancelled", "insufficient_credits"
]


class BudgetRefused(ValueError):
    """Stable refusal code for a caller, never a retry instruction."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def nonnegative(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("amounts and token counts require nonnegative integers")


def identity(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("a bounded nonblank identity is required")


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        nonnegative(self.input_tokens)
        nonnegative(self.output_tokens)


@dataclass(frozen=True)
class TokenPrices:
    """Integer micro-USD per million tokens; no floating-point money or live prices.

    This two-rate contract covers uncached input and output only. A provider with
    cache, reasoning or other separately priced usage needs an explicit adapter.
    """

    version: str
    input_per_million: int
    output_per_million: int

    def __post_init__(self) -> None:
        identity(self.version)
        nonnegative(self.input_per_million)
        nonnegative(self.output_per_million)

    def cost(self, usage: TokenUsage) -> int:
        numerator = (
            usage.input_tokens * self.input_per_million
            + usage.output_tokens * self.output_per_million
        )
        return (numerator + 999_999) // 1_000_000


@dataclass(frozen=True)
class BudgetPolicy:
    """Unset limits disable paid work. Currency is always USD, in microdollars."""

    monthly_limit: int | None = None
    iteration_limit: int | None = None
    operation_limit: int | None = None
    autonomous_spend: bool = False

    def __post_init__(self) -> None:
        for limit in (self.monthly_limit, self.iteration_limit, self.operation_limit):
            if limit is not None:
                nonnegative(limit)
        if type(self.autonomous_spend) is not bool:
            raise ValueError("autonomous_spend requires a boolean")


@dataclass(frozen=True)
class SpendRequest:
    """Trusted application dry-run inputs, not fields copied from model output.

    configuration_sha256 pins the endpoint/model, source batch, maximum token
    counts and other operation-specific inputs displayed in the dry-run summary.
    month is the UTC reservation month; reconciliation never rebuckets history.
    """

    operation_id: str
    project_id: str
    iteration_id: str
    month: str
    estimate: int
    price_version: str
    configuration_sha256: str

    def __post_init__(self) -> None:
        for value in (
            self.operation_id,
            self.project_id,
            self.iteration_id,
            self.price_version,
        ):
            identity(value)
        if not isinstance(self.month, str) or len(self.month) != 7:
            raise ValueError("month must be YYYY-MM")
        try:
            parsed = date.fromisoformat(self.month + "-01")
        except ValueError as error:
            raise ValueError("month must be YYYY-MM") from error
        if parsed.strftime("%Y-%m") != self.month:
            raise ValueError("month must be YYYY-MM")
        nonnegative(self.estimate)
        if (
            not isinstance(self.configuration_sha256, str)
            or len(self.configuration_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.configuration_sha256)
        ):
            raise ValueError("configuration requires a lowercase SHA-256")

    @property
    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class SpendApproval:
    """An authenticated click, supplied only by a future trusted auth adapter.

    This value validates the binding, not the actor's authentication/authorization.
    It is not accepted from a worker or public request body.
    """

    actor_id: str
    request_digest: str

    def __post_init__(self) -> None:
        identity(self.actor_id)


@dataclass(frozen=True)
class LedgerEntry:
    request: SpendRequest
    approval: SpendApproval | None = None
    state: OperationState = "reserved"
    actual: int | None = None
    reserved_autonomously: bool = False
    exposure_floor: int = 0

    def __post_init__(self) -> None:
        nonnegative(self.exposure_floor)
        if self.state == "cancelled" and self.exposure_floor:
            raise ValueError("cancelled entries cannot retain child exposure")
        if type(self.reserved_autonomously) is not bool:
            raise ValueError("reservation authorization requires a boolean")
        if not self.reserved_autonomously and self.approval is None:
            raise ValueError("manual reservation requires authenticated click evidence")
        if self.state not in {
            "reserved",
            "dispatched",
            "unconfirmed",
            "settled",
            "cancelled",
            "insufficient_credits",
        }:
            raise ValueError("unknown operation state")
        if self.actual is not None:
            nonnegative(self.actual)
            if self.actual < self.exposure_floor:
                raise ValueError("confirmed total cannot be below known child exposure")
        if (self.state == "settled") != (self.actual is not None):
            raise ValueError("only settled entries have confirmed actuals")
        if self.approval is not None and self.approval.request_digest != self.request.digest:
            raise ValueError("approval does not describe this dry run")

    @property
    def exposure(self) -> int:
        if self.state == "cancelled":
            return 0
        return (
            self.actual
            if self.actual is not None
            else max(self.request.estimate, self.exposure_floor)
        )


@dataclass(frozen=True)
class AcknowledgedPause:
    """Exact terminal ledger snapshot explicitly acknowledged by a trusted admin port.

    Not an approval or a dispatch grant. The durable adapter binds this evidence
    to an immutable actor event; callers without it retain fail-closed semantics.
    """

    project_id: str
    pause_reason: Literal["insufficient_credits", "estimate_overrun"]
    entries: tuple[LedgerEntry, ...]

    def __post_init__(self) -> None:
        identity(self.project_id)
        if self.pause_reason not in {"insufficient_credits", "estimate_overrun"}:
            raise ValueError("unsupported acknowledged pause")
        if type(self.entries) is not tuple or not self.entries:
            raise ValueError("acknowledgement requires an immutable ledger snapshot")
        if any(
            not isinstance(entry, LedgerEntry)
            or entry.request.project_id != self.project_id
            or entry.state not in {"settled", "cancelled", "insufficient_credits"}
            for entry in self.entries
        ):
            raise ValueError("acknowledgement requires terminal project entries")
        if len({entry.request.operation_id for entry in self.entries}) != len(self.entries):
            raise ValueError("acknowledgement operation IDs must be unique")


@dataclass(frozen=True)
class BudgetGate:
    project_id: str
    policy: BudgetPolicy = BudgetPolicy()
    entries: tuple[LedgerEntry, ...] = ()
    pause_reason: Literal["insufficient_credits", "estimate_overrun"] | None = None
    acknowledged_pause: AcknowledgedPause | None = None

    def __post_init__(self) -> None:
        identity(self.project_id)
        if type(self.entries) is not tuple:
            raise ValueError("ledger entries require an immutable tuple")
        if self.pause_reason not in {None, "insufficient_credits", "estimate_overrun"}:
            raise ValueError("unknown paid-stage pause")
        if any(entry.request.project_id != self.project_id for entry in self.entries):
            raise ValueError("ledger cannot cross project boundaries")
        if len({entry.request.operation_id for entry in self.entries}) != len(self.entries):
            raise ValueError("operation IDs must be unique within the project")
        acknowledged = self.acknowledged_pause
        if acknowledged is not None:
            if not isinstance(acknowledged, AcknowledgedPause):
                raise ValueError("typed acknowledgement evidence required")
            if acknowledged.project_id != self.project_id:
                raise ValueError("acknowledgement cannot cross project boundaries")
            for prior in acknowledged.entries:
                current = self.entry(prior.request.operation_id)
                if (
                    prior.request != current.request
                    or prior.approval != current.approval
                    or prior.reserved_autonomously != current.reserved_autonomously
                ):
                    raise ValueError("acknowledgement substituted immutable ledger identity")
        unacknowledged = tuple(entry for entry in self.entries if not self._acknowledged(entry))
        if (
            any(entry.state == "insufficient_credits" for entry in unacknowledged)
            and not self.pause_reason
        ):
            raise ValueError("a 402 must pause paid stages")
        if (
            any(
                (entry.actual is not None and entry.actual > entry.request.estimate)
                or entry.exposure_floor > entry.request.estimate
                for entry in unacknowledged
            )
            and not self.pause_reason
        ):
            raise ValueError("an estimate overrun must pause paid stages")

    def _acknowledged(self, entry: LedgerEntry) -> bool:
        return self.acknowledged_pause is not None and entry in self.acknowledged_pause.entries

    def acknowledge_pause(self) -> "BudgetGate":
        """Clear only the observed pause, retaining every entry and its exposure."""
        if self.pause_reason is None:
            raise BudgetRefused("paid_stages_not_paused")
        if not self.entries or any(
            entry.state in {"reserved", "dispatched", "unconfirmed"} for entry in self.entries
        ):
            raise BudgetRefused("resume_in_flight")
        return replace(
            self,
            pause_reason=None,
            acknowledged_pause=AcknowledgedPause(self.project_id, self.pause_reason, self.entries),
        )

    def entry(self, operation_id: str) -> LedgerEntry:
        for entry in self.entries:
            if entry.request.operation_id == operation_id:
                return entry
        raise ValueError("unknown operation")

    def monthly_exposure(self, month: str) -> int:
        return sum(entry.exposure for entry in self.entries if entry.request.month == month)

    def iteration_exposure(self, iteration_id: str) -> int:
        # Iterations can span a month boundary; their ceiling does not reset then.
        return sum(
            entry.exposure for entry in self.entries if entry.request.iteration_id == iteration_id
        )

    def reserve(
        self, request: SpendRequest, *, approval: SpendApproval | None = None
    ) -> "BudgetGate":
        if request.project_id != self.project_id:
            raise BudgetRefused("project_mismatch")
        existing = next(
            (entry for entry in self.entries if entry.request.operation_id == request.operation_id),
            None,
        )
        if existing is not None:
            if existing.request != request or existing.approval != approval:
                raise BudgetRefused("operation_conflict")
            return self
        self._require_active()
        self._check_limits(request, extra=request.estimate)
        if not self.policy.autonomous_spend and approval is None:
            raise BudgetRefused("approval_required")
        entry = LedgerEntry(request, approval, reserved_autonomously=self.policy.autonomous_spend)
        return replace(self, entries=(*self.entries, entry))

    def dispatch(self, operation_id: str) -> "BudgetGate":
        self._require_active()
        entry = self.entry(operation_id)
        if entry.state != "reserved":
            raise BudgetRefused("operation_not_dispatchable")
        self._check_limits(entry.request, extra=0)
        if not self.policy.autonomous_spend and entry.approval is None:
            raise BudgetRefused("approval_required")
        return self._replace(replace(entry, state="dispatched"))

    def _check_limits(self, request: SpendRequest, *, extra: int) -> None:
        policy = self.policy
        if (
            policy.monthly_limit is None
            or policy.iteration_limit is None
            or policy.operation_limit is None
        ):
            raise BudgetRefused("budget_not_configured")
        if (
            request.estimate > policy.operation_limit
            or self.monthly_exposure(request.month) + extra > policy.monthly_limit
            or self.iteration_exposure(request.iteration_id) + extra > policy.iteration_limit
        ):
            raise BudgetRefused("budget_exceeded")

    def cancel(self, operation_id: str) -> "BudgetGate":
        """Release only a reservation known not to have reached provider dispatch."""
        entry = self.entry(operation_id)
        if entry.state == "cancelled":
            return self
        if entry.state != "reserved":
            raise BudgetRefused("reconciliation_required")
        return self._replace(replace(entry, state="cancelled"))

    def unconfirmed(self, operation_id: str) -> "BudgetGate":
        """Transport failure or missing usage: retain the entire estimate, never retry."""
        entry = self.entry(operation_id)
        if entry.state == "unconfirmed":
            return self
        if entry.state != "dispatched":
            raise BudgetRefused("operation_not_dispatched")
        return self._replace(replace(entry, state="unconfirmed"))

    def insufficient_credits(self, operation_id: str) -> "BudgetGate":
        entry = self.entry(operation_id)
        if entry.state == "insufficient_credits":
            return self
        if entry.state not in {"dispatched", "unconfirmed"}:
            raise BudgetRefused("operation_not_dispatched")
        return self._replace(replace(entry, state="insufficient_credits"), "insufficient_credits")

    def settle(self, operation_id: str, actual: int) -> "BudgetGate":
        nonnegative(actual)
        entry = self.entry(operation_id)
        if entry.state == "settled":
            if entry.actual != actual:
                raise BudgetRefused("actual_conflict")
            return self
        if entry.state not in {"dispatched", "unconfirmed", "insufficient_credits"}:
            raise BudgetRefused("operation_not_dispatched")
        pause = self.pause_reason
        if actual > entry.request.estimate and pause is None:
            pause = "estimate_overrun"
        return self._replace(replace(entry, state="settled", actual=actual), pause)

    def _require_active(self) -> None:
        if self.pause_reason is not None:
            raise BudgetRefused("paid_stages_paused")

    def _replace(
        self,
        changed: LedgerEntry,
        pause: Literal["insufficient_credits", "estimate_overrun"] | None = None,
    ) -> "BudgetGate":
        pause = pause or self.pause_reason
        if pause is None and not self._acknowledged(changed):
            if changed.state == "insufficient_credits":
                pause = "insufficient_credits"
            elif (
                changed.actual is not None and changed.actual > changed.request.estimate
            ) or changed.exposure_floor > changed.request.estimate:
                pause = "estimate_overrun"
        return replace(
            self,
            entries=tuple(
                changed if entry.request.operation_id == changed.request.operation_id else entry
                for entry in self.entries
            ),
            pause_reason=pause,
        )
