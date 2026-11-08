# Offline cloud training boundary

`PlatformTrainingBackend` implements `TrainingBackend[CloudRun]`. Its one-step
`train(request)` returns accepted/active/terminal progress, not a local
`TrainingResult`: a cloud `best.pt` was selected using provider rectangular
validation and cannot satisfy the local fixed-shape checkpoint-selection contract.
The shared protocol is covariant in its result, leaving the local implementation
and its invariants unchanged.

## Inputs and records

A trusted application constructs `CloudPlan` from a frozen dataset mapping,
approved base-model ID/URI and digest, recipe, explicit GPU and pinned price.
The complete plan, configuration digest, original `TrainingRequest` and
`SpendRequest` are saved together. The estimate reserves at least 900 seconds in
integer micro-USD, rounding up. No model output selects these values. The model
slug hashes the application project ID and run ID as a structured pair, so separate
projects can reuse run IDs in a shared provider project. It is a deterministic
exact-lookup key; the API does **not** have an idempotency key. A new warm-start experiment requires a new run ID, checkpoint digest, parent
run ID and reservation. An unchanged operation is reconciliation, not a restart.

`train_arguments()` pins the full baseline from the delivered training request,
substituting supported model/data URIs and removing CPU-only runtime and local
per-epoch save settings. `captureDatasetVersion` and `gpuType` are explicit. The
requested val/predict/export stage contracts retain identical recipe, postprocess
and augmentation metadata, with static export. These are **contracts**, not
fabricated cloud execution evidence. Augmentation metadata is not forwarded to
non-training vendor methods. The controlled downstream worker still must measure
tensors and the exported signature before qualification.

After start the model readback supplies dataset ID, version and content hash.
The plan requires the expected Platform content hash from verified synchronization
(separate from the compiler manifest hash). The captured version may be newly
allocated, but its content must equal that synchronized identity on the very first
readback as well as later reads. No hash algorithm is invented for vendor content IDs.
Missing/changed pinned arguments or captured dataset identity permanently mark
`args_mismatch`; they never silently fall back to vendor defaults. Store only
matching allowlisted resolved values plus mismatched key names, not arbitrary
provider values that could contain secrets. A mismatch does not stop cost
reconciliation for a failed/cancelled/completed remote job.

The start receipt retains `estimatedCost` (price/memory) and `billing` (estimated
total/balance), normalized to typed integer amounts by the HTTP adapter. These are
never actuals. A receipt exceeding the authorized estimate (including its hourly
rate applied to the reserved duration) is retained durably and pauses further paid
stages through the budget port. It is not settled as a charge. Lost, invalid or
unpersisted start pricing instead leaves a durable `pricing_unconfirmed` marker;
`pause_pricing_uncertain` fails closed on further paid work without inventing a
price/actual. The pause is attempted even on a receipt-save exception and retried
on recovery. Readback cannot resolve pricing, and there is no automatic resume.
`TrainingTransport.charge` returns only an authoritative **FINAL TOTAL** bound to
both the original model and provider run ID, or `None`. Interim, partial, estimated
and unknown totals must normalize to `None`, even with a valid transaction ID and
attribution. Real integrations need authoritative billing finality evidence; without
it they must return `None`. Neither a terminal model state nor attribution alone
proves finality, and the backend cannot detect an adapter lying about it. A final
billing total can arrive while model readback is stale/running or unavailable;
there is no terminal-model gate.

`Charge` represents that final total, not an increment. `BudgetPort.reconcile`
stores the final amount and original-job attribution atomically and immutably;
repeating the same settlement is idempotent. `None` keeps the estimate counted and
actual unset until final, and never overwrites settlement. `settled_charge` restores
the same durable final total and attribution after a crash before model readback,
even during a model-endpoint outage. A replacement provider job is quarantined as
`identity_mismatch`, while late final billing for the original bound job remains
independently reconcilable. Replacement checkpoints are never downloaded. Until an
authoritative attributed final total is available, the ledger retains the reservation
estimate even when training failed or was cancelled. Downloaded `best.pt` is bounded and hashed by
the application, then placed in the injected content-addressed artifact store.
No vendor hash is assumed. Provider initialization digest, package versions,
Albumentations behavior, dataloader retention and fixed-shape checkpoint selection
remain explicitly unverified. No registry promotion is performed here.

## Required trusted ports (no production adapters supplied)

All five ports must be supplied; otherwise `ports_unavailable` occurs before I/O.
There is no mutable enable flag or `authorized: true` request field.

- `SecretPort` authenticates/authorizes the service and workspace/project/operation
  binding and configures credentials out of band. The grant contains no key.
