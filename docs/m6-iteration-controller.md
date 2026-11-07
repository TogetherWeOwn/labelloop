# Bounded iteration controller (offline contracts)

This slice implements plan §§10.1, 10.5 and 12 as a pure projection of durable
job and validated artifact receipts. It is not a queue, scheduler, registry,
annotation store, paid-dispatch authority or qualification service. No real
adapter is wired: `real_iteration_ports()` always refuses. No metric improvement,
independent verification, account connection or end-to-end milestone completion
is claimed by these fixtures.

## Inputs and stages

`IterationManifest` pins project/access scope, canonical source pixels and
annotation revisions, source batch, ontology, approved reference pack, teacher,
each recipe/training profile/incumbent, split assignment, review pipeline,
provider/prompt/schema/environment versions, acceptance policy, evaluation
protocol, new evidence and explicit policy renewal. Referenced artifacts are
immutable store IDs and SHA-256 digests, never transport URLs. Source and recipe
IDs are unique and sorted. Environment artifacts include decoder, encoder,
interpolation and library versions that affect output.

The graph is freeze → propose/discover → review → validate → adjudicate/audit →
release, then independent compile → sync → train → evaluate → promote/retain
chains per recipe, followed by select-next. Local sync is a validated local-only
variant receipt, not a Platform mutation. `IterationPorts` exposes typed outputs
against delivered canonical release, correction loop, audit, training,
evaluation and registry contracts. Platform mapping/compiled variant receipts
are integration boundaries, not a second representation of mutable Platform
state. Proposal/discovery includes a separate whole-image discovery artifact.

`StageReceipt` is **trusted validator evidence**, never model output or a public
request payload. Validators own checking actual bytes/hashes, canonical
membership/completeness/exclusions, full source/policy lineage, compiler gates,
resolved training arguments, original-frame evaluation and all qualification
vetoes. They must bind each output to its committed operation ID/input digest
and record their own version. The controller does not equate an artifact's
existence, successful training or a machine audit with independent quality.
A promote receipt must come from the authoritative per-recipe registry
transaction after its qualification checks; a retain receipt must identify that
recipe's pinned incumbent (including no incumbent). Incumbent drift fails the
store's registry revision comparison, never silently updates this manifest.

## Durable integration contract

1. Existing job/store code loads the manifest, history and current revision.
2. It calls `begin_stage` for a ready stage and **CAS-commits** the returned
   projection plus its fenced job intent before any I/O. Only a newly inserted
   intent may be dispatched, never an idempotent return of an existing intent.
3. A worker validates the typed port result and atomically commits its artifact
   plus `finish_stage` receipt. Complete validation accounts for every source's
   terminal `CorrectionLoop`; unresolved results still require adjudication,
   not training approval. No independent/human/gold tier is synthesized here.
4. On restart, `resume` requires the exact ordered completed receipts after the
   store revalidates artifact bytes, validators and lineage. Completed IDs are
   reused. `recover_stage` returns `reconcile`, never `dispatch`, for any intent
   lacking a receipt. Provider/job readback may establish completion or confirmed
   absence as a failed receipt; a timeout or inconclusive lookup remains pending.
   There is deliberately no blind retry transition, even after confirmed failure.
5. Cancellation stops new work but permits recording readback of already-issued
   intent. It does not mean remote cancellation succeeded or budget was refunded.
   Remote IDs, cancellation intent and uncertain billing remain authoritative in
   the existing worker/ledger records, linked by the stage's artifact.

Changed source/policy/reference/teacher/recipe input conservatively retires all
current stage records. Nothing stale remains reusable; retired artifacts remain
available for descendant tracing. Uncertain retired intent must be settled via
`reconcile_retired` before any new iteration. Round/correction history is retained.
There is no reactivation of the same invalidated training intent: create an
explicit new iteration after reconciliation. The store must retain the complete
projection/history, reject stale CAS writes, keep canonical batch identities
stable, and never construct a new empty history to bypass limits. This pure
module does not itself implement database persistence, leases or authentication.

## Bounds, failures and stop gates

Both recipes together consume one training round when the first training intent
is committed, including failed or cancelled attempts. Per-source-batch lifetime
round counts survive new iterations and invalidation. A new round needs new
source/evidence, changed recipe/training profile or explicit policy renewal.
Changing only a teacher or unrelated policy/configuration is insufficient. The
configured ceiling still applies; extending it requires a different explicit
renewal artifact. A renamed batch or iteration is not new evidence. The service
must preserve canonical source-batch IDs and the retained round ledger.

Correction pass counts and object-hash histories cannot decrease or reset on
resume/new rounds; they reuse the delivered one/two-pass `CorrectionLoop` bound.
A failed recipe skips further training/evaluation and can only produce an
explicit retain decision. Another recipe may continue, but is never implicitly
promoted. An unqualified candidate retains the incumbent through its own
validated decision receipt.

Trusted policy evaluation can stop for sufficient scoped quality, no informative
data or plateau; pause for insufficient evidence, oscillation, drift/regression,
round exhaustion or insufficient credits. Each explicit `stop` records an
immutable evidence reference in its reason. There are no hard-coded universal
quality thresholds. Qualification/selection services evaluate the pinned project
policy and supply this evidence; machine agreement cannot mint verification.
In-flight intent still requires reconciliation after a stop/pause.

Paid review and Platform sync/train require `DurableBudgetAuthorization`: a
trusted ledger commit reference and an already-dispatched `BudgetGate` entry
matching project, iteration, stable operation ID and full input digest. All three
limits, click/autonomous policy and paid-stage pauses are rechecked. An in-memory
reservation alone is insufficient. This type is not authentication or a database
commit proof by itself: only the trusted persistence/worker boundary may create
it. That real boundary remains disabled here, so these contracts authorize no
provider calls. A 402 is a terminal-for-now worker/ledger result and a paid-stage
pause, not a retry instruction.

## Verification and remaining integration

`packages/core/tests/test_iteration.py` uses licence-clean synthetic metadata,
existing `CorrectionLoop` values and fake store/provider receipts. It covers
complete graph progression in unrelated projects; validated artifact reuse;
source/policy/reference/teacher/recipe invalidation; crash/cancel/uncertain
intent; isolated recipe failure and explicit retains; bounded rounds and renewal;
correction-history preservation; scoped stop/pause signals; and fail-closed paid
stage authorization. Pickle round-trips use only self-generated fixture values,
not untrusted input or a recommended persistence format. Full suites and strict
typing run on GitHub Actions only, with no paid calls.

The integrator still owns real job/store CAS and leases, typed receipt validators,
M3 annotation/audit sinks, durable budget dispatch, M4 Platform reconciliation,
M5 qualification/registry evidence and M6 selection/experiment wiring. This module
must not be enabled by merely replacing the disabled factory: those boundary
checks and integration fixtures are required first. The separate integrated
non-game experiment remains responsible for measured improvement evidence.

REA angle: reuse delivered domain/review/budget/training/evaluation/registry
records and the existing durable runtime; no copied engine or second scheduler.
