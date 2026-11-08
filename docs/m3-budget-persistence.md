# Shared durable budget service

This slice implements the PostgreSQL service behind plan §12.1 for both paid
workers. It reuses `labelloop_core.budget`; the review-batch extension adds a
conservative child-exposure floor without changing the existing transitions.
It does not expose HTTP
routes, authenticate a caller, perform provider I/O, enable spend, change a real
budget or finish end-to-end acceptance checks 23/24. Authenticated HTTP and worker
consumers remain separate integration work.

## Authority and units

`BudgetStore` is a trusted Python service, not a public serialization boundary.
Its structural `Principal` port matches the authentication slice's `id`, `kind`,
`role`, and `permits` properties. The consuming adapter must authenticate on every
call and derive the principal from the server session/service credential. Never
construct it from a browser or model body. Admin methods require human `admin`;
paid worker methods require `service` plus exactly `worker-llm` or
`worker-platform`, matched against the persisted intent. A worker cannot approve
manual spend or edit policy. Reviewer and other service roles cannot do either.
There is no new authentication engine or token handling here.

Audit events persist principal ID, kind, role and a deterministic SHA-256 version
of that exact principal snapshot. This is snapshot versioning, not a claim of a
session generation/revocation counter (the delivered auth port has no such field).
The auth adapter remains responsible for enabled/revoked/current-session checks.
Historical events retain the original snapshot even after a role change.

Money remains integer USD microdollars. `TokenPrices` rates/version are persisted
in policy history and copied into the operation at reservation. LLM forecasts
require explicit maximum uncached input/output token counts and an estimate at
least their pinned two-rate cost. The worker must conservatively establish those
bounds before preparing the summary, rather than trusting a model's estimate.
Cache, reasoning, image-specific or other separately priced usage is unsupported
by this two-rate contract: do not manufacture `TokenUsage` for it. The reviewer
codec already returns `None` for unsupported/missing usage; reconciliation then
retains the estimate, never assumes a zero charge.