- `BudgetPort` durably reserves, authenticates approval/autonomous limits, performs
  atomic single-use dispatch, and exposes a separate `authorize_start` gate that
  rechecks current pause/authority/limits and the trusted application's current UTC
  month immediately before a first paid start. A stale month is rejected with
  `dispatch_month_mismatch`: the integration must cancel the provably unsent
  operation and obtain a new ID/reservation/approval, never rewrite its month.
  Cancellation of an already-dispatched ledger marker requires fenced proof that
  no paid start intent exists; this module does not bypass `BudgetGate.cancel`.
  Historical/uncertain starts only reconcile in their original month. The port
  attributes actuals and pauses every project paid stage on 402 or uncertain pricing. Merely invoking
  the pure `BudgetGate.reserve` idempotent fast path is **not** sufficient to
  reauthorize an already-dispatched operation.
- `RunStore` fences competing writers for the bounded advance and commits intent
  before each mutation. Its lock must remain effective through remote I/O; an
  unfenced expiring lease or a dict is not production implementation. Store and
  ledger recovery must retain their original operation ID after a crash.
- `TrainingTransport` validates the pinned HTTP schema, uses exact model lookup,
  does not automatically retry mutations (including SDK retries on 429), checks
  returned resource IDs, bounds I/O and enforces approved artifact hosts without
  forwarding the Platform bearer to storage. No endpoint comes from model output.
- `ArtifactPort` writes content-addressed bytes and returns a stable non-secret
  reference, not a temporary signed URL.

The integration owner composes the delivered real transport, persistent ledger,
credential and fenced-store adapters. That operation is intentionally unavailable
in this module, not a dependency blocking offline fixtures. No SDK or external
service is instantiated here; imports stay within the licence boundary.

## Recovery behavior

A separate `dispatch_intent` phase precedes atomic ledger dispatch. A refused
dispatch, or a crash just after its ledger commit, recovers from authoritative
ledger state; no provider call is possible in this phase. Definitive preflight
refusals durably release only reservations known never to have dispatched. Release
is idempotent, including after a crash between terminal-state and ledger writes.
If GPU availability disappears after free model creation, the run stays in
`model_created` with `gpu_unavailable`, retaining its conservative hold but a real
recovery path. A later authorized first start reuses that model and reservation;
there is no second create and no release of an uncertain paid start.

Mutation intent is durable **before** create/start. A timeout, 409, 429, malformed receipt,
or other uncertain response only allows read-only reconciliation. Missing
readback is not proof of non-execution and never authorizes another mutation.
An uncertain create can recover its model by the exact stable slug, then perform
its first start. An uncertain start only reads the existing model. Another
provider job ID or resource under that operation becomes `identity_mismatch`.

402 and 503 are terminal-for-now, never mutation retries. A 402 saves terminal
intent first; if pausing the ledger crashes, the next advance retries only the
idempotent ledger pause. A 503 during create permits exact-slug read-only lookup
and recovery of the model ID, but not even a first start. A 503 after a known model
permits read-only reconciliation in case the response was uncertain. No transient
failure releases estimated cost.
Failed/cancelled/completed provider jobs remain readable for late charge attribution
and checkpoint retrieval, with their original IDs and costs.

Tests use the checked-in licence-clean **synthetic normalized-port recordings**
in `packages/platform/tests/fixtures/cloud_training.json`, derived from accepted
plan v0.3 and SDK commit `8af3921`, not captured account traffic. The HTTP transport
owner supplies raw pinned OpenAPI fixture qualification separately. Fixtures cover
both sizes/NMS modes, missing controls, concurrent/restarted attempts, uncertain
create/start, 402/503, cost attribution, captured dataset identity and checkpoint
limits. Synthetic billing normalization returns `None` for nonfinal totals (including
an interim 10,000 micro-USD), retaining the 172,500 estimate across active, restarted,
quarantined and model-outage advances; an authoritative final 120,000 replaces it
once. The outage fixture also supplies explicitly final billing despite cached
running state. These fixtures qualify the port contract, not a real HTTP adapter's
finality detection. No paid training or live Platform call is allowed in CI.

## Dependency and verification scope

The only dependency change is declaring the already-locked first-party
`labelloop-core` workspace edge. No registry version, artifact integrity, build
backend or external package changes; no local installation or source build.
Existing Hatchling first-party editable build behavior is unchanged. Native uv
release-age/source-build controls are not retroactively claimed for the existing
registry lock; a broader policy rollout is outside this change. Frozen clean
installation, lock validation, lint, types and suites run on GitHub Actions.
