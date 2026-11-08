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
protocol and evaluation release, new evidence and explicit policy renewal. Referenced artifacts are
immutable store IDs and SHA-256 digests, never transport URLs. Source and recipe
IDs are unique and sorted. Environment artifacts include decoder, encoder,
interpolation and library versions that affect output.

Before execution, the owner commits the preregistered protocol artifact. Its hash
pins domain, per-recipe primary metric, critical AP50 classes/slices (including
tiny objects and negatives), tolerances and decision rule. The protocol names the
separately pinned `evaluation_release`; trusted qualification validators must
check that exact binding, locked-final-test status, and split-group disjointness
from training, references and active selection. The default primary metric is
original-frame mAP50-95, with the lower bound of a 95% paired bootstrap interval
above zero, resampling split groups. Critical AP50 non-regression uses the declared
tolerance (null means zero); real targets remain admin-owned, not fixture defaults.
A protocol changed after results are seen makes the run development evidence;
changing either pin invalidates receipts, but cannot restore holdout independence.
Only real human-adjudicated domain gold can support an M6 improvement claim.
Harness truth and fixture ports prove mechanics only and never an M6 exit PASS.
These checks belong to the authoritative qualification boundary, not to this pure
projection; a hash reference alone proves neither provenance nor independence.

The graph is freeze → propose/discover → review → validate → adjudicate/audit →
release, then independent compile → sync → train → evaluate → promote/retain
chains per recipe, followed by select-next. Local sync is a validated local-only
variant receipt, not a Platform mutation. `IterationPorts` exposes typed outputs
against delivered canonical release, correction loop, audit, training,
evaluation and registry contracts. Platform mapping/compiled variant receipts
are integration boundaries, not a second representation of mutable Platform
state. Proposal/discovery includes a separate whole-image discovery artifact.
A human-first bootstrap with zero proposals is valid: its empty proposal artifact
never establishes exhaustive completeness or a verified negative. Human review
and the canonical completeness/release gates remain necessary.

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
2. It calls `prepare_stage` on the current revision before reserving or dispatching
   any budget. This pure preview checks readiness and training-round eligibility;
   it is not permission to persist or invoke a worker. If no new intent is prepared
   (including a round-limit pause or an existing intent), no new ledger dispatch is
   allowed. For a new paid intent, the trusted transaction prepares the ledger's
   dispatch transition, calls `begin_stage` on the same original projection with
   that authorization, and **CAS-commits** the ledger, returned projection and fenced
   job intent together before any I/O. CAS failure discards all those transitions;
   a refused preview never commits a dispatched reservation without an intent.
   Only a newly inserted intent may be invoked, never an idempotent return.
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
`reconcile_retired` before any new iteration. Round/correction history is retained. Validation intent pins its original source
membership and correction ceiling; late retired-intent readback merges consumed
passes under that original scope, even if the current manifest has changed. The
original round-evidence digest is retained separately from the latest stop reason,
so a subsequent credit/drift pause cannot erase valid restart provenance.
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
configured ceiling still applies; extending it requires a fresh explicit renewal
artifact not previously used for that batch. Per-batch ceiling/renewal history
survives switches away from and back to a batch; it is not inferred from only the
immediately preceding manifest. A renamed batch or iteration is not new evidence. The service
must preserve canonical source-batch IDs and the retained round ledger. New
iteration admission rejects evidence already consumed by that batch anywhere in
retained round history, before shared stages (including paid review) can run;
switching batches or alternating evidence does not make old evidence new.

Correction pass counts and object-hash histories cannot decrease or reset on
resume/new rounds; they reuse the delivered one/two-pass `CorrectionLoop` bound.
A policy transition cannot lower the ceiling beneath consumed passes or tighten
it while a validation intent remains uncertain. Such a transition fails explicitly
instead of admitting a subsequent validation contract that cannot be satisfied.
A failed recipe skips further training/evaluation and can only produce an
explicit retain decision. Another recipe may continue, but is never implicitly
promoted. An unqualified candidate retains the incumbent through its own
validated decision receipt. A confirmed failed registry transaction records a failed
receipt with no decision or selected model: it settles the intent but blocks
select-next. It cannot be retried implicitly. After an explicit stop/pause and
reconciliation of all other intents, new evidence can start a fresh iteration;
the failed receipt and consumed rounds remain in history. Failed registry receipts
do not change their own input digest or become successful retain decisions.

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
pause, not a retry instruction. Paid review must reuse the durable review-budget
service's fixed admin-approved batch and child-request ledger, binding images,
provider/model, price version, release, estimate and pass count. Actuals plus
outstanding estimates pause at the approved estimate. This controller adds no
second batch approval, spend authority or scheduler.

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
Acceptance check 22 additionally requires the authenticated application facade
and its end-to-end flow; controller fixtures cannot substitute for it. Management
screens are not an M6 exit gate.

REA angle: reuse delivered domain/review/budget/training/evaluation/registry
records and the existing durable runtime; no copied engine or second scheduler.
