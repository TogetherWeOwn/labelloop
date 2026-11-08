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

`labelloop_api.main.create_app(management_operations=...)` is the opt-in server
composition boundary. It installs the existing authentication middleware and auth
routes exactly once, then binds management to `auth.current_principal`. No caller
can supply a separate principal resolver through this factory; the operations
resolver is trusted server code, never a request field or environment switch.
Human sessions use the same origin/CSRF proof as review operations. Service tokens
retain service identity and cannot enter management reads or mutations. The
factory does not supply an adapter, change grants or enable admission by itself.
The default `main.app = create_app()` still mounts **no management routes**.

Actions cases exercise this composition with the real SQL-backed auth store:
identity attribution, reviewer reads/admin refusal, forged-field rejection,
all four service roles, CSRF, expiry, revocation, current-role reload, unavailable
authentication and refusal before operations resolution. Recording-port cases
prove only the identity boundary. One disposable Postgres case additionally binds
real canonical project creation and reads under the existing restricted canonical
role, checks a single attributed receipt on replay, rejects changed-key content
and verifies logout. Its auth store now uses a **separate restricted NOLOGIN role**,
not the fixture owner. The owner connection seeds only synthetic principals and
configures disposable grants; neither request-time adapter uses it. This does
**not** prove production auth-role provisioning, full adapter composition, shared
queue dispatch or the project-to-export acceptance path. Newly added cases require
exact-head Actions verification; test source is not a passing result.

### Disposable authentication-role contract

The composition fixture exercises the delivered `AuthStore` without changing its
implementation, tables, migration history or production grants. Authentication
and canonical requests use distinct temporary roles and engines. The auth role
has no superuser, role-creation, database-creation or RLS-bypass capability and no
canonical-table privileges. Its grants are limited to:

| Table | Disposable auth runtime grant | Purpose |
| --- | --- | --- |
| `auth_principal` | SELECT; UPDATE(password_hash, failed_attempts, locked_until) | Read identity and current role; lock/recheck credentials; maintain lockout and password-parameter upgrades |
| `auth_service_token` | SELECT | Authenticate already-provisioned service credentials, never issue or rotate them |
| `auth_session` | SELECT, INSERT, DELETE; UPDATE(expires_at) | Issue/revoke sessions and take PostgreSQL FOR UPDATE locks for bounded expired-session cleanup |
| `auth_event` | INSERT only | Append attributed login/logout/denial evidence |

PostgreSQL requires UPDATE privilege on at least one session column for the
cleanup row locks; the narrow expiry grant is explicit, not a hidden table-wide
UPDATE. Session issuance/deletion is already trusted auth-store authority. The
fixture grants no principal INSERT/DELETE, role/kind/enablement changes, service
credential mutations, session-principal/CSRF updates or auth-event rewrites.
The immutable event triggers remain additional protection, not a substitute for
restricted grants. No principal, service token or deployment credential is
provisioned outside the disposable test database.

Actions cases assert direct forbidden statements fail with insufficient privilege,
not merely a constraint error. Missing login audit/session/lock grants must return
generic no-store 503 without cookies, preserving the principal counters, expired
sessions and audit rows atomically. Missing logout audit/delete grants preserve
the session and event history; the response does not falsely clear the cookie.
A CSRF-denial audit failure fails closed before canonical project creation.
The existing create/read/replay/CSRF/logout composition runs with the positive
grants above. These are commissioned cases, not verified results until exact-head
Actions passes. Final operational role provisioning, service-credential lifecycle,
perimeter controls, full producer composition and deployment remain separate gates.

| Method/path | Port | Result |
| --- | --- | --- |
| POST /projects | create_project | 201 resource |
| POST /projects/{project_id}/setup/scope | save_scope_draft | 200 draft resource |
| POST /projects/{project_id}/setup/guide | save_guide_draft | 200 draft resource |
| POST /projects/{project_id}/setup/classes | save_class_catalog_draft | 200 draft resource |
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
artifact containing `management.openapi.json`, `management-client.ts` and
`application.openapi.json`:

```sh
uv run python -m labelloop_api.management_codegen --output-dir <artifact-directory>
```

The management-only schema and client remain unbound adapter contracts.
The companion application schema comes directly from
`create_app(management_operations=unavailable_operations)`, combining the real
shared session routes, health routes and management contracts without a separate
router inventory. Generation resolves no auth store, storage client or operation;
it neither changes the default app nor exposes a schema/docs endpoint. Valid
human sessions still receive `management_unavailable` at the adapter boundary;
anonymous, service, role and CSRF guards remain enforced. This composed schema is
**not final producer wiring, production availability or acceptance evidence**.
Middleware-enforced session and CSRF policy is documented above; generated
OpenAPI alone does not prove or replace it. The existing auth client remains the
shared identity client; the generated management client contains no second auth
implementation.

