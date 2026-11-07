# M3 budget accounting core

This sequential slice implements the pure `BudgetGate` contracts from plan §12.1.
It does **not** enable a paid operation, change a project's real budget, expose an
HTTP endpoint, or claim completion of acceptance check 23. Persistence, sessions,
LLM adapters and job dispatch remain required integration work. The prior review
validator and audit primitives are described in `m3-review-core.md`.

## Units and price inputs

All money is integer USD microdollars (1 USD = 1,000,000 units). Negative values,
booleans, floats, non-finite numbers and numeric strings are rejected. Limits
start unset, and `autonomous_spend` defaults to false. Every applicable limit must
be configured before reserving even a zero-cost operation.

`TokenPrices` carries a version and two rates in microdollars per million tokens.
`cost(TokenUsage(...))` computes the exact combined input/output numerator and
rounds up once to a microdollar. There is no floating-point arithmetic, live price
lookup or hard-coded provider tariff. The fixture prices in tests are not actual
provider quotes. The two-rate contract supports only uncached input/output;
separately priced cache, reasoning, image or other usage must receive an explicit
adapter and price contract, never a silent zero-cost default.

The application must pin the model/provider, endpoint configuration, price table,
maximum output tokens, source batch and input-token bound in the dry-run record.
`SpendRequest.configuration_sha256` identifies that complete immutable record;
`price_version` pins its price table. Compute the estimate conservatively using
those token bounds, not the model's self-reported cost. Compute actuals using the
same pinned price table and validated provider usage; missing or unsupported
usage stays unconfirmed. This module performs no tokenization or network I/O.

## Ledger transitions

A `BudgetGate` is a project-scoped immutable snapshot. Every accepted transition
returns a new snapshot; rejected transitions leave the original unchanged.

- `reserve` records the estimate before dispatch. It checks operation, monthly
  and iteration ceilings inclusively: equality is permitted, exceeding is not.
  Iteration totals span months. Other iterations share the same monthly ceiling.
- A manual-spend reservation requires `SpendApproval` bound to the exact request
  digest, including operation/project/iteration, month, price version, estimate
  and configuration hash. The stored actor identifies the authenticated click.
  **This type does not authenticate anyone.** The future auth adapter must derive
  the actor and verify authorization; never deserialize approval claims from a
  model or caller-controlled request body.
- Exact reservation replay is idempotent. Reusing an ID with different request
  or approval evidence is an `operation_conflict`. Replaying an old reservation
  does not grant another dispatch: callers must check the persisted state.
- Each entry retains whether autonomous spend was enabled at reservation time.
  Disabling it later does not demand retroactive clicks for historical charges.
  `dispatch` rechecks current ceilings and manual-approval requirements, so
  outstanding unapproved reservations cannot dispatch under a new manual policy.
- `dispatch` accepts a reservation once only. Dispatched and uncertain operations
  cannot be cancelled, re-reserved under a changed request, or dispatched again.
- `unconfirmed` records a transport failure, missing usage or unknown billing
  outcome. It retains the estimate in both limits until reconciliation.
- `settle` records a confirmed actual, including zero, and counts that actual
  instead of the estimate. Identical settlement is idempotent; conflicting actuals
  are rejected. An actual above the estimate is never discarded or clamped: it
  is recorded and pauses all new reservations and dispatch pending intervention.
- `cancel` releases only an unsent reservation. Its ID remains occupied, so a
  replay cannot revive a cancelled operation.
- `insufficient_credits` records a 402 and pauses the entire project's paid
  stages. It never retries. Outstanding estimates remain reserved; confirming a
  zero charge later does not remove the pause. There is deliberately no resume
  method without a separately implemented authenticated recovery workflow.

A stable `BudgetRefused.code` distinguishes budget refusal, paused work, request
conflicts, missing approval and invalid transition. Provider error bodies and
secrets are never stored here. No paid worker uses these primitives yet.

## Required integration gates before dispatch

1. Persist the complete request, approval provenance, policy version and ledger
   transitions in PostgreSQL. Serialize reservation/dispatch/settlement per
   project in one transaction, including concurrent operations in different
   iterations. An in-memory snapshot or process-local lock cannot enforce a
   hard budget across workers.
2. Commit the dispatch intent **before** provider I/O. A crash after intent but
   before response is uncertain; reconcile instead of blindly retrying. A replay
   returning the same snapshot is not a second dispatch grant.
3. Derive project, iteration and UTC month from trusted application state. Never
   allow a model or browser to select accounting buckets. An unsent reservation
   crossing a UTC month boundary must be cancelled and re-approved/reserved with
   a new operation ID in the dispatch month; historical reconciliation remains
   attributed to the original reservation month. Do not clear a 402/overrun pause
   at a month boundary.
4. Authenticate the click on the exact dry-run summary while autonomous spend is
   off; protect policy changes with admin authorization. Service principals and
   model output cannot supply approval or raise limits. Keep provider secrets
   outside this domain model and respect project pixel-sharing policy.
5. Require a configured conservative estimate and pinned price version, validate
   actual token usage, and preserve missing/ambiguous charges as unconfirmed.
   Connect the paused outcome to a visible paid-stage banner and bounded job
   state, not a retry queue.
6. Demonstrate persistent concurrent ceiling enforcement, crash/restart recovery,
   authenticated approvals, a 402 with zero retries, and secret-redaction tests
   on GitHub Actions before claiming acceptance checks 23 or 24 complete.

This is implementation scaffolding within the approved plan, not an ADR changing
spend policy and not an authorization to call a provider. Worker/adapters,
secrets/auth, persistence, review APIs, audit-to-promotion wiring and the designed
review UI remain on the open M3 milestone.
