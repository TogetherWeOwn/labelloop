# Offline Platform composition seam

`labelloop_platform.assembly.assemble_training` performs read-only preflight and
returns the existing `PlatformTrainingBackend`: no route, worker, scheduler,
connection validator, sync engine, ledger, training or production enable switch.
All six capabilities come from trusted worker composition, never public/model data.
Missing ports fail before lookup. `SecretPort` must authenticate the exact
project/workspace/operation/configuration grant without exposing a key.
`SynchronizedReleasePort` must read an authorized, persisted frozen mapping:
missing/pending/incomplete/conflicted reconciliation fails, and every local and
remote identity must match. Platform content hash and compiler digest are distinct.
Connection/sync exceptions become fixed non-secret codes. A queued ingest or a
constructed `ReleaseReadback` is not proof of verification: the sync producer owns
byte/label/class/split/dimension/membership completeness, partial-output rejection,
immutable persistence and fencing. Assembly cannot freeze a mutable remote dataset.
The backend retains authority rechecks, single-use budget, write-intent persistence,
reconciliation and checkpoint bounds; preflight is not cached spend authority.
Retain it for recovery: sync reassembly/outages must not gate late billing or
uncertain-job reconciliation. Assembly adds no recovery loop or persistence.

## Offline evidence

`packages/platform/tests/test_assembly.py` reuses existing cloud fixture ports:

| Component | Evidence | Boundary |
| --- | --- | --- |
| Connection | Exact grant, denial/scope mismatch | No real principal lookup, validation or unwrap |
| Frozen sync | Synthetic readback, every identity mismatch, missing/incomplete records | No upload, byte/label replacement or remote persistence proof |
| HTTP | Real pinned `PlatformAdapter` with `httpx.MockTransport` start replay | No socket/account; only start wire composition |
| Cloud | Real backend, 320/640, content identity, hashed checkpoint, settlement | Synthetic provider run, never training |
| Ledger | Real pure `BudgetGate` behind existing fixture `Ledger` | Not durable PostgreSQL `BudgetStore` integration |
| Run/artifact stores | Existing memory ports; failed save prevents dispatch | No durable fencing/object-storage qualification |
| Connections UI | Existing fixture-only module unchanged | No backend/UI binding |

The JSON is a licence-clean synthetic SDK-contract example, not captured traffic.
Test-only `WireFixtureTransport` maps it to `StartReceipt`, not a production adapter
or billing-finality proof; synthetic final billing reuses the cloud recording.

## Remaining bindings (final integration owner)

1. Credential producer: implement worker-only unwrap, authenticated operation and
   workspace lookup, persisted connection state through `SecretPort`; not bound here.
2. Sync producer: translate actual persisted complete frozen evidence to readback.
   Merged sync modules retain sole ownership of upload/class/split/byte identity,
   conflict-safe labels and partial-output rejection; this bridge is not supplied.
3. Normalize `PlatformPort`/`StoragePort` to `TrainingTransport`: exact model identity,
   approved-host bounded downloads and authoritative conservative final billing.
4. Bridge durable `BudgetStore.prepare/reserve/claim/outcome/reconcile` to cloud budget
   methods with real principal/intent, month/pause/approval rechecks, single-use claims,
   immutable model/job-attributed settlement and fenced never-started cancellation.
   Missing finality/attribution cannot be fabricated or a claim replayed to fit.
5. Supply durable `RunStore` fencing through I/O, content-addressed `ArtifactPort`,
   restart-stable operation identity, and separate authenticated API/worker/UI reads.
6. Authorized test workspace, credential/budget/placement and manual-dispatch-only
   create/ingest/reconcile/delete live smoke remain prerequisites. Never training.

Plan checks 1, 8–10 (Platform completeness), 18, 23, 24, 26, 27 remain final gates.
Fixtures prove refusal, authority/budget rechecks, save-before-dispatch, pinned start
arguments, captured content hash and one synthetic start/settlement, not real ingest,
byte/label completeness, secret isolation, persistent principal/ledger or live recovery.
GitHub Actions is the full-suite record; PR verification holds exact-head evidence and
bounded pre-push checks/skips, not claims from draft or skipped jobs.
REA angle: reuse the pinned SDK transport, cloud backend and budget transitions;
no replacement client, queue, ledger, sync, credentials, facade or design.
