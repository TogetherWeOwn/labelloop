# Offline Platform composition seam

`labelloop_platform.assembly.assemble_training` performs a read-only composition
preflight, then returns the existing `PlatformTrainingBackend`. It is not an API
route, worker, scheduler, connection validator, sync engine or new ledger. No
training is performed by assembly itself. There is no production enable switch.

The trusted application supplies all six producer capabilities. Missing any port
fails before even connection lookup. `SecretPort` must authenticate the service
and exact project/workspace/operation/configuration binding; its grant has no key.
The new `SynchronizedReleasePort` reads an authorized, persisted frozen mapping.
Missing, pending, incomplete or conflicted reconciliation is refused. Every
project, workspace, remote dataset and local release/recipe/manifest/class/split
identity must match the plan. The Platform content hash is compared separately
from the compiler manifest digest. A queued ingest is never a verified mapping.
Producer exceptions at these two seams become fixed non-secret error codes.

`ReleaseReadback` is trusted producer data, not evidence merely because somebody
constructs the dataclass. Only the sync producer can establish byte identity,
complete membership, labels, class order, geometry, split reconciliation and
partial-output rejection. This module does not repeat those checks or accept its
records from public requests/model output. The producer must keep the mapping
immutable and fenced. Assembly cannot make a mutable remote dataset immutable.
The returned backend retains all existing authorization, single-use budget,
write-intent persistence, reconciliation and checkpoint bounds. It authenticates
again on each advance; preflight is not a cached spend grant.

Retain the backend for recovery. Do not make sync reassembly a prerequisite for
late billing or uncertain-job reconciliation: a sync outage must not hide an
existing liability. This module adds no recovery loop or persistence mechanism.

## What the offline test actually composes

`packages/platform/tests/test_assembly.py` uses the existing cloud producer's
fixture ports rather than another accounting model or training implementation:

| Component | Evidence in this change | Production claim |
| --- | --- | --- |
| Connection | Recorded exact `ConnectionGrant`; denial and scope mismatch | Fixture only; no principal lookup, validation or unwrap |
| Frozen sync | Synthetic `ReleaseReadback`; each identity mismatch, missing record and incomplete state | Fixture only; no upload, byte identity, label replacement or remote persistence |
| HTTP transport | Real merged `PlatformAdapter`, pinned SDK/schema, `httpx.MockTransport` replay of start response | Real adapter, no socket/account; only start wire composition covered here |
| Cloud backend | Real merged `PlatformTrainingBackend`, both 320 and 640, captured content identity, hashed checkpoint, settlement | Synthetic provider run, not training |
| Ledger | Real merged pure `BudgetGate` behind the existing cloud fixture `Ledger` | Not the PostgreSQL `BudgetStore`; no durable ledger integration claimed |
| Run/artifact stores | Existing cloud fixture memory ports; injected failed save prevents dispatch | Fixture only; no durable fencing/object storage qualification |
| Connections UI | Existing merged fixture-only module remains unchanged | No backend binding or UI integration claimed |

The new JSON recording is a licence-clean synthetic contract example derived
from the accepted plan and pinned official SDK contract, not captured traffic.
Test-only `WireFixtureTransport` normalizes that recording to `StartReceipt`; it
is deliberately not a production HTTP-to-training adapter. It supplies no billing
finality proof. Synthetic final billing reuses the cloud producer's recording.

## Exact remaining producer bindings

1. Credential producer: worker-only unwrap, authenticated project/operation lookup,
   workspace validation and persisted connection state must implement `SecretPort`.
   The open credential implementation is not imported or reported delivered.
2. Sync producer: translate its actual persisted, complete frozen-release record
   to `ReleaseReadback`. Its open implementation remains sole owner of upload,
   class/split/byte identity, conflict-safe labels and partial-output checks.
3. Integration owner: normalize the real `PlatformPort`/`StoragePort` to
   `TrainingTransport` (including exact model identity, bounded approved-host
   downloads and conservative authoritative final billing). Current protocols
   differ; structural injection of the raw transport is not integration.
4. Integration owner with ledger producer: bridge durable `BudgetStore` to
   `BudgetPort`. `BudgetStore.prepare/reserve/claim/outcome/reconcile` require a
   real authenticated principal and trusted dispatch intent; they do not directly
   implement cloud `reserve_and_authorize/authorize_start/dispatch/settled_charge`.
   Preserve current-month/pause/approval rechecks, single-use claims, immutable
   model/job-attributed final settlement, and fenced never-started cancellation.
   Do not fake missing finality/attribution or replay a claim to make it fit.
5. Integration owner: a durable `RunStore` fence effective through I/O and a
   content-addressed `ArtifactPort`; persist original operation identity across
   restart. Authenticated API/worker and UI reads remain separate integration.
6. Live smoke stays on the final integration owner: actual authorized workspace,
   credential/budget/placement prerequisites, and manual-dispatch-only
   create/ingest/reconcile/delete workflow. Never training. None is granted here.

## Acceptance boundaries

Plan checks 1, 8, 9, 10 (Platform completeness), 18, 23, 24, 26 and 27 remain
required for final integration. This change demonstrates **fixture-only** binding
refusal, no dispatch without every capability, existing backend authority/budget
rechecks, save-before-dispatch, pinned start arguments, captured dataset hash and
single synthetic start/settlement. It does not establish post-ingest per-image
membership, byte identity, conflict-safe label replacement, real encrypted-secret
isolation, persistent principal/ledger integration, live recovery or smoke PASS.
Existing producer tests and final integration must supply those exact checks.

GitHub Actions is the full-suite record. Local pre-push format/lint run only on
changed Python files; project typing and covering tests are skipped if their
Python tools/dependencies are unavailable, without installing a virtualenv.
Exact-head Actions run/job links and conclusions belong in the PR verification
record; a draft or skipped job is not a passing gate.

REA angle: reuse the merged official SDK transport, pinned contract, cloud
backend and budget transitions. No new client, queue, ledger, sync algorithm,
credential construction, facade or design is introduced.