Generation is deterministic and uses the pinned FastAPI/Pydantic versions;
no new generator dependency is introduced. The TypeScript client uses fixed
same-origin `/api` routes, encoded IDs, credentials, CSRF and idempotency headers,
no-store caching and refused redirects. It supplies no authorization of its own.
Response types are compile-time types, not permission to treat server/model data
as trusted domain state.

The tracked schemas are `apps/api/openapi/management.openapi.json` and
`apps/api/openapi/application.openapi.json`; the tracked client is
`apps/web/src/generated/management-client.ts`. Both the unit and web-build jobs
compare fresh generation with all three artifacts' exact bytes before tests:

```sh
uv run python -m labelloop_api.management_codegen --check
```

A missing or stale file fails without rewriting it. To update the publication,
generate into an empty artifact directory with `--output-dir`, then copy the
schemas and client to those tracked paths together. Do not hand-edit the outputs.
The initial schema publication comes from the detail-contract Actions artifact;
the client uses that schema with the corrected union renderer. The new head's
drift, lint, type and request tests must pass before that publication is verified.

The web job checks the **tracked** client rather than replacing it before lint,
typecheck and tests. It runs for Python changes as well as web changes, and the
CI aggregator requires it for both. A successful Python generator alone cannot
satisfy the client contract. Schema publication still does not qualify real
adapters or the full project-to-export path. No local suite is used as a substitute.

## Canonical transaction adapter (partial, unmounted)

`CanonicalManagementMutations` implements project creation, scope/guide/class-
catalog draft saves, original-image ingestion and eligible canonical dataset
freezing using the existing canonical tables, `ingest_canonical_asset` and
`canonical_event`; it is not a complete `ManagementOperations` implementation.
Draft saves clear dependent approvals and automatic acceptance, and block new
freezes until the separate approval/publication path is implemented. Catalog
intent is pinned to the current ontology, stored separately in the existing
project policy and exposed as `class_catalog_draft`, never as released `classes`
or export mappings. Prose examples are not approved reference evidence. See
[the draft contracts](management-contract.md) for bounds and hierarchy validation.
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
approval/publication are not implemented by this class, so it cannot dispatch
paid work or claim queue/qualification semantics.

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

## Shared queue transaction composition (preparation, unmounted)

`JobStore.enqueue_in_transaction(connection, ...)` accepts an already-active
psycopg transaction and appends intent to the **existing** `worker_job` table.
It opens no connection and does not begin, commit, roll back or close the caller's
transaction. Idle connections (including autocommit connections) are refused.
The existing standalone `enqueue` wraps this same implementation in its own
transaction, preserving one input-validation/replay path. Project/operation
uniqueness, canonical JSON comparison and the bounded retry budget are unchanged;
claiming, fenced leases, stage artifacts and result publication are untouched.

This is the producer prerequisite for committing management state, attributed
canonical events and async intent together. The existing standalone enqueue is
**not** safe to call after a management commit and describe as atomic. The final
adapter must supply the same database transaction, retain authenticated ownership,
aggregate/expected-revision and actor-scoped retry checks, and let any enqueue,
audit or state failure roll the entire transaction back. This helper grants none
of that authority and does not resolve a handler, endpoint, credential or spend
from request/model data.

Disposable Actions queue cases cover state/event/job visibility before commit,
commit versus caller/audit-failure rollback under both connection autocommit modes,
existing-worker consumption, replay normalization, kind/input/budget conflicts,
concurrent unique-key callers, idle-connection refusal and unchanged invalid-input
controls. These new cases are commissioned, not verified until exact-head Actions
completes. No new queue/table/migration/runtime grant, management async binding,
worker handler, default route mount or project-to-export acceptance is enabled.

### Canonical compilation producer (partial, unmounted)

`CanonicalManagementCompilation(mutations, enqueue)` composes the canonical
mutation authority with the existing queue's `JobStore.enqueue_in_transaction`,
supplied as a trusted server dependency. The adapter passes SQLAlchemy's actual
psycopg driver connection while its transaction is active. It does not open a
second connection or invoke standalone enqueue. Temporary disposable-DB producer
roles need SELECT/INSERT on `worker_job` in addition to the existing canonical
read/event grants; no queue UPDATE/DELETE, worker lease or audit rewrite grants
are required. Production provisioning remains separately unproved.

`compile_variant` requires an authenticated human admin, a bound actor/route/body
fingerprint, workspace ownership and the immutable release revision. It takes the
shared project lock, validates the recorded canonical freeze and pinned members,
then resolves one of the two delivered full-frame compiler recipes in code.
The queue input pins workspace/project/release/revision, canonical snapshot hash,
ontology, assignment version, complete recipe, variant identity and requester.
No endpoint, output path, provider or spend setting comes from the request.

