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
slug is a deterministic exact-lookup key; the API does **not** have an idempotency
key. A new warm-start experiment requires a new run ID, checkpoint digest, parent
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
stages through the budget port. It is not settled as a charge. A transaction bound
to both model and provider run ID supplies the actual, with charge attribution and
settlement stored atomically. Recovery reads that durable attribution before asking
the provider again. Until attribution, the ledger retains the reservation estimate
even when training failed or was cancelled. Downloaded `best.pt` is bounded and hashed by
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
  atomic single-use dispatch, rechecks current pause/authority before start,
  attributes actuals, and pauses every project paid stage on 402. Merely invoking
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
limits. No paid training or live Platform call is allowed in CI.

## Dependency and verification scope

The only dependency change is declaring the already-locked first-party
`labelloop-core` workspace edge. No registry version, artifact integrity, build
backend or external package changes; no local installation or source build.
Existing Hatchling first-party editable build behavior is unchanged. Native uv
release-age/source-build controls are not retroactively claimed for the existing
registry lock; a broader policy rollout is outside this change. Frozen clean
installation, lock validation, lint, types and suites run on GitHub Actions.
