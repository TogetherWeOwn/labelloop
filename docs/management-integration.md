# Management facade integration

Status: **contract preparation, not an integrated management API**. The default
application has not mounted these routes. The isolated OpenAPI generator mounts
unavailable ports, which return `management_unavailable`; it never substitutes a
fixture store for production persistence. Keep the PR draft until the acceptance
matrix below has real exact-head Actions evidence.

REA angle: reuse the delivered canonical records, orientation-normalizing ingest,
compiler, local training backend, source-frame evaluator and per-recipe registry.
Consume the existing session, append-only event and fenced queue implementations.
There is no new domain engine, scheduler, ledger, authentication store or UI shell.

## Contract slice

`labelloop_api.management.management_router` requires trusted principal and
operations dependencies. The principal is structural against the existing auth
contract (`id`, `kind`, `role`, `permits`). No client request selects a principal,
workspace, endpoint, storage key, class mapping, arbitrary training arguments or
paid backend. Human admins mutate; human reviewers/admins read. Service/model
principals cannot use either human management surface, even with an admin role.
Shared middleware still owns session authentication, CSRF and denial auditing.
The router is **not** a replacement for that middleware.

| Method/path | Port | Result |
| --- | --- | --- |
| POST /projects | create_project | 201 resource |
| POST /projects/{project_id}/assets | ingest_asset | 201 resource |
| POST /projects/{project_id}/dataset-releases | freeze_release | 201 resource |
| POST /dataset-releases/{release_id}/variants | compile_variant | 202 shared job |
| POST /variants/{variant_id}/training-runs | start_training | 202 shared job |
| POST /models/{model_id}/evaluations | start_evaluation | 202 shared job |
| POST /projects/{project_id}/promotions | promote | 200 promotion decision |
| GET /projects | list_projects | bounded resource page |
| GET /projects/{project_id}/resources?kind=… | list_resources | bounded resource page |
| GET /resources/{kind}/{resource_id} | read | resource summary |
| GET /models/{model_id}/export | read_export | artifact identity/digests/static shape |

These read models are deliberately minimal fixture contracts, not a claim that
all accepted management screens can already render their full detail. Detail
fields and downloads must be bound to actual producer records, not fabricated.

Every mutation requires `Idempotency-Key` and an expected aggregate revision
(project creation pins zero). Release requests additionally pin every selected
annotation revision. `MutationContext` binds authenticated actor, operation,
route target and the canonical validated body into a fingerprint; this is an
input to the real transaction, **not itself an idempotency implementation**.

The real adapter must:

1. Resolve organization/workspace and resource ownership from authenticated
   database records. Validate every referenced ontology, assignment, source,
   release, profile, candidate, incumbent and qualification belongs to that
   authorized scope. Return non-oracular not-found responses for inaccessible IDs.
2. Reconcile the principal-scoped idempotency key under a unique database guard.
   Matching retries return the original result before stale-revision checks;
   changed fingerprints conflict. Never replay across actors or route targets.
3. Lock the relevant aggregate and check expected revisions. Freeze only actual
   eligible canonical revisions under their authoritative completeness and split
   policy. Do not accept human-review/eligibility assertions in management input.
4. Commit state, attributed append-only audit and shared queue intent atomically.
   A failure rolls all three back. Compile/train/evaluate return existing durable
   fenced job IDs; they do not spawn background tasks or an alternate scheduler.
5. Normalize uploaded original bytes through `ingest_canonical_asset`; preserve
   original provenance and source groups. Source URIs are provenance only, never
   server-side URL fetch instructions. Resource limits at ingress must cap the
   encoded HTTP body as well as the decoded image and pixel/decompression budget.
6. Resolve approved local training profiles server-side, derive shape from the
   recipe and call the delivered ML worker. Compile 320/640 directly from original
   sources, never resize the other derivative. Run source-frame evaluation and
   export through delivered adapters, retaining hashes and static signatures.
7. Delegate promotion to the shared qualification operation. Consume it if it
   lands first; never add a second promotion route or copy its gates. Null targets,
   adverse audits, missing support, critical-slice regressions and compromised
   final-test independence retain the incumbent. Rollback metadata stays atomic
   and per-recipe. The facade cannot turn `retained` into `promoted`.

