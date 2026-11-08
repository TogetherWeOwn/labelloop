"""Trusted service port for both paid workers. No routes or provider I/O.

Every mutation owns its transaction and locks the project's budget head. A claim is
returned only after COMMIT, once; losing it is an uncertain operation, not a retry.
Principals come from authentication, requests/intents from trusted application state,
never model output or deserialized principal claims. SQL/driver payload logging must
remain disabled. Provider bodies, URLs, credentials and pixels are not accepted.
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from labelloop_api import budget_schema as db
from labelloop_core.budget import (
    BudgetGate,
    BudgetPolicy,
    BudgetRefused,
    LedgerEntry,
    SpendApproval,
    SpendRequest,
    TokenPrices,
    TokenUsage,
    identity,
    nonnegative,
)


class Principal(Protocol):
    """Structural port implemented by PR #10's authenticated Principal."""

    @property
    def id(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def role(self) -> str: ...

    def permits(self, permission: str) -> bool: ...


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def sha256(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise ValueError("expected a SHA-256 digest")


@dataclass(frozen=True)
class DispatchIntent:
    """Pinned opaque configuration, not an endpoint or arbitrary request payload.

    The configuration hash in SpendRequest pins GPU/epochs/image count, batch,
    endpoint/model, input/output bounds and all immutable worker inputs displayed
    by the caller's dry-run summary. The worker must compare that hash at claim.
    """

    stage: Literal["llm", "platform"]
    provider_id: str
    sends_pixels: bool = True
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.stage not in {"llm", "platform"}:
            raise ValueError("unsupported paid stage")
        if re.fullmatch("[a-z][a-z0-9_-]{0,63}", self.provider_id) is None:
            raise ValueError("provider requires a configured identifier, not a URL")
        if type(self.sends_pixels) is not bool:
            raise ValueError("pixel intent requires a boolean")
        if self.max_input_tokens is not None:
            nonnegative(self.max_input_tokens)
        if self.max_output_tokens is not None:
            nonnegative(self.max_output_tokens)
        if (self.max_input_tokens is None) != (self.max_output_tokens is None):
            raise ValueError("forecast requires both token bounds")


@dataclass(frozen=True)
class DryRun:
    request: SpendRequest
    intent: DispatchIntent
    policy_revision: int
    digest: str


@dataclass(frozen=True)
class DispatchClaim:
    """An informational receipt, not a replayable bearer capability."""

    project_id: str
    operation_id: str
    claim_id: str
    configuration_sha256: str


@dataclass(frozen=True)
class BillingCharge:
    """Only an adapter-validated charge attributed to this exact operation.

    A Platform estimatedCost is NOT a BillingCharge. Evidence is a content digest
    of the attributable billing record, never the raw provider body or a secret.
    """

    operation_id: str
    amount: int
    receipt_sha256: str

    def __post_init__(self) -> None:
        identity(self.operation_id)
        nonnegative(self.amount)
        sha256(self.receipt_sha256)


def decode_entry(value: dict) -> LedgerEntry:
    return LedgerEntry(
        request=SpendRequest(**value["request"]),
        approval=SpendApproval(**value["approval"]) if value["approval"] else None,
        state=value["state"],
        actual=value["actual"],
        reserved_autonomously=value["reserved_autonomously"],
    )


class BudgetStore:
    def __init__(self, engine: sa.Engine, clock: Callable[[], datetime] | None = None) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("budget serialization requires PostgreSQL")
        self.engine = engine.execution_options(isolation_level="READ COMMITTED")
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("budget clock requires aware UTC time")
        return now.astimezone(UTC)

    @staticmethod
    def _admin(actor: Principal) -> None:
        if actor.kind != "human" or actor.role != "admin" or not actor.permits("admin"):
            raise BudgetRefused("admin_required")

    @staticmethod
    def _worker(actor: Principal, intent: DispatchIntent) -> None:
        if actor.kind != "service" or actor.role != "worker-" + intent.stage:
            raise BudgetRefused("worker_scope_mismatch")

    def _event(self, connection, actor, head, action, record, operation_id=None) -> None:
        connection.execute(
            sa.insert(db.event).values(
                id=str(uuid4()),
                project_id=head["project_id"],
                operation_id=operation_id,
                principal_id=actor.id,
                principal_kind=actor.kind,
                principal_role=actor.role,
                principal_version=digest({"id": actor.id, "kind": actor.kind, "role": actor.role}),
                policy_revision=head["revision"],
                action=action,
                record=record,
                occurred_at=self._now(),
            )
        )

    @staticmethod
    def _head(connection, project_id):
        row = (
            connection.execute(
                sa.select(db.head).where(db.head.c.project_id == project_id).with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise BudgetRefused("budget_not_configured")
        return row

    @staticmethod
    def _operations(connection, project_id):
        return (
            connection.execute(
                sa.select(db.operation).where(db.operation.c.project_id == project_id)
            )
            .mappings()
            .all()
        )

    def _gate(self, connection, head) -> BudgetGate:
        return BudgetGate(
            head["project_id"],
            BudgetPolicy(**head["policy"]["limits"]),
            tuple(
                decode_entry(row["entry"])
                for row in self._operations(connection, head["project_id"])
            ),
            head["pause_reason"],
        )

    @staticmethod
    def _scope(table, project_id, operation_id):
        return (table.c.project_id == project_id, table.c.operation_id == operation_id)

    @staticmethod
    def _privacy(head, intent: DispatchIntent) -> None:
        if intent.sends_pixels and intent.provider_id not in head["policy"]["pixel_providers"]:
            raise BudgetRefused("pixel_provider_disallowed")

    def set_policy(
        self,
        project_id: str,
        *,
        actor: Principal,
        expected_revision: int,
        policy: BudgetPolicy,
        pixel_providers: tuple[str, ...] = (),
        prices: TokenPrices | None = None,
    ) -> int:
        """Admin-only CAS; every edit invalidates old dry runs and unsent claims.

        Does not resume a paused project. Retains immutable policy/principal history
        in the audit record, including price-table rates/version and allowlist.
        """
        self._admin(actor)
        identity(project_id)
        nonnegative(expected_revision)
        for provider in pixel_providers:
            DispatchIntent("llm", provider)
        value = {
            "limits": asdict(policy),
            "pixel_providers": sorted(set(pixel_providers)),
            "prices": asdict(prices) if prices else None,
        }
        with self.engine.begin() as connection:
            connection.execute(
                insert(db.head)
                .values(project_id=project_id, revision=1, policy=value)
                .on_conflict_do_nothing()
            )
            head = self._head(connection, project_id)
            # An initial insert and an existing revision 1 must be distinguished.
            history = connection.execute(
                sa.select(db.event.c.id).where(db.event.c.project_id == project_id).limit(1)
            ).first()
            current = head["revision"] if history else 0
            if current != expected_revision:
                raise BudgetRefused("policy_conflict")
            revision = current + 1
            connection.execute(
                sa.update(db.head)
                .where(db.head.c.project_id == project_id)
                .values(revision=revision, policy=value)
            )
            self._event(connection, actor, dict(head) | {"revision": revision}, "policy", value)
        return revision

    def prepare(self, request: SpendRequest, intent: DispatchIntent, *, actor: Principal) -> DryRun:
        """Persist a trusted dry-run estimate. This does not reserve or authorize I/O."""
        if actor.kind == "human":
            self._admin(actor)
        else:
            self._worker(actor, intent)
        with self.engine.begin() as connection:
            head = self._head(connection, request.project_id)
            gate = self._gate(connection, head)
            gate._require_active()
            gate._check_limits(request, extra=request.estimate)
            self._privacy(head, intent)
            if request.month != self._now().strftime("%Y-%m"):
                raise BudgetRefused("reservation_month_mismatch")
            prices = head["policy"]["prices"]
            if intent.stage == "llm":
                if not prices or prices["version"] != request.price_version:
                    raise BudgetRefused("price_version_mismatch")
                if intent.max_input_tokens is None or intent.max_output_tokens is None:
                    raise BudgetRefused("forecast_required")
                bound = TokenUsage(intent.max_input_tokens, intent.max_output_tokens)
                if request.estimate < TokenPrices(**prices).cost(bound):
                    raise BudgetRefused("estimate_below_forecast")
            if connection.execute(
                sa.select(db.operation.c.operation_id).where(
                    *self._scope(db.operation, request.project_id, request.operation_id)
                )
            ).first():
                raise BudgetRefused("operation_conflict")
            record = {
                "request": asdict(request),
                "intent": asdict(intent),
                "policy_revision": head["revision"],
            }
            summary = DryRun(request, intent, head["revision"], digest(record))
            connection.execute(
                insert(db.dry_run)
                .values(
                    project_id=request.project_id,
                    operation_id=request.operation_id,
                    digest=summary.digest,
                    **record,
                )
                .on_conflict_do_update(
                    index_elements=[db.dry_run.c.project_id, db.dry_run.c.operation_id],
                    set_={"digest": summary.digest, **record},
                )
            )
            self._event(connection, actor, head, "prepare", record, request.operation_id)
        return summary

    def reserve(
        self, project_id: str, operation_id: str, dry_run_digest: str, *, actor: Principal
    ) -> LedgerEntry:
        """Authenticated click or explicitly enabled autonomous service reservation."""
        sha256(dry_run_digest)
        with self.engine.begin() as connection:
            head = self._head(connection, project_id)
            row = (
                connection.execute(
                    sa.select(db.dry_run).where(*self._scope(db.dry_run, project_id, operation_id))
                )
                .mappings()
                .one()
            )
            intent = DispatchIntent(**row["intent"])
            manual = actor.kind == "human"
            if manual:
                self._admin(actor)
            else:
                self._worker(actor, intent)
                if not head["policy"]["limits"]["autonomous_spend"]:
                    raise BudgetRefused("approval_required")
            if row["digest"] != dry_run_digest or row["policy_revision"] != head["revision"]:
                raise BudgetRefused("stale_dry_run")
            request = SpendRequest(**row["request"])
            if request.month != self._now().strftime("%Y-%m"):
                raise BudgetRefused("reservation_month_mismatch")
            self._privacy(head, intent)
            gate = self._gate(connection, head)
            approval = SpendApproval(actor.id, request.digest) if manual else None
            changed = gate.reserve(request, approval=approval)
            entry = changed.entry(operation_id)
            if changed != gate:
                connection.execute(
                    sa.insert(db.operation).values(
                        project_id=project_id,
                        operation_id=operation_id,
                        dry_run_digest=dry_run_digest,
                        policy_revision=head["revision"],
                        entry=asdict(entry),
                        intent=asdict(intent),
                        prices=head["policy"]["prices"],
                    )
                )
                self._event(connection, actor, head, "reserve", asdict(entry), operation_id)
        return entry

    def claim(
        self,
        project_id: str,
        operation_id: str,
        *,
        actor: Principal,
        configuration_sha256: str,
    ) -> DispatchClaim:
        """Single-use dispatch decision committed before the caller may perform I/O.

        Never retry after losing this receipt or after a provider timeout. Recovery
        may read/reconcile the operation but cannot obtain another dispatch claim.
        """
        sha256(configuration_sha256)
        with self.engine.begin() as connection:
            head = self._head(connection, project_id)
            row = self._operation(connection, project_id, operation_id)
            intent = DispatchIntent(**row["intent"])
            self._worker(actor, intent)
            self._privacy(head, intent)
            request = decode_entry(row["entry"]).request
            if row["policy_revision"] != head["revision"]:
                raise BudgetRefused("stale_dry_run")
            if request.configuration_sha256 != configuration_sha256:
                raise BudgetRefused("intent_mismatch")
            if request.month != self._now().strftime("%Y-%m"):
                raise BudgetRefused("reservation_month_mismatch")
            changed = self._gate(connection, head).dispatch(operation_id)
            claim = DispatchClaim(project_id, operation_id, str(uuid4()), configuration_sha256)
            self._save(
                connection,
                head,
                changed,
                operation_id,
                {
                    "claim_id": claim.claim_id,
                    "claimed_by": actor.id,
                },
            )
            self._event(connection, actor, head, "claim", asdict(claim), operation_id)
        return claim

    @staticmethod
    def _operation(connection, project_id, operation_id):
        return (
            connection.execute(
                sa.select(db.operation).where(
                    *BudgetStore._scope(db.operation, project_id, operation_id)
                )
            )
            .mappings()
            .one()
        )

    @staticmethod
    def _save(connection, head, changed, operation_id, extra=None):
        connection.execute(
            sa.update(db.operation)
            .where(*BudgetStore._scope(db.operation, head["project_id"], operation_id))
            .values(entry=asdict(changed.entry(operation_id)), **(extra or {}))
        )
        connection.execute(
            sa.update(db.head)
            .where(db.head.c.project_id == head["project_id"])
            .values(pause_reason=changed.pause_reason)
        )

    def cancel(self, project_id: str, operation_id: str, *, actor: Principal) -> LedgerEntry:
        """Only an unclaimed reservation is proven unsent; a claimed crash is not."""
        self._admin(actor)
        with self.engine.begin() as connection:
            head = self._head(connection, project_id)
            gate = self._gate(connection, head)
            changed = gate.cancel(operation_id)
            if changed != gate:
                self._save(connection, head, changed, operation_id)
                self._event(connection, actor, head, "cancel", {}, operation_id)
        return changed.entry(operation_id)

    def outcome(
        self,
        project_id: str,
        operation_id: str,
        *,
        actor: Principal,
        status: Literal["unconfirmed", "insufficient_credits"],
    ) -> LedgerEntry:
        if status not in {"unconfirmed", "insufficient_credits"}:
            raise ValueError("unsupported outcome; provider bodies are not accepted")
        with self.engine.begin() as connection:
            head = self._head(connection, project_id)
            row = self._operation(connection, project_id, operation_id)
            self._worker(actor, DispatchIntent(**row["intent"]))
            gate = self._gate(connection, head)
            changed = getattr(gate, status)(operation_id)
            if changed != gate:
                self._save(connection, head, changed, operation_id)
                self._event(connection, actor, head, status, {}, operation_id)
        return changed.entry(operation_id)

    def reconcile(
        self,
        project_id: str,
        operation_id: str,
        *,
        actor: Principal,
        billing: BillingCharge | None = None,
        usage: TokenUsage | None = None,
        usage_operation_id: str | None = None,
        usage_receipt_sha256: str | None = None,
    ) -> LedgerEntry:
        """Confirm attributable billing or supported two-rate usage, never an estimate.

        Missing/unsupported usage is passed as None and remains unconfirmed. The LLM
        codec only yields TokenUsage for supported uncached input/output. No raw
        usage JSON, cache/reasoning fallback, price override or fake zero is accepted.
        """
        with self.engine.begin() as connection:
            head = self._head(connection, project_id)
            row = self._operation(connection, project_id, operation_id)
            intent = DispatchIntent(**row["intent"])
            self._worker(actor, intent)
            gate = self._gate(connection, head)
            evidence = None
            actual = None
            if billing is not None:
                if (
                    intent.stage != "platform"
                    or usage is not None
                    or billing.operation_id != operation_id
                ):
                    raise BudgetRefused("billing_attribution_mismatch")
                actual = billing.amount
                evidence = {"kind": "billing", **asdict(billing)}
            elif usage is not None:
                if intent.stage != "llm" or usage_operation_id != operation_id:
                    raise BudgetRefused("usage_attribution_mismatch")
                sha256(usage_receipt_sha256)
                if not row["prices"]:
                    raise BudgetRefused("price_version_mismatch")
                prices = TokenPrices(**row["prices"])
                if prices.version != gate.entry(operation_id).request.price_version:
                    raise BudgetRefused("price_version_mismatch")
                actual = prices.cost(usage)
                evidence = {
                    "kind": "usage",
                    "operation_id": operation_id,
                    "receipt_sha256": usage_receipt_sha256,
                    "usage": asdict(usage),
                    "price_version": prices.version,
                }
            if actual is None:
                # A 402 retains its terminal outcome/pause even with missing billing.
                if gate.entry(operation_id).state == "insufficient_credits":
                    return gate.entry(operation_id)
                changed = gate.unconfirmed(operation_id)
            else:
                if row["evidence"] is not None and row["evidence"] != evidence:
                    raise BudgetRefused("actual_conflict")
                changed = gate.settle(operation_id, actual)
            if changed != gate:
                # Preserve SQL NULL until real evidence exists; JSON null would
                # activate the immutable-evidence guard and block later actuals.
                extra = {"evidence": evidence} if evidence is not None else None
                self._save(connection, head, changed, operation_id, extra)
                self._event(
                    connection,
                    actor,
                    head,
                    "reconcile",
                    {
                        "entry": asdict(changed.entry(operation_id)),
                        "evidence": evidence,
                    },
                    operation_id,
                )
        return changed.entry(operation_id)

    def snapshot(self, project_id: str) -> BudgetGate:
        """Internal query; HTTP authorization belongs to the consuming admin adapter."""
        with self.engine.begin() as connection:
            head = self._head(connection, project_id)
            return self._gate(connection, head)