One release/recipe pair has one producer intent across actors and retry keys.
An identical actor/key/body replays the original accepted resource/job, including
after a worker claims the job; changed bodies conflict. Another key for an
already-requested pair conflicts rather than dispatching another job. The two
recipes remain independent. The attributed receipt and queue row commit/roll
back together, and compilation intent does not advance the project revision or
claim that a validated derivative already exists. The release is the receipt's
aggregate; its immutable revision remains one.

Actions cases exercise the real SQLAlchemy transaction and existing queue under
restricted grants: precommit invisibility, both recipes, worker claim/replay,
queue/audit/caller rollback, concurrent retries/unique pairs, revision/key/actor
and ownership refusals, missing freeze evidence and independent recipe intents.
New cases are not verified until exact-head Actions completes. This is a producer
binding only; enqueue/claim tests do not prove compiler execution. Progress/detail
reads and final shared-session app composition remain required before live mounting.
There is no new table, migration, dependency, scheduler or production grant.

### Canonical compiler handler and fenced publication (partial, unmounted)

`CanonicalCompilationWorker` supplies a trusted `compile` stage and `publish` sink
for the existing `JobRunner`, not another runner or scheduler. Trusted server
composition binds kind `compile_variant`, `Stage("compilation", worker.compile)`
and `worker.publish`; there is no default worker registration or application mount.
The adapter uses structural queue/lease ports and adds no package dependency.

Before reading objects, the stage checks workspace/project ownership, the actual
job input, the human-attributed producer receipt and canonical freeze receipt.
It rehydrates the exact historical ontology, annotation revisions and locked
assignments for selected and excluded sources, bounded to 10,000 members, then
recomputes the complete canonical snapshot and ontology hashes. Later labels,
ontologies or ingests do not silently replace frozen evidence. Recipe policy is
resolved and compared against the delivered code-owned 320/640 definitions.

The delivered compiler reads each normalized original by its pinned object key,
checks its source hash and produces all files in a worker-owned temporary directory.
The adapter uploads each verified file plus the complete manifest under its byte-
derived content key. The existing queue's 2 MB inline-artifact bound is checked
before uploads. Temporary files are cleaned on success and failure. There is no
request-provided output path, endpoint, backend, provider or spend. Objects are
not transactional: failures may leave immutable unreferenced bytes, never a
successful canonical variant. Storage durability/retention is deployment-owned.

`JobStore.complete` owns the sink transaction and checks the current lease before
and after it. The sink rechecks job/receipt/snapshot/recipe authority and manifest
lineage, then inserts the immutable recipe (or verifies an identical existing
one), validated variant, derived source transforms/content hashes and one system-
attributed completion event with requester, job and generation pins. Existing
release/recipe uniqueness and queue application fencing remain authoritative.
The sink commits nothing and performs no object or filesystem operation; failure,
cancellation, expiration or a superseding generation rolls publication back.
Saved stage artifacts resume through the existing runner without recompiling.

Disposable test worker roles add SELECT/INSERT on `preprocessing_recipe`,
`yolo_dataset_variant`, `derived_sample`, `worker_stage_artifact` and
`worker_application`, plus UPDATE on `worker_job`, to the canonical/producer test
grants. Canonical event UPDATE/DELETE/TRUNCATE authority is refused at publication.
No production role is created or widened. Actions cases cover real 320/640
compilation, positive and negative labels, content hashes and source transforms,
historical pins, precommit invisibility, missing-grant/caller/deadline rollback,
expired/cancelled/superseded leases, artifact resume, storage retry, corrupt source
bytes, unattributed jobs and forged input/stage lineage. New cases are commissioned,
not verified until exact-head Actions completes. Training/evaluation/export,
operational grants, final mounts and actual project-to-export acceptance remain
incomplete; neither this handler nor its fixture claims model qualification.

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

### Canonical project-setup details

`read_project` uses one REPEATABLE READ, READ ONLY snapshot after the same
human/workspace/managed-project authorization. It projects current scope/domain,
source permitted use, allowed provider IDs, the original free-text annotation
guide and the latest canonical ontology with its stable IDs, aliases and parent
links. Core ontology/class-map validation is reused; missing policy or invalid
ontology evidence returns `missing_evidence`. The saved timestamp is the unique
attributed event at the current management revision, not an inferred setup approval.

The bootstrap pack has no display name, representation, intended use, specificity,
structured guide or setup approval record. Those fields are **null**, not invented
from scope/domain or defaults. Class approval, mirror safety, exclusions and
positive-reference support also remain null. References stay null until their
provenance/approval producer is implemented; null does not mean an empty reference
bank. Arbitrary policy JSON, provider payloads and un-attributed approval-shaped
fields are not reflected. A created ontology is not an approved/published project.