## Generated contract

GitHub Actions' unit job generates and publishes the `management-contract`
artifact containing `management.openapi.json` and `management-client.ts`:

```sh
uv run python -m labelloop_api.management_codegen --output-dir <artifact-directory>
```

This is an **unbound management-only schema**, not the eventual combined app
OpenAPI. Generation is deterministic and uses the pinned FastAPI/Pydantic versions;
no new generator dependency is introduced. The TypeScript client uses fixed
same-origin `/api` routes, encoded IDs, credentials, CSRF and idempotency headers,
no-store caching and refused redirects. It supplies no authorization of its own.
Response types are compile-time types, not permission to treat server/model data
as trusted domain state.

The tracked schema is `apps/api/openapi/management.openapi.json`; the tracked
client is `apps/web/src/generated/management-client.ts`. Both the unit and
web-build jobs compare fresh generation with these exact bytes before tests:

```sh
uv run python -m labelloop_api.management_codegen --check
```

A missing or stale file fails without rewriting it. To update the publication,
generate into an empty artifact directory with `--output-dir`, then copy the
schema and client to those tracked paths together. Do not hand-edit the outputs.
The initial schema publication comes from the detail-contract Actions artifact;
the client uses that schema with the corrected union renderer. The new head's
drift, lint, type and request tests must pass before that publication is verified.

The web job checks the **tracked** client rather than replacing it before lint,
typecheck and tests. It runs for Python changes as well as web changes, and the
CI aggregator requires it for both. A successful Python generator alone cannot
satisfy the client contract. These remain the isolated management contract, not
a final combined app schema. No local suite is used as a substitute.

## Canonical transaction adapter (partial, unmounted)

`CanonicalManagementMutations` implements project creation, original-image
ingestion and eligible canonical dataset freezing using the existing canonical
tables, `ingest_canonical_asset` and `canonical_event`; it is not a complete
`ManagementOperations` implementation.
No new table, migration, queue, grant or production binding is introduced.
The workspace UUID comes only from trusted deployment configuration. The
adapter rechecks human/admin authority and context attribution, scopes project
lookups to that workspace, and authorizes ownership before returning a replay.
Existing projects without management events fail closed pending explicit adoption.

The engine must use READ COMMITTED; repeatable-snapshot configurations are
refused so replay reads can see a commit made while awaiting a lock.
Each transaction takes a Postgres transaction-scoped advisory lock on workspace,
actor and retry key. A matching fingerprint returns the original resource before
revision checks; changing the actor/operation/target/body cannot replay it.
Creation additionally serializes the globally unique project key. Asset writes
lock the canonical project row, check its management revision from canonical
events, call the delivered orientation-normalizing ingest, append the initial
unreviewed annotation revision, and commit one attributed event with the result.
The asset's resource revision is its annotation revision (initially 1); the event
also records the incremented **project** revision. Source group metadata and the
pinned imaging toolchain are retained in that event. Materialized grouping/split
assignment and setup publication are still unimplemented, not inferred from it.

The supplied runtime role must have SELECT/INSERT on the touched canonical
tables, permission to lock the project row (Postgres requires UPDATE privilege
on at least one project column), and **no UPDATE, DELETE or TRUNCATE privilege on
canonical_event**. Every transaction checks that last condition, refusing broad
owner/superuser connections with `management_unavailable`. The adapter does not
create or widen runtime roles. Final deployment must integrate real least-
privilege role provisioning and shared auth, and independently verify all grants.
The disposable Actions fixture uses a temporary NOLOGIN role with UPDATE(scope)
on project for row locking; it removes that role's grants and role on teardown.

A storage or database error rolls back canonical rows and the audit/replay
receipt together. Object storage is not transactional: an event failure after
upload may leave an unreferenced, immutable content-addressed object, never a
successful asset receipt; retries normalize the original again and safely reuse
those bytes. No remote URL is fetched. Async compile/train/evaluate and setup
edits are not implemented by this class, so it cannot dispatch paid work or claim
queue/qualification semantics.