Platform forecasting remains the Platform adapter's explicit contract: estimate
from the selected GPU/time budget (at least the plan's 15-minute training floor),
image count and pinned price-table version. `estimatedCost` is only an estimate,
not an attributed billing record. The ledger does not invent a GPU tariff or
infer a confirmed cost from training completion/cancellation.

## Transaction and dispatch contract

All mutations own an `Engine.begin()` transaction. The persistent `budget_head`
row is locked `FOR UPDATE` before reading ledger exposure, even for different
iterations/months in one project. Different projects have independent locks.
Do not use this store under an external transaction expecting a returned claim
before commit. The store explicitly selects READ COMMITTED isolation on its engine
view; non-PostgreSQL engines are refused. Serialization/deadlock/commit failure does
not return permission to dispatch. SQL driver/parameter and engine echo logging
must remain disabled; errors are not safe public response bodies.

1. A trusted producer assembles immutable `SpendRequest` and `DispatchIntent`.
   `configuration_sha256` pins the complete server-owned dry-run display and
   worker inputs: endpoint/model configuration, batch/source IDs, maximum tokens,
   or GPU/epochs/image count. The store accepts only the hash and bounded provider
   identifier, not the provider URL, pixels, prompts or credentials.
2. `prepare` persists the estimate and returns a `DryRun` digest over request,
   intent and current policy revision. It checks current limits, clock, forecast
   and pixel allowlist, but makes no reservation and grants no I/O. Repreparing
   an unsent/unreserved operation replaces the current summary; old digests are
   invalid. Audit history retains previous summaries. IDs are project-scoped.
3. The authenticated admin's `reserve(project, operation, digest, actor=...)`
   is the click on that exact summary. Only an explicitly autonomous policy
   permits the stage's service principal instead. Reservation and approval
   provenance/audit commit together, after rechecking *every* limit under lock.
   Exact replay returns the existing entry, never another claim.
4. The stage's worker calls `claim`, comparing its immutable configuration hash.
   Policy revisions, month and allowlist are rechecked. The core `dispatch`
   transition consumes the reservation exactly once. The unique claim ID,
   principal, state and audit COMMIT **before** the receipt is returned. A receipt
   is informational, not a reusable bearer token; do not queue it for replay.
5. Only that successful call permits one provider attempt. If the process crashes
   after commit (even before I/O), loses the receipt, or sees an uncertain response,
   **never dispatch again**. Recover by reading/reconciling persisted state and
   provider evidence. There is deliberately no lease expiry/reclaim or retry API.
   This trades potential unsent work for no duplicate spend in the absence of a
   provider idempotency guarantee. Final worker wiring must obey this contract.
6. `outcome(unconfirmed)` or missing supported usage keeps the full estimate.
   `reconcile` accepts only operation-attributed `BillingCharge` for Platform or
   validated `TokenUsage` plus operation ID and receipt hash for LLM. It computes
   usage cost with the operation's original prices, regardless of later policy
   changes. Evidence is a hash, not an arbitrary provider billing/body payload.
   Trusted adapters must validate attribution before constructing these types;
   this is not a cryptographic proof of a provider charge. Different evidence or
   actual cannot overwrite an already confirmed actual.
7. `outcome(insufficient_credits)` persists a project-wide 402 pause. No retry is
   permitted. A confirmed actual over estimate is recorded without clamping and
   pauses new paid work. Neither policy edits nor month changes clear a pause.
   There is no unauthenticated or automatic resume method.
8. Only a still-`reserved` operation can be cancelled/released: by invariant it
   has never received a claim. Claimed, uncertain or 402 work is not proven
   unsent. Its reservation cannot be released merely because cancellation was
   requested or the worker disappeared. Confirm actual billing instead.

An unsent reservation crossing a month boundary is cancelled by an admin and
replaced with a **new** operation ID/current-month summary and click. A policy
change likewise invalidates outstanding unsent approvals/claims; cancel and
prepare a new operation. Iteration exposure spans month boundaries, while later
actual reconciliation stays in the original reservation month. Cancelled IDs
remain occupied. A lowered ceiling is rechecked before dispatch. Historical
charges remain counted with their actual or conservative estimate.

The provider pixel allowlist defaults empty. Any `sends_pixels=True` intent,
including Platform image dispatch, requires explicit membership; a no-pixel
operation does not implicitly grant later pixel sharing. Worker consumers must
use the exact persisted intent/configuration, never let model output choose the
provider or the pixel flag.

## Bounded review batches and child requests

One admin click approves one annotation-job review stage: fixed sorted image IDs,
one configured provider/model, pinned project release, maximum passes and full
maximum-pass estimate. Supply `DispatchIntent.review_batch = ReviewBatch(...)`;
its complete binding participates in the dry-run digest, including the image set.
`ReviewBatch.image_set_sha256` is available for the summary. Repreparing with a
different set, model, release, pass bound or estimate invalidates the old click.
Provider/configuration/probe identity remains in the configuration hash. The
consumer validates image membership in the pinned release and must not populate
these records from model output.

`ReviewPriceTable` is an admin-maintained, audited policy record for one configured
provider/model, with USD input/output/image token rates, version, effective date
and public source reference. It is never fetched from the provider. No current
review table gives `no_price_table`; mismatched model/version or a future effective
date refuses dispatch preparation. Each edit increments the policy revision,
invalidating outstanding approvals. Reservation copies the original table for
later actual reconciliation. Source references must contain no credentials or
sensitive content; they are audit data, not endpoints the ledger will contact.
Fixtures use invented rates only. Unsupported usage, including cached or
separately priced reasoning usage, must stay unconfirmed rather than be coerced
into this three-rate record.

The parent reserves the full forecast: conservatively rounded cost of the
per-image/pass input, output and image-token bounds × image count × max passes.
It counts once against every project/month/iteration/operation limit. The worker
uses `claim_review_request` for each full evidence-bundle request (one image/pass),
not the flat `claim` API. It supplies the immutable parent configuration hash,
image/pass, child request ID, estimate and supported token bounds. Multiple
network requests/crops for a single image/pass are not authorized by this port;
the worker must compose the evidence bundle in one request, or use a separately
approved operation contract rather than bypass this bound.

Under the same project lock, the service validates the image/pass and bounds,
rechecks policy/month/privacy/limits and computes child actuals plus outstanding
estimates. If another child would exceed the approved parent estimate, it commits
`approval_exhausted` and audit **before** returning refusal. That batch pause is
sticky: it is not cleared by lower actuals, replay or a policy edit. The remainder
requires a new operation, dry run and click (or a new bounded autonomous
reservation), never an in-place approval top-up. Autonomous mode applies all the
same checks. A rejected child has no claim and performs no I/O.

A successful child claim inserts its estimate and consumes its unique image/pass
and request ID in one transaction, before returning dispatch permission. Only
that successful call permits one provider attempt. Duplicate IDs/image-passes,
restart, lost receipt and uncertain responses cannot obtain another claim.
The first child also consumes the parent's reservation, preventing cancellation
from releasing a potentially sent batch. The parent claim is never exposed.

`reconcile_review_request` accepts operation-attributed supported `ReviewUsage`
with a child request ID and receipt digest, or records unconfirmed/402 outcomes.
Missing usage retains the child estimate and SQL NULL evidence. A 402 pauses all
paid stages and never retries. A child actual above its estimate also pauses paid
work. The parent remains unconfirmed and reserves at least its original estimate
until the trusted job controller stops the stage and calls `finish_review_batch`.
That method requires every sent child confirmed, closes the batch permanently and
settles the exact sum of actuals, releasing unused capacity. It cannot release a
lost/uncertain child estimate. Known child exposure above the parent estimate is
retained in `LedgerEntry.exposure_floor` while the parent actual stays NULL; the
ledger never hides an overrun or invents a confirmed total for unfinished work.
`review_snapshot` exposes child state and batch pause to authorized consumers.

Local admin-registered private/loopback endpoints do not use this paid port and
write **no** budget ledger rows. Review run usage/probe/cache provenance is still
required. Trusted registration and dispatch integration owns address validation,
DNS re-resolution and no redirects under Security; this store provides no generic
endpoint or arbitrary-network authority. Provider enabled/key/probe checks and
actual request-bound enforcement are also dispatch integration obligations.

## Persistence and migration

Alembic revision `budget_0001` follows delivered `0002_local_models`. Its distinct
identity is reserved with the authentication migration owner; sibling auth uses
`0003_authentication`. Whichever sibling integrates second must reconcile the
migration graph (a merge revision or a not-yet-shipped down-revision adjustment)
with that owner before merge. This is integration ordering, not a global build
hold. `budget_0002` follows `budget_0001`, adding the child request table and
batch-specific pause. Historical DDL is explicit and does not import application metadata.

Tables: `budget_head` (current policy/revision/pause), `budget_dry_run` (current
unreserved summary), `budget_operation` (immutable reservation identity plus
single-use claim/outcome/evidence), `budget_event` (append-only policy and
transition history). Database triggers reject audit edits/deletes, operation
removal and changes to an operation's immutable request/approval/intent/prices or
consumed claim/evidence. All accepted mutations append audit in the same database
transaction; audit failure rolls back the associated balance/state/claim/pause.
Balances are computed from validated core ledger entries under the project lock,
not a second independently mutable counter. This initial implementation scans a
project's entries; a measured performance need may justify normalized aggregate
counters later, but not a second authority today.

## Actions evidence and remaining integration

`apps/api/tests/test_budget_store.py` has transport-free codec/frozen-migration
checks and real PostgreSQL fixtures selected only by
`LABELLOOP_BUDGET_POSTGRES=1`. The existing Actions `ml-smoke` job supplies a
pinned PostgreSQL 16 service, verifies clean upgrade/downgrade/re-upgrade, then
runs ledger fixtures before CPU training. Each fixture gets its own random
schema and removes it on exit. No live/shared DB, paid calls, keys or new
dependencies are used. Unit jobs explicitly skip the PostgreSQL-only cases.

Fixtures cover racing monthly/iteration/operation ceilings, at-most-one claim
across workers and engine restart, uncertainty/reconciliation, original price
pinning, attributed billing, persistent 402/overrun pauses, stale summary/intent/
policy, admin versus service roles, empty pixel allowlist, cancellation, month
rollover, project isolation, immutable history and injected audit failures.

Before end-to-end acceptance: wire authenticated admin queries/commands and the
worker execution/reconciliation paths; compare actual worker configuration with
the pinned summary; enforce token bounds and Platform forecasting; translate
stable refusal codes into non-retry job outcomes and visible paid-stage pause;
retain final API/log redaction and no-paid-call tests. These consumers cannot
substitute a returned reservation/snapshot for a fresh successful claim.