Source counts and pending pair public IDs come from canonical rows. Group/split
counts require one complete, locked, policy-consistent latest assignment version;
missing, partial, unlocked or tied publications return null counts/version. Counts
are descriptive, not a grouping-validation or release-readiness pass. Pending
pairs remain visible; a relation to a source outside the authorized project fails
closed without revealing that endpoint. Source and pending-pair lists are bounded
to 10,000 each; larger projects return unavailable, never silently truncated data.
Configured metadata keys remain unknown until setup publication supplies them.

The isolated OpenAPI/client now distinguish those nullable facts and carry
`annotation_guide` separately from the structured `guide`. Consumers must render
unknown values as missing evidence, not approval or false/zero defaults. This
adapter remains unmounted; draft writes, explicit approvals, reference provenance,
grouping publication and project-release publication are still unimplemented.
Disposable Actions cases cover SELECT-only access, missing evidence, denied and
foreign/legacy IDs, current ontology/split facts, pending pairs, ambiguous
assignments and a concurrent ingest proving snapshot consistency without locks.
New cases are commissioned, not passing evidence until exact-head Actions finishes.

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

## Reference lifecycle recovery (partial, unmounted)

`read_reference_lifecycle` exposes a separate, authorized historical summary through
`GET /projects/{project_id}/references/lifecycle`. It reuses the receipt-chain
validator used by explicit renewal and retirement, under the existing workspace/
managed-project scope and a REPEATABLE READ, READ ONLY snapshot. It returns at most
1,000 source/class identities, historical lifecycle/pin values and the last
approval revision alongside the project revision. Every entry explicitly says
`eligibility: not_assessed`; it cannot supply `ReferenceRead` or approved retrieval
evidence. It exposes no provenance text, source URI, object key, raw audit payload,
context digest or actor identity.

This supports explicit admin recovery even when a stale accepted reference makes
the normal project detail fail closed. History corruption still fails; current
scope/grouping/holdout validation is unchanged for normal reads and renewal.
Withdrawal still requires its existing locks, admin/revision/retry authority and
atomic receipt. The read takes no mutation lock, writes no event and needs only
SELECT. Concurrent retirement cannot mix revision and receipt snapshots. Actions
cases cover all lifecycle states, renewal-pin recovery, stale-context/source
retirement, denied/foreign/legacy readers, persisted mismatch and SELECT-only/
concurrent-writer behavior. These are commissioned cases, not verified until
exact-head Actions completes. No pack publication, real admission, qualification,
new table/migration/grant, final mount or producer composition is enabled.

## Receipt-bound setup approval

`POST /projects/{project_id}/setup/approvals` (`approveSetupStep`) accepts
`scope`, `guide` or published `classes`, an expected project revision, a server-projected
`setup_draft_sha256[step]` and a reason. The digest covers the validated saved
draft, excluding its transport revision. The browser echoes the digest; it does
not determine what content is approved. The adapter uses the existing admin,
workspace, project/retry locks and append-only event transaction. Replays return
the original receipt; stale revisions, changed digests, absent drafts and second
approvals under a new key fail closed.

Project reads report approved steps only after checking the stored digest/revision
against the attributed approval event and current content.
A later scope, guide, class, source-policy or reference mutation clears all setup
approvals. Even restoring identical old approval bytes after such a mutation
cannot resurrect approval: the read checks the latest invalidating event.
Approving one intent step preserves another current intent approval, removes
any automatic-acceptance qualification and leaves the project in `draft`.

For `classes`, the content must be the latest receipt-verified immutable catalog,
with no pending catalog draft. Bootstrap classes alone are insufficient. The
server projects the published catalog digest under `setup_draft_sha256.classes`
after publication, preserving the wire field name; while a draft exists that pin
is for publication only and cannot approve classes. The adapter also checks later
catalog-save events, so removing pending policy bytes cannot permit approval of
the superseded publication. Expected revisions and shared project locks serialize
approval against edits/publication. Approval covers class definitions, not example
support: current class reads expose `admin_approved` only while this approval is
valid, and receipt-backed exclusions/mirror-safety settings only without a pending
catalog draft. Prose positive examples never become approved asset counts;
`positive_examples` remains null. Later edits clear the approval even if content
bytes are unchanged. An approval of scope or guide can compose with the current
class approval without resetting its receipt.

This is **not** evidence-pack acceptance, source-group qualification, pilot
acceptance or project publication. Those steps cannot be selected by this command.
New freezes still refuse unpublished draft policies;
no bulk-job release, completeness, recipe, retrieval, promotion or spend authority
is enabled. There is no new table, migration, runtime grant or approval store.
The facade remains unmounted; the combined auth/CSRF composition is still pending.
Disposable Actions cases cover digest/revision/retry pins, races, attribution,
grant rollback, malformed/privileged inputs and invalidation. Commissioned cases
are not a pass claim until exact-head Actions completes.

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
