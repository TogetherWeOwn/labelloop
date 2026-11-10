# Explicit acknowledgement of a paid-stage pause

- Status: Accepted implementation contract; product mounting is separate
- Date: 2026-11-10

## Context

The budget gate deliberately refuses reconstruction of an unpaused ledger with
historical insufficient-credit or estimate-overrun entries. Clearing only the
head's pause would break restart, and dropping historical entries would erase
exposure. Policy edits must not become implicit resume commands. Outcomes can
change the pause generation without changing the policy revision.

## Decision

Add `AcknowledgedPause`, an immutable, typed snapshot of the terminal ledger at
an explicit acknowledgement. `BudgetGate.acknowledge_pause()` preserves all
entries and exposure and clears only the current pause. Reconstruction accepts
historical causes only when the **exact entry** is in the acknowledged snapshot.
An added or changed causal entry requires a pause again. The default constructor
without acknowledgement keeps both existing fail-closed guards. The snapshot
reuses the existing `LedgerEntry` representation instead of adding cause flags
that could miss a later exposure increase or a 402 on the same operation.

The dedicated `BudgetResumeStore` serializes on the existing budget-head row
lock. It checks the expected revision and a server-derived `v1.<sha256>` token
covering head, full policy, every operation/claim/evidence, dry run, review child,
and immutable journal event, in deterministic order. Event IDs provide generation
nonces even when policy revision and visible pause reason are unchanged. The
hash is an optimistic concurrency token, not a credential or spend capability.

Under that same transaction, resume increments policy revision with the policy
unchanged, clears the head pause, and appends one actor-bound `resume` event.
The event records version 1, prior revision, exact state token and typed ledger
snapshot. All worker reconstruction uses explicit resume events, validates their
version, project, actor, revision and original immutable entry identities, and
uses the latest acknowledgement. No legacy acknowledgement is inferred. The
existing immutable-event table supports this contract without a migration or a
second ledger. Snapshot duplication in an audit event is deliberate evidence,
not another source of accounting authority.

Conservative v1 admission requires every parent to be settled, cancelled or
insufficient-credit, and every review child to be settled or insufficient-credit.
No reserved, dispatched or unconfirmed parent, or dispatched/unconfirmed child,
can be acknowledged. Review-batch `approval_exhausted` is never cleared. A 402
without attributable actuals retains its original nonzero conservative exposure.
Any later child outcome or increased parent exposure can pause again.

## Consequences

- Existing failed operations, child requests, claims, approvals, prices and event
  rows are not rewritten. Tokens cannot choose any of these fields.
- The revision increase invalidates pre-resume dry runs and old unsent claims.
  New work still requires current budgets, privacy, dry-run/approval and worker gates.
- There is no dispatch, provider I/O, billing reconciliation, retry, requeue,
  operation/child replay or drift resumption in this command.
- Snapshot/token generation reads the complete project ledger and journal while
  holding the head lock. This v1 tradeoff favors correctness; large-ledger
  optimization needs an independently proven durable generation contract.
- The human-admin session/CSRF router is intentionally **not mounted** in the
  product application. Integration, web wiring and generated OpenAPI are separate.
- Rolling back code after a resume requires retaining an implementation capable
  of reading these explicit events; old code fails closed rather than silently
  ignoring acknowledged historical causes. No downgrade/backfill is provided.

REA angle: reuse the delivered immutable journal, head lock and original claim
fences, rather than adding a recovery engine, approval replenishment or parallel
accounting system.