Release freeze is synchronous SQL, not a queued job. It takes the same project
and retry locks, then locks all project sources in stable UUID order (the same
source-row synchronization used by `append_review`). It rechecks latest ontology,
selected annotation revisions, current scope-bound completeness, non-truncated
inspection and actual training eligibility. Verified negatives need the same
completeness evidence as positive images. System acceptance additionally needs
its currently configured auto-accept qualification; request bodies cannot assert
human review, objects, eligibility, split membership or a passing policy.

Every source, including exclusions, needs a current locked assignment under one
seed/algorithm/threshold policy. Exact-hash and declared ingest-group constraints,
persisted similarity edges and unresolved borderline reviews are checked across
all project sources, so removing an endpoint from the selected samples does not
hide a cross-group relation. This consumes materialized grouping evidence; it
does not compute missing pHashes/embeddings, create assignments or claim setup
publication. Ambiguous assignment publication times fail closed. Setup/grouping
writers must acquire the project lock before publishing ontology, relations or
assignment versions; that writer binding remains a composition prerequisite.

The adapter constructs the delivered `CanonicalDatasetRelease` and lets its
class/geometry/eligibility/group/validation-membership invariants validate the
candidate. It persists exact membership, explicit `not_selected` exclusions,
ontology hash and a deterministic canonical snapshot digest in the existing
frozen-release row, atomically with the attributed retry/audit event. A matching
historical retry returns its receipt, not a new freeze against current policy.
Failure to insert the event rolls the release back. This creates no derivative,
starts no worker and cannot establish independent final-test qualification.
The runtime role also needs permission to lock source rows (UPDATE on at least
one source column; source identity mutation is still rejected by its DB trigger).

Actions runs `test_management_canonical_db.py` against the migrated disposable
Postgres service in the existing ML job. Cases cover concurrent same-key retry,
different-key stale-revision writers, workspace/actor isolation, event/storage
failure rollback, normalized pixels/provenance, and direct runtime-role event
mutation refusals. Additional freeze cases cover concurrent identical/different
retry keys, review-writer synchronization, unreviewed negatives, empty validation,
unlocked/obsolete assignments, stale annotation/scope, pending grouping reviews,
excluded relations, frozen-row mutation refusal, persisted exclusions and
audit-failure rollback. New cases are commissioned, **not passing evidence until
exact-head Actions completes**. These cases do not establish sessions/CSRF, full
setup lifecycle, grouping publication, fenced jobs or project-to-export.

## Canonical summary reads (partial, unmounted)

`CanonicalManagementReads` supplies `list_projects`, `list_resources` and `read`
for canonical projects, assets and dataset releases. Each entry point requires a
human admin/reviewer with review permission and constrains every query to the
trusted deployment workspace. Unknown and foreign IDs return the same not-found
response. Projects without a scoped management event are omitted pending explicit
adoption. This is a partial adapter; unsupported producer kinds fail unavailable,
not as an invented empty page or completed result.

Lists use bounded UUID keysets (1–100 entries), fetch at most one lookahead row,
and return a cursor within the published identifier bounds. Its scope tag binds
workspace, project and resource kind to prevent accidental cursor reuse. The tag
is not a signature, authorization or stable snapshot: the SQL query always applies
ownership independently, and concurrent inserts before the position require a new
traversal. No count-all query, unbounded response, offset walk or per-item query is
used. Project revisions come from attributed management events; asset revisions
come from current canonical annotation revisions. Asset review approval does not
claim dataset publication, and project state remains draft until setup publication
is implemented. Frozen dataset summaries report the actual frozen row state.

Only public identity, kind, revision and lifecycle state are projected. Source
URIs, object keys, provider payloads and audit evidence never enter summaries.
Reads need only SELECT and append no event, revision, job or retry receipt. Actions
Postgres cases additionally exercise reviewer reads with INSERT/UPDATE revoked,
current annotation revisions, frozen summaries, bounded traversal, cursor scope,
invalid limits/cursors, denied principals and foreign/legacy ID isolation. These
new cases require exact-head Actions results; no session/CSRF,
setup-publication or complete operations binding is established by them.

### Canonical freeze-plan read

