# ADR: Fenced PostgreSQL proposal jobs

- Status: accepted implementation of the technical queue decision
- Scope: plan sections 2.3, 5.1–5.2, 8.7 and 12

## Decision

Use the existing PostgreSQL 16 dependency, psycopg driver and explicit `FOR UPDATE
SKIP LOCKED` protocol. No queue service, queue library, scheduler or general-purpose
execution engine is added. Supervision calls a bounded `run_once`; it does not execute
handlers, endpoints, paths or credentials supplied by model output.

Jobs pin a project-scoped operation key and JSON input. Re-enqueue with identical
inputs returns the existing job; changed inputs are a conflict. Each claim increments
a generation and attempt count. The database clock determines expiration. Every
heartbeat, artifact write and completion requires a matching worker and generation,
a running job and an unexpired lease under a row lock. Cancellation increments the
generation and is terminal. Failures retry with capped exponential backoff, at most
ten attempts; lease expiration also spends an attempt. Stored errors are fixed codes,
never exception messages. Deadline checks execute after acquiring the row lock;
a heartbeat that waited past expiration cannot revive the lease. Every store operation
uses an explicit transaction even with an autocommit connection factory. The runner
renews leases on a separate connection during blocking stage calls, stops renewal on
error/cancellation and enforces a one-hour maximum stage runtime. Handlers may also
call the injected heartbeat at safe boundaries. Late computations may finish but
cannot save/apply results; no attempt is made to kill vendor code inside a thread.

Completed stage artifacts are immutable bounded JSON records with SHA-256 hashes.
A reclaimed job skips completed stages. Final application locks the job, inserts a
unique application marker, invokes a trusted **same-database transactional** sink,
rechecks the lease deadline and marks success in one transaction. Sink errors or
expiration roll back both effects and marker. Sinks must not commit, close the
connection or produce remote effects. This guarantees at-most-once committed local
application, not exactly-once computation or arbitrary network delivery. Future
remote adapters need an outbox and their own reconcile-before-retry protocol.

## Proposals and independent discovery

`ProposalJobRunner` fixes the order to discovery then proposal and requires a job ID:
request-bound callbacks cannot claim another workflow's job and spend its retry budget.
General kind-wide runners are only for handlers able to execute any input of that kind.
`ProposalWorkflow`
pins the source, expected annotation revision, ontology/scope/reference pack, actual
reference records, recipe, full registry release and revision, candidate threshold
and discovery version. Both callbacks verify worker configuration against immutable
job input. A resumed operation cannot accidentally use a newly promoted champion.

Discovery receives original pixels and scope, never teacher boxes or scores. Its
result is saved even on zero-detection images. The offline injectable provider port
is not a hosted detector or LLM implementation. It must be supplied by the authorized
evidence/transport integration; no automatic default claims that empty output proves
an image negative. Partial, cropped and truncated discovery are recorded explicitly.

`ProposalProvider` reuses the M2 controlled `predict_samples` path with its actual
input-tensor hook, explicit square shape, `rect=False`, `tile=False`, NMS and inverse
source transform. The only extension to M2 is an explicit low candidate confidence
argument; the existing AP default remains unchanged. Candidate confidence is stored
separately from deployment confidence. The checkpoint digest, recipe digest, classes
and runtime are checked against the pinned champion. Decoder/encoder/interpolation
and raster gains follow the compiler recipe; library versions and transforms remain
in the run record. Full-frame `end2end` outputs are not subjected to extra IoU
suppression; the recorded NMS-free release policy is preserved.

Source order is qualifying recipe champion, then admin-pinned initial detector from
a frozen project release, then `source: none` human-first seeding. Initial detector
records pin checkpoint hash/source/version/licence/vocabulary and controlled runtime.
Their class mapping defaults empty; only explicit admin-approved mappings bound to
the same frozen release may identify project classes. Worker/reviewer approval roles,
unfrozen configuration and mappings from another release are refused by the port.
The trusted API producer still owns actual authentication and release persistence;
these role/provenance fields are not authentication by themselves. No pretrained
weights are downloaded. An empty mapping means unknown identity even when detector
and project names happen to match. Absent class-specific evidence remains uncertain.
With no detector, blind discovery is still recorded as inspection evidence, but no
annotation candidates are emitted and completeness remains unreviewed. Reference
retrieval permissions and holdout exclusion remain the evidence service's responsibility.

The append-only `ProposalRunSink` stores evidence, not canonical annotation edits.
All outputs are proposed/unreviewed, incomplete for release purposes and ineligible
for training or gold. Database checks reinforce these fields. The authenticated
annotation adapter must use service-principal authorization and expected-revision
checks from the review API; the queue does not authenticate arbitrary callers or
permit overwriting human corrections. No public route or production worker startup
is introduced by this slice.

## Migrations and integration

The reserved identity `0006_proposal_jobs` owns only `worker_*` tables and guards.
It starts from the delivered `0002_local_models` revision on this independent branch,
with branch label `proposal_jobs`. Authentication and credential/budget owners keep
their separate revisions. Before merging branches, the integrator must chain or
merge their Alembic heads without rewriting any already-applied revision. All CI
uses isolated disposable PostgreSQL; no shared database or evidence window is used.
Queue identifiers are application keys, deliberately not guesses at unmerged API
principal IDs. The trusted producer validates project access before enqueue.

## Verification and limits

Actions owns migration upgrade/downgrade/re-upgrade, real PostgreSQL concurrent
claim, lease expiration, stale generation, cancellation, retry, replay, rollback,
immutable records and human-field refusal fixtures. Offline fixtures exercise
320/640 lineage, low thresholds, discovery ordering, zero detections and uncertain
identity. The CPU ML smoke observes real square tensors with locally generated
random fixture weights; it proves shape/lineage plumbing, not model quality or a
production champion qualification. No hosted requests, paid calls, new dependencies,
credential provisioning or deployment are needed.

REA angle: reuse the delivered compiler geometry, M2 fixed-shape inference/registry
and PostgreSQL lock semantics rather than adding a replacement inference engine,
queue library or third-party code.