`read_freeze_plan` authorizes the human reader and managed project in one
Postgres REPEATABLE READ, READ ONLY transaction. It selects the latest ontology,
annotation/completeness evidence and published assignment version, excludes
currently ineligible images with explicit reasons, and reuses the actual freeze
validator and core release invariants without taking source-row locks. Grouping
checks still include excluded sources. An empty selection, absent/ambiguous
assignment, pending group review or other failed prerequisite returns a null
payload and a failed canonical-freeze check, never fabricated membership.
Preparation is bounded to 10,000 project sources; larger projects need a future
explicit selection surface and return unavailable evidence rather than silently
truncating membership.

A ready plan contains the current project revision and exact selected annotation
revisions. It is evidence at one read snapshot, not a reservation or approval.
POST repeats validation under its existing project/source locks, including when a
later review did not increment the management project revision. No write grant,
audit event, retry receipt, queue, derived artifact or qualification is produced.
The passed check means canonical freeze eligibility only, never model quality or
independent final-test qualification. The adapter remains unmounted; other detail
reads, setup/publication and shared producer composition remain incomplete.

Disposable Actions cases cover the real plan-to-freeze handoff, SELECT-only
reviewer access, workspace/legacy and principal refusal, missing prerequisites,
explicit exclusions and pending grouping through an excluded image. A writer
committed while the read is open verifies snapshot consistency and absence of
source locks; POST must refuse that now-stale annotation revision. These cases
are commissioned, not verified until their exact-head Actions job passes.

### Frozen dataset-release details

`read_release` uses one REPEATABLE READ, READ ONLY snapshot and the same
human/workspace/managed-project authorization. It projects the release's exact
annotation revisions and locked assignment version, not current reviews or the
newest ontology. A later rejection does not rewrite historical membership. Class
counts and human/system object provenance come from those pinned annotations;
class support thresholds remain null. The previous release is selected by its
attributed management freeze revision, and changed assets/class deltas compare
frozen pins rather than wall-clock order or current labels.

New freezes record `canonical-training-eligibility-v1` in the append-only audit
receipt. This identifies the canonical eligibility checks, not a project quality
target, independent gold, publication or model qualification. A detail read fails
closed for a missing/ambiguous receipt, absent policy pin, mismatched recorded
snapshot digest or missing pinned membership. Legacy freezes without a policy pin
require explicit adoption; the reader does not silently assert one. It checks
receipt-to-release identity/digest consistency, not full snapshot rehashing.

Responses expose stable IDs, counts, review tiers, exclusions and authenticated
freeze attribution, never source URIs, object keys or the raw audit/retry payload.
A canonical snapshot digest is not a compiler manifest: manifest fields stay null
until that producer supplies an artifact. The explicit model check stays unknown.
The endpoint remains unmounted. Disposable Actions cases cover SELECT-only roles,
historical reviews, denied/foreign IDs, missing evidence, exclusions and class
count/revision deltas. New cases are commissioned, not a test-pass claim.

## Acceptance evidence still required

| Check | Contract preparation | Final integration evidence required |
| --- | --- | --- |
| Admin/reviewer/service isolation | Route matrix against injected principals | Real sessions, CSRF, service authentication and cross-resource reads/writes |
| Audit/revision/idempotency | Fingerprint/error/typed request cases only | Disposable Postgres parallel writers/retries; one event/job; immutable audit grants |
| Migration/role safety | No new migration or grant in this slice | Combined migration graph upgrade/downgrade/re-upgrade and runtime-role refusal tests |
| Canonical source/release | Uploaded bytes and revision pins in request | Actual normalized ingest, eligible freeze, exclusions and split membership |
| Shared queue | Typed async receipts only | Real producer, fenced lease/result publication, crash/retry recovery |
| Promotion | Delegating port, retained decision remains retained | Real qualification veto transaction plus independent 320/640 promotion/rollback |
| New project to export | No end-to-end PASS claimed | Actions CPU fixture creates project, ingests, freezes, compiles both sizes, trains, evaluates source frame and exports using real adapters |
| Generated API/client | Generation and fixture assertions commissioned | Exact-head artifact, checked-in combined schema/client, drift and TypeScript checks |

The fixture recording port does not store state or simulate a durable queue. Its
tests establish only transport/dispatch boundaries. They do not establish any of
the final integration checks above, independent domain gold or qualification.
No paid provider call, live database mutation or production deployment is needed
or authorized for this work.
