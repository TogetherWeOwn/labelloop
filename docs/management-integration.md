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

### Opt-in canonical operations assembly

`labelloop_api.management_operations.CanonicalManagementOperations` is a trusted
server-side assembly for the delivered adapters, usable as
`create_app(management_operations=lambda: operations)`. It takes one canonical
engine, object store and workspace UUID plus immutable, bounded project-scoped
training-profile and development-protocol tuples. Construction does no database,
object or provider I/O. Existing canonical create/setup/reference/freeze methods
are inherited unchanged. Explicit read and async methods delegate to the existing
snapshot validators and transaction adapters; the latter always use
`JobStore.enqueue_in_transaction`. No request, environment switch or generic
callback selects a queue, workspace, profile arguments or execution engine.

This assembly is **not final production binding**. Asset admission explicitly
returns `management_unavailable` after authorization; the independently tested
canonical ingest adapter is not enabled here. Promotions and registry projection
also remain unavailable pending consumption of the single qualified registry
producer. No alternate registry/qualification policy is implemented. Default
`main.app` remains unmounted, and no runtime role, grant, migration, worker startup,
provider call, setup publication or deployment is introduced.

New disposable Actions cases commission shared restricted-session/CSRF-backed
freeze → compile → native compiler publication → training intent at both sizes,
plus completed synthetic model/training/export HTTP reads and development
submission at both sizes. They check replay, actor attribution, queued/completed
job observations, reviewer mutation refusal, unchanged denied-operation state and
workspace isolation. Source admission, annotations/grouping and ML artifacts are
explicit fixture preparation outside this HTTP slice. These cases do not prove
real admission, actual HTTP-to-CPU training/evaluation completion, full project
publication, registry composition or model quality. Unit delegation/configuration
cases prove wiring only; exact-head Actions must verify all new cases.

### Initial source-pair discovery (opt-in, not mounted by default)

`CanonicalInitialGrouping.discover` persists code-computed borderline suggestions
in the existing pair-review table and an attributed canonical receipt. Discovery
and first assignment publication share one bounded preparation path: current
ontology/source-policy/annotation pins, project/source locks, canonical image
hash/decode checks, declared metadata and historical-assignment/reference/pair
refusals. The unchanged core grouping function computes scores and thresholds;
no caller chooses a score, threshold, split or human decision. Discovery assigns
no groups and writes no automatic relations. Exact retries replay the receipt;
competing requests use the existing project revision and idempotency fences.
Pair inserts, setup-approval/automatic-acceptance invalidation and the receipt
commit or roll back together. Receipt-history invalidation includes discovery.

The opt-in `discoverInitialSourcePairs` HTTP command reuses this producer through
an explicit trusted grouping reader and the shared human-admin/session/CSRF boundary.
Its strict source-pin request accepts no scores, endpoints, thresholds, decisions or
splits. Missing reader configuration refuses. Existing reads and `resolveSourcePair`
can observe and decide its pending suggestions; this is not a complete grouping
lifecycle. First assignment publication consumes resolved suggestions
only with one attributed discovery, one complete human decision receipt per pair,
exact request fingerprints/results/revisions, current source/annotation/policy and
toolchain pins, and the identical freshly computed review band. Related decisions
must retain their exact persisted conservative edge. Unrelated decisions dismiss
only that suggestion; automatic, declared and transitive relations still constrain
the groups. The publication receipt and assignment identity bind the consumed
receipts. Pending, foreign, legacy unreceipted, duplicate, corrupt or stale evidence
refuses; any assignment/reference history still refuses before object I/O. No
historical holdout is regrouped, no quarantine restriction is relaxed, and real
admission/default mounting remain disabled. No new table, migration, dependency,
grant or scheduler is introduced. Management/application OpenAPI and the generated
client publish the typed discovery operation.

Five covering unit tests exercise core review-band boundaries, pending-only
inserts, unchanged observations, preparation refusal and receipt invalidation.
Eighteen disposable PostgreSQL cases cover attribution/replay, both existing human
decisions, empty discovery followed by first grouping, privilege rollback/recovery,
races, nonadmin and current-pin refusals, assignment-history protection and
workspace/context isolation. Their deterministic review-band observations are
explicitly synthetic pHashes after real canonical byte validation, not a claim
that the fixture images naturally have that distance. Nine additional covering unit
cases exercise decision consumption and conservative-edge preservation. Both
existing decision database cases now continue through first publication and replay;
ten additional PostgreSQL cases check pending, mutated pair, absent related edge,
actor/workspace/fingerprint/body and source/toolchain evidence refusal with unchanged
state. New and expanded database cases await exact-head Actions; local unit output
is not database evidence.

### Deployment-owned runtime composition (source preparation)

`management_runtime.api_operations`, `compiler_dispatch` and `local_ml_dispatch`
assemble existing adapters from explicit caller-owned PostgreSQL engines/job stores,
MinIO clients, bucket/cap, workspace UUID and immutable server configuration. They
perform no connection, object or backend I/O during construction. Each process
constructs only its own lane; there is no all-capabilities runtime container.
Deployment code retains connection/client lifecycle and must supply the independently
provisioned least-privilege roles. These factories neither inspect nor establish
SQL/storage IAM isolation and never read deployment credentials or select endpoints.

The canonical mutation boundary and all three worker publication boundaries reuse
one effective audit-write query. It checks table UPDATE/DELETE/TRUNCATE, UPDATE
on any audit column and effective audit-table ownership before mutation/publication.
Revoking DML privileges from an owner does not remove its ability to alter table
protections or regrant privileges. `pg_has_role(current_user, relowner, 'USAGE')`
rejects direct ownership and immediately inherited owner authority, even when both
DML privilege checks are false. Four unit query/wiring regressions and disposable
PostgreSQL cases cover column/table grants, direct/inherited ownership with revoked
DML, unchanged refused publication state and mutation recovery after restoration.
Ownership tests restore the original owner and restricted fixture grants in `finally`;
they run only against disposable Actions PostgreSQL. Exact-head Actions must verify
the new ownership controls. This remains a narrow refusal guard, not a complete role,
SET ROLE membership, DDL, storage-IAM or credential audit. It does not provision or
alter any live grant; deployment must still enforce independent least privilege.

The checked-in Compose stack remains local development scaffolding: it shares
bootstrap database/storage settings, starts the default unmounted API, and parks
worker processes rather than dispatching management jobs. Do not deploy that stack
as a least-privilege management installation. An operational binding still needs
independent auth/API/worker connection identities, restricted storage capabilities,
trusted per-lane composition and separately accepted admission/mounting evidence.
Any staging/preview binding must also inventory every billed runtime, start on
demand with SHA-bound readiness and a bounded use window, and retain native
stop/scale-to-zero plus failure/cancellation cleanup evidence. No always-on staging
runtime, keep-alive, alarm, schedule or new recurring spend is authorized here;
production is unchanged.

The API assembly gives grouping only a frozen `read(key)` port backed by the bounded,
content-addressed MinIO reader. Its separate admission store always refuses writes;
asset admission and registry operations remain unavailable. This narrows the exposed
application port, not the underlying client's IAM authority or a Python sandbox.
Do not supply a root storage client in a real deployment. Compiler composition has
no ML callbacks, API operations or SQLAlchemy engine. Local-ML composition receives
only its supplied execution engine/store and trusted backend/export/predict callbacks;
it does not import ML libraries into the API package. Existing dispatcher ownership
preflight, specific-job selection, stages, retries and transaction fencing are unchanged.

Construction grants no mount or startup authority. The caller still uses the single
shared-session `create_app` boundary when separately admitted, and must keep auth
connections separate from canonical API and worker execution connections. Default
`main.app` remains unmounted. No queue scan, scheduler, credential, grant, bucket,
provisioning, installation or production change is introduced. Zero-idle staging/
preview and independent Security/QA/release gates remain binding.

Twelve bounded unit cases cover lazy construction, read-only API port/refused writes,
actual bounded-reader delegation/cleanup, explicit profiles/native enqueue, lane
shape, bounds/workspace/backend refusals and unchanged default mount. The existing
320/640 disposable Actions cohorts now consume all three factories with their
restricted roles and live MinIO; no extra CPU cohort is added. Their source records
runtime-composition provenance, retains SQL refusal/ownership/replay/cross-lane and
cleanup checks, and **awaits exact-head Actions**. This is not production auth/SQL/
storage provisioning evidence, complete bootstrap, quality or real asset admission.

### Verified opt-in HTTP-to-CPU slice

Exact head `e747622b376dec8ace382527f6583685c4e8c72e` passed
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42758177591).
The [DB/CPU job](https://github.com/TogetherWeOwn/labelloop/actions/runs/42758177591/job/129637394489)
passed 689 management DB cases, 24 ledger cases and 21 CPU cases. The
[unit job](https://github.com/TogetherWeOwn/labelloop/actions/runs/42758177591/job/129637394568)
passed 1,764 tests with 790 skipped. Queue, migration, contract drift, web,
dev-stack, lint, type and import checks passed; CodeQL and gitleaks also passed.
These exact-head results supersede the commissioned-only language for existing
cases above, not for later changes or full production integration.

The [retained CPU artifact](https://github.com/TogetherWeOwn/labelloop/actions/runs/42758177591/artifacts/13735794828)
contains both 320/640 `management-pipeline.json` cohorts. Restricted shared-session
HTTP create/freeze-plan/freeze/compile/train/evaluate, actual fenced workers,
completed publication/detail/export reads, replay, CSRF, role reload/reviewer
mutation refusal and logout completed. Both cohorts have four validation sources
and four nonempty ONNX comparison samples, matching weights/export digests and
static `[1, 3, size, size]` signatures. Both have **zero recall**: this proves the
execution/equivalence slice, not model quality, independent gold or qualification.
Admission and annotation/grouping are explicitly seeded canonical fixture setup,
not HTTP producer acceptance. Default mounting, effective project publication,
production role provisioning, operational dispatch and registry composition remain
incomplete; real admission retains its exact-consuming-head Security gate.

### Configuration-bound HTTP-to-CPU acceptance expansion

The existing two CPU pipeline cases now commission HTTP setup drafts, identity-
preserving class publication, source-bound reference acceptance, anchor-pack
publication, all five setup approvals, configuration snapshot/read and explicit
local activation before freeze → compile → train/export → development evaluation.
The initialization digest and recipe-matching inference policy are trusted server
profile settings pinned before snapshot publication. A valid client pin alone
cannot activate a configuration: the test first requires refusal without the
separate server binding, then activates that exact version/digest. Freeze planning
remains unavailable before activation and exposes the same pin afterwards.

The tests require the freeze receipt, immutable training job input and attributed
training intent to carry the exact configuration provenance. All HTTP mutations
exercise CSRF refusal and idempotent replay; reviewer-role reload retains summary
and artifact reads but refuses training mutation. The existing pipeline JSON now
records snapshot identity/summary, local publication and the consumed freeze/job
pins alongside actual CPU/export/evaluation evidence. No extra training cohort is
added. Exact head `dfbbe15dc33751fc510e9a0d7eff0097b0b1a5f0` passed
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42858467894):
[939 management DB, 24 ledger and 21 CPU cases](https://github.com/TogetherWeOwn/labelloop/actions/runs/42858467894/job/129977909077),
plus unit, queue, web/drift, dev-stack, migrations, lint, types and import checks.
CodeQL and gitleaks passed. The
[retained artifact](https://github.com/TogetherWeOwn/labelloop/actions/runs/42858467894/artifacts/13783433985)
contains both configuration-bound pipeline records: matching publication/freeze/
training pins, four validation sources and nonempty ONNX comparisons at each size,
matching checkpoint/export hashes and static signatures. Both cohorts have zero
recall: execution/equivalence is not model quality or independent gold.

That verified head uses explicit licence-clean canonical admission, annotation
and grouping fixtures, not their HTTP producers. The expansion does not prove
complete bootstrap, quality, retrieval, operational role provisioning/dispatch,
registry composition or default app mounting. Default management and real admission
remain disabled under the retained Security gate.

### Initial discovery/grouping-to-CPU acceptance expansion

The same two CPU cases commission `discoverInitialSourcePairs` before the existing
`publishInitialSourceGrouping` HTTP operation against actual normalized original
rasters, before references and configuration publication. No split assignment,
similarity relation, pair row or discovery/grouping receipt is seeded. The fixture
has no borderline pairs under the unchanged core thresholds: discovery must leave
assignment/relation/pair tables empty, append exactly one session-attributed receipt,
and expose an empty pending-pair read with no assignment version. CSRF and exact
replay use the same HTTP helper as the other commands. A publication with the
pre-discovery revision must refuse without writes; a fresh revision then publishes
all 16 locked assignments, 15 independent groups and train/validation membership.
Grouping alone still cannot freeze an unpublished setup.

Publication must recompute the same canonical source observations, metadata,
annotation/policy/ontology pins and toolchain as discovery. Reference enrollment
selects training sources from actual persisted assignments, not the fixture's
previous split oracle. Freeze/manifest assignment versions, every compiled source's
group/split and validation report membership/support must match those assignments.
The pipeline artifact retains the discovery result/receipt ID, empty review band,
thresholds, revision refusal and no-assignment-write observations, plus the grouping
version, source-policy pin, toolchain and actual membership. No extra CPU cohort,
new endpoint or production behavior is introduced. This discovery-to-CPU expansion
**awaits exact-head Actions**, not a claimed pass.

Admission and annotations remain explicit canonical fixture preparation, not HTTP
producer acceptance or independent gold. This no-borderline fixture does not prove
measured borderline quality or human pair decisions followed by CPU execution;
separate database decision cases use explicitly synthetic band observations after
canonical byte validation. Regrouping/quarantine, full bootstrap and quality
qualification remain unproved. Operational roles/dispatch, registry composition,
default mounting and the existing real-admission Security gate are unchanged.

### Explicit internal local configuration publication

`management_configuration_release.CanonicalLocalConfigurationRelease` takes an
explicit canonical snapshot producer and immutable version/digest pin. Its
`publish` command rechecks the current approved setup, source-group evidence and
trusted local profiles under the canonical project/source locks, then appends an
attributed `publish_local_configuration` receipt. The receipt fixes runtime to
`local_ultralytics`, qualification to `not_assessed`, automatic acceptance to
false and promotion targets to null. It creates no provider or spend authority.
A snapshot alone remains preparation, not activation. A local publication version
cannot be reused under a different key; an exact retry acknowledges history.

Only this explicitly bound internal adapter can freeze an edited setup. It
rechecks both the latest intact local publication and the complete current
configuration before calling the existing canonical freeze persistence. Missing,
stale, foreign or ambiguous evidence refuses; no fallback to an older activation
is allowed. The existing eligibility, completeness, geometry, ontology,
assignment, expected-revision, retry, lock and atomic audit gates still apply.
The frozen release receipt retains the selected configuration pin. Historical
freeze replay does not renew publication or current eligibility.

New Actions cases commission real canonical freeze → fenced compiler execution
at 320/640 → configuration-bound training enqueue, with immutable input/audit
pin equality and replay. They seed fixture annotation/grouping explicitly and
stop before actual CPU training. Publication/freeze audit rollback, stale
setup/profile/pin, nonadmin identities, races, duplicate versions and corrupt
publication controls are also commissioned. Pure receipt tests are not database
acceptance; exact-head Actions must verify these new cases.

This slice does **not** mount a publication route, supply an HTTP freeze plan or
wire local activation into `CanonicalManagementOperations`. Ordinary mutation and
read adapters retain their existing draft refusal; project details still report
no published release ID. Full public project-release identity/version projection,
configuration-bound HTTP-to-CPU acceptance and production runtime provisioning
remain incomplete. Default management and real asset admission stay disabled;
no table, migration, grant, dependency, worker startup or registry change occurs.

### Anchor-publication approval invalidation

Exact head `66d8a2b6ddb4a1c565bc6e6649c8175284f15fcf` passed
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42800627598):
[833 management DB, 24 ledger and 21 CPU cases](https://github.com/TogetherWeOwn/labelloop/actions/runs/42800627598/job/129776946586),
[1,916 unit tests with 934 skipped](https://github.com/TogetherWeOwn/labelloop/actions/runs/42800627598/job/129776946444),
and the remaining CI, CodeQL and gitleaks checks. This verifies the typed
anchor-publication operation, not complete setup or production integration.

Subsequent source inspection found that publication cleared setup approvals but
was absent from the approval reader's invalidating-event query. Restoring an old
policy could therefore revive an approval predating the publication. The query
now includes `management.publish_anchor_pack`; receipt history, not a mutable
policy marker alone, determines whether approval remains current. A focused
query regression failed before the correction. Three new disposable PostgreSQL
cases exercise scope/guide/source approvals, historical retry acknowledgment,
restored-policy refusal, fresh approval and later publication replay without
invalidating that fresh approval. Exact `545a194ab1c25fe3cca7030ce7b60ece578c8be4`
[Actions CI passed](https://github.com/TogetherWeOwn/labelloop/actions/runs/42803435363),
including these regressions, CodeQL and gitleaks. This verifies the invalidation
repair, not the summary-read increment below or complete project setup.
No freeze guard is relaxed and no project publication, retrieval, admission,
runtime grants, worker startup or default mounting is enabled by this repair.

### Current anchor summary (opt-in, not mounted by default)

`readAnchorPack` composes the existing current-pack verifier inside one authorized
SELECT-only repeatable snapshot. The caller explicitly pins pack ID and version;
only identity, canonical snapshot digest and example counts cross the typed port.
The response always says `qualification: not_assessed`. It exposes neither the
internal snapshot/lineage nor an object read, URL, retrieval or durable eligibility
grant. Reference withdrawal, changed scope/annotation/completeness/grouping and
corrupt/duplicate receipts fail closed; no historical fallback or renewal occurs.
Project/workspace ownership is resolved before pack lookup. Shared sessions allow
admin/reviewer reads; service roles cannot read and reviewers still cannot publish.
The default app and real asset admission remain disabled.

Existing disposable DB tests now cover public summary refusal alongside the core
verifier's stale/corrupt evidence controls, exact version selection, SELECT-only
access and project/workspace isolation. The restricted-session publication case
also reads the exact digest summary, reloads the reviewer role, checks publication
refusal and checks summary denial after logout. Pure projection/schema tests and
generated-client path encoding cases cover the new read contract. Exact summary
head `f58c22ac68e03861627ec526fdda7b2546fa59bf` passed
[Actions](https://github.com/TogetherWeOwn/labelloop/actions/runs/42806950435),
CodeQL and gitleaks. This verifies the summary, not full setup publication.

### Explicit evidence-step approval (opt-in)

The existing setup approval command now accepts a named current human-anchor pack,
its version/ontology/assignment pins and the verified summary's snapshot digest.
The canonical mutation uses project and source locks with existing revision/retry/
audit authority. Read-only projections independently revalidate the receipt's pack;
no prior summary response is a durable eligibility grant. Reference evidence loading
is factored beneath setup approval to avoid recursive self-validation, while public
reads retain setup checks. Old intent requests omit the new optional field when
serialized, preserving historical idempotency hashes.

Fourteen new disposable DB cases cover attribution/replay, explicit pin substitution,
nonadmin refusal, competing revisions, grant rollback, later annotation/publication
invalidation, and restricted shared-session/CSRF/reviewer/logout composition. Pure
contract/receipt and generated-client cases cover the new shape. These changes await
exact-head Actions. Effective project publication, runtime roles/dispatch, single
registry composition and final mounting remain incomplete. This approval grants no
pilot quality, retrieval, automatic acceptance or freeze clearance; default mounting
and real admission remain disabled.

### Internal configuration snapshots (not executable release clearance)

The approved-setup summary and immutable-source fixture repair passed on exact
`38fe9fb0de1af0e4f40995de3cab72148865bb3e`:
[Actions](https://github.com/TogetherWeOwn/labelloop/actions/runs/42823386009), including
[876 management DB, 24 ledger and 21 CPU cases](https://github.com/TogetherWeOwn/labelloop/actions/runs/42823386009/job/129854464831).
CodeQL, gitleaks and the remaining CI checks passed. This verifies that earlier
summary, not the configuration preparation below or complete bootstrap acceptance.

`CanonicalConfigurationSnapshots` now prepares bounded, versioned, attributed
configuration records inside the existing canonical event transaction. It requires
all five current approvals, the explicit setup-summary digest, current locked source
grouping, and project-scoped immutable **server** training profiles. It records scope,
guide, source policy, ontology/assignment and approval receipt pins, source identity /
group / split / algorithm / thresholds, resolved recipe definitions, checkpoint hashes,
training arguments and inference postprocessing. Records pass through the existing
canonical JSON encoder and are capped at two megabytes and 1,000 sources; histories
are bounded and versions cannot be reused. Shared admin/project/source/retry/revision
locks and atomic attributed audit remain the sole write authority. Exact retry is a
historical acknowledgment, not current configuration or evidence renewal.

This is internal preparation: no public operation, schema/client change, effective
release pointer, freeze clearance, job/worker registration or runtime consumer is
installed. The result remains `draft`; qualification is `not_assessed`, promotion
targets and runtime binding are null, and automatic acceptance, autonomous spend and
external-provider access are disabled in the snapshot. It does not apply or renew
those capabilities in live project policy. Later edits leave immutable history intact
but invalidate current setup. No new table, migration, dependency, production grant,
object I/O or default mounting is introduced.

Eight pure contract/preparation tests cover profile ordering, project ownership,
bounds, checkpoint/argument/postprocessing digest sensitivity, strict authority-field
refusal, JSON-native independent copies, fail-closed incomplete evidence and explicit
transaction-context propagation. Twelve disposable DB cases commission exact replay,
current setup/source/profile pins, immutable historical records after edits, unchanged
freeze refusal, three stale-pin / version cases, nonadmin refusal, audit-grant rollback
and competing revisions. Race assertions require actual successful resources, not
merely two identical failure strings.

Initial snapshot head `9cbed9d03691b3a6634163d8fb7a5570ec0ec7fc`
[failed Actions](https://github.com/TogetherWeOwn/labelloop/actions/runs/42826943831):
[6 management DB failures / 882 passed](https://github.com/TogetherWeOwn/labelloop/actions/runs/42826943831/job/129866706010).
All six stop at the same transaction-context mismatch: preparation holds canonical
project/source locks under READ COMMITTED, but its composed project-detail read
selects the snapshot-only pack-verification path. Ledger/CPU cases did not run;
artifact upload failed downstream. [1,955 unit cases / 989 skipped](https://github.com/TogetherWeOwn/labelloop/actions/runs/42826943831/job/129866706706),
remaining CI jobs, CodeQL and gitleaks passed. This is partial evidence, not a
configuration-publication or full-bootstrap pass.

The repair passes the existing source-lock context through project details to both
setup-approval and reference reads. Normal authorized reads still default to the
SELECT-only repeatable snapshot; mutation callers retain the existing project/source
lock requirement. Neither pack-verifier isolation guard changes. Unit regressions
failed before repair and passed after it. Exact repair head
`3f94f4d1663cd745e092bda0449e3b290864c4b2`
[passed Actions](https://github.com/TogetherWeOwn/labelloop/actions/runs/42830205836),
including [888 management DB, 24 ledger and 21 CPU cases](https://github.com/TogetherWeOwn/labelloop/actions/runs/42830205836/job/129878024109).
CodeQL, gitleaks and remaining CI jobs passed. This verifies snapshot publication,
not full bootstrap or the current-verification extension below. No executable
publication/freeze clearance, schema/client, history, migration, grant, dependency
or admission change is made.

`CanonicalConfigurationSnapshots.read_current` now accepts an explicit immutable
version and snapshot digest at the internal authorized boundary. It reads owned
project/revision, bounded unique version history, current five-step setup/source
and grouping evidence, and the current trusted server profiles in one SELECT-only
REPEATABLE READ transaction. Preparation retains its READ COMMITTED/source-lock
path for mutation callers; snapshot readers never take those locks. Verification
checks human attribution, workspace/project/result/revision, reconstructed request
fingerprint, stored digest, canonical JSON bytes and freshly prepared current
content. A rehashed stored claim is not current evidence. Missing, malformed,
ambiguous, stale or mismatched records fail closed; historical mutation replay
never renews them. Explicit older versions remain readable only when all their
pinned facts are still current.

Pure contract tests cover full receipt mutation/refusal, bounds/uniqueness and
isolation/lock-context propagation. Eighteen added disposable Actions DB cases
cover SELECT-only grants, explicit versions/digests, ownership/principal refusal,
changed setup/profiles, replay without renewal and corrupt/duplicate receipts,
including rehashed false authority. Exact verifier head
`9aff9d7af87c44fcce878feb8709ef00bd0596d0` passed
[Actions](https://github.com/TogetherWeOwn/labelloop/actions/runs/42834423864), including
[906 management DB, 24 ledger and 21 CPU cases](https://github.com/TogetherWeOwn/labelloop/actions/runs/42834423864/job/129892790488),
CodeQL, gitleaks and remaining CI. This verifies current snapshots, not complete
bootstrap, executable publication or the opt-in consumer below.

### Opt-in training configuration consumption

Trusted assembly can supply immutable `ConfigurationTrainingBinding` tuples to
`CanonicalManagementOperations` / `CanonicalManagementTraining`. Each binding selects
one project and an explicit snapshot version/digest. Configured mode must cover every
profile project exactly once; malformed, duplicate, mutable or partial bindings refuse
at construction. Empty bindings retain the existing unbound fixture/adapter path and
make no configuration-publication claim. Public profile reads remain server declarations,
not proof that their setup is current or executable.

For a **new** configured training submission, the existing producer first authorizes
and locks the owned project and verifies fenced compiler publication. In that same
READ COMMITTED caller transaction it rechecks the selected snapshot against current
setup receipts, source-locked evidence, grouping and all trusted project profiles.
It also checks compiled ontology/assignment and each emitted source hash/group/split
against that snapshot. The verified project/version/digest/setup-summary provenance
is included in the existing immutable queue input and attributed training receipt.
No separate enqueue, connection, store, runtime pointer or scheduler is introduced.
Historical exact retries return their original intent before current configuration
resolution; they neither renew stale setup nor enqueue again. Workers retain native
immutable-input/receipt equality and historical request validation rather than resolving
changed deployment profiles during resume.

Seven pure cases exercise binding immutability/coverage, current-evidence forwarding,
lineage refusal, pre-enqueue failure, queue/receipt provenance and facade forwarding.
Eleven new disposable DB cases commission the real locked verifier with current/stale
setup and profile evidence, explicit version and compiler membership substitutions,
and real training-producer missing-snapshot refusal / historical replay at both sizes.
**Those new cases await exact-head Actions.** The successful locked membership case
uses a constructed manifest projection, not a compiled variant; the positive enqueue
wiring case uses mocks. Successful configuration-bound HTTP-to-CPU training is not yet
proved: effective project publication and its unchanged freeze gate remain prerequisites.
Existing CPU acceptance uses the unbound fixture path and is not such evidence.

This is a restricted opt-in profile/provenance consumer, not an executable project
release or freeze clearance. No schema/client/table/migration/dependency/grant changes,
real admission, default mounting, worker startup, provider dispatch, retrieval,
qualification, promotion targets or autonomous spend are enabled.

### Approved setup summary (opt-in, not publication)

Exact `891a65e229039db7f535f9deca9e33d41064b9bb` passed
[Actions](https://github.com/TogetherWeOwn/labelloop/actions/runs/42814247214):
[866 management DB, 24 ledger and 21 CPU cases](https://github.com/TogetherWeOwn/labelloop/actions/runs/42814247214/job/129823195761),
[1,945 unit cases / 967 skipped](https://github.com/TogetherWeOwn/labelloop/actions/runs/42814247214/job/129823195866),
and the remaining CI, CodeQL and gitleaks checks. Approval receipt integrity is
verified there, not the summary increment below or complete bootstrap acceptance.

The existing project read now computes `setup_summary_sha256` after verifying all
five approvals and their draft/catalog/current-pack evidence. It binds canonical
project/workspace identity, approved content digests and receipt revisions, and
current ontology/assignment versions. No new row, operation or mutable summary store
is introduced. Incomplete approvals return null; inconsistent current evidence
refuses. It is not a project release or an executable runtime-configuration pin.
Freeze/admission/default mounting and qualification gates are unchanged.

Eight new disposable DB cases cover composed approvals, deterministic SELECT-only
projection, three draft-edit invalidations, corrupt receipt/stale source/assignment
refusals, and shared-session admin/reviewer/no-store/logout reads. The fixture
explicitly seeds grouping; it does not prove full grouping-to-setup publication.
Pure digest and generated-client tests cover deterministic ownership/version/receipt
binding and read-only transport. These changes await exact-head Actions. Effective
project publication, recipe/runtime-policy binding, runtime roles/dispatch, single
registry composition, final mounting and native reviews remain incomplete.

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

### Canonical local-training producer (partial, unmounted)

`CanonicalManagementTraining(mutations, enqueue, profiles)` binds local training
intent to the same SQLAlchemy/psycopg transaction and existing queue, not a second
training controller. `ApprovedTrainingProfile` is immutable trusted server
configuration scoped to one canonical project, with unique public ID/revision,
recipe, initialization digest, epochs, batch and seed. Construction validates the
delivered `TrainingRequest` and public read contract; no caller can pass arbitrary
arguments, checkpoint paths, endpoints, GPU or paid backend. The delivered recipe
supplies 320/640 shape and the existing baseline supplies every resolved argument.
Production profile approval/provisioning and checkpoint storage remain deployment
prerequisites; accepting this configuration does not create an approval UI.

The authorized profile read is SELECT-only and exposes only profiles for the
owned managed project. `start_training` requires a human admin, bound context,
workspace ownership, the immutable variant revision and a compatible project
profile. It takes the existing project/retry locks. A queued compile identity is
not a published variant. Before enqueue it verifies the real system-attributed
compiler completion, succeeded job and matching application generation, saved
stage artifact, manifest/content/class-map hashes and immutable recipe/variant
records. It reuses the compiler's historical snapshot/receipt rehydration; later
canonical reviews cannot silently replace the compiled training inputs.

The training input pins the exact compiled variant, producer job, manifest,
profile ID/revision/arguments, initialization content key, core `TrainingRequest`
and authenticated requester. One variant/profile revision has one intent across
actors/keys; distinct profiles/revisions can express distinct controlled
experiments. Exact retries return the accepted job even after claim or profile
configuration removal, never re-resolve historical intent from current config.
Changing a key's body conflicts. The receipt and job commit or roll back together;
neither dispatch nor replay advances the project revision or claims a completed
training run. The trusted queue kind is `train_local`; no handler is registered.

Disposable Actions cases compose actual project/freeze/compile publication with
this producer at both sizes and test precommit invisibility, restricted-role
queue/audit/caller rollback, concurrent retry/unique-profile intents, profile and
workspace isolation, revision/context/principal refusals, missing/ambiguous
compiler authority, SELECT-only profiles and real shared-session/CSRF dispatch.
Profile configuration cases validate immutable bounds and baseline arguments.
These are commissioned cases, not a pass until exact-head Actions completes.
No new table/migration, dependency, production grant, default mount, paid call or
model loading is introduced by the producer. Training publication is supplied by
the separate adapter below. Model/export publication, training detail reads,
standalone evaluation and full CPU project-to-export acceptance remain incomplete.

### Local-training execution stage (partial, unmounted)

`CanonicalLocalTrainingWorker` supplies a `train` stage for trusted composition
with the existing queue, not a new controller. Its backend factory is injected by
server code; an ML process can pass the delivered `LocalUltralyticsBackend`
without importing vendor libraries into the API. Its `publish` method is the
canonical completion sink for `JobStore.complete`, not a no-op. No worker is
registered by default; saving a stage alone is not successful canonical publication.

Before object access, one read-only repeatable snapshot verifies the actual job,
workspace/project identity, exact human-attributed training receipt, historical
profile/request and fenced compiler publication. It reuses the compiler's frozen
snapshot rehydration, never substitutes current labels, and constructs complete
source-frame development truth for the compiled validation members. Initialization,
manifest and every compiled file are read only by validated content-addressed keys
and hashed before backend construction. The complete relative-path allowlist is
checked before any object read or file write. Worker-owned temporary paths are
cleaned on success/failure; the dataset is a disposable copy, not edited canonical
storage. Input limits are 100,000 files, 64 MB per dataset object, 512 MB total
dataset bytes and 256 MB initialization. Deployment object readers must additionally
bound transport/memory allocation; checking returned bytes cannot prevent an
unbounded reader from allocating them first.

`collect_training` validates the core result/request and complete epoch coverage,
checkpoint bytes, matching source-frame report lineage, actual training/validation/
inference shapes and non-lossy dataloader coverage. Only after every check succeeds
are epoch weights uploaded under their byte hashes. Total checkpoint bytes are
bounded to 512 MB (256 MB each); inline result/report/shape/retention evidence uses
the existing queue's 2 MB bound. Storage failure may leave unreferenced immutable
objects; it cannot create a canonical run or completion receipt. The stage retains
internal resolved worker paths in its private record; future public reads must
project only approved scalar argument keys, not expose raw records.

New disposable DB cases check both recipes' historical truth/materialization,
forged authority refusal before storage/backend access, corrupt inputs and cleanup.
They stop at an explicitly synthetic backend and do not claim actual queued CPU
training. Unit cases use synthetic output to check artifact/lineage/shape/retention
refusals and safe paths. The existing real CPU smoke additionally feeds its actual
320/640 backend output through the collector without another training run. These
are commissioned cases, **not verified until exact-head Actions completes**.

### Canonical training/report publication (partial, unmounted)

`publish` rechecks durable human intent, owned project/variant identity, historical
profile/request, succeeded compiler application/receipt and frozen source snapshot
inside the queue's existing application transaction. It reconstructs the core
result/checkpoints/reports, rechecks epoch coverage, selected checkpoint, arguments,
shape/retention evidence and content-addressed checkpoint keys. It performs no
backend, filesystem, object-store operation or second database connection.

The delivered model store now exposes `save_training_transaction` for the queue's
active psycopg transaction. It compiles the same bound variant SELECT and applies
the same lineage validator as the existing SQLAlchemy `save_training`; native JSON
inserts use a savepoint inside the caller transaction, never an independent commit.
Only after verified ownership, the sink translates `request.project_id` from the
management UUID to that project's canonical key for the model-store record. The
authorized job and original stage remain byte-for-byte unchanged. A system audit
receipt binds both identities, requester, job/generation and the complete stage
digest. Training, development reports, audit, application fence and queue success
commit together; grant failure, caller error or late lease expiry rolls all back.
Broad canonical-audit rewrite authority is refused.

Disposable Actions cases use the real canonical/compiler/queue path and explicitly
synthetic training artifacts at both sizes. They cover precommit invisibility,
identity translation, three missing-grant rollbacks/recovery, caller/deadline
rollback, expired/cancelled/superseded leases, artifact refusals, broad-role refusal
and saved-stage resume without retraining or storage access. These cases do not
prove actual queued CPU training and remain unverified until exact-head Actions.
The fixture grants SELECT/INSERT on existing `training_run`/`evaluation_run` tables
only in its disposable DB; no production grants or model/registry writes are added.
Export/model publication, standalone evaluation, final role provisioning, mounted
app and actual new-project-to-export acceptance remain open. No qualification or
default mount is introduced here.

### Candidate export and trusted execution (partial, unmounted)

`CanonicalLocalModelPublisher` composes the training/report sink and delivered
model-store lineage/export validation inside the existing queue application
transaction. Candidate identity, class order, preprocessing and policy come from
verified training, not exporter-selected registry fields. The candidate, its
selected development-evaluation link, attributed export receipt, training/reports
and queue success commit together. No promotion or qualification is performed.
`collect_export` validates the controlled export evidence and actual bounded ONNX
bytes before content-addressed upload. It is a collector, not an ONNX verifier.

`CanonicalLocalExportWorker(training, exporter)` supplies the next `export` stage
for the same `train_local` job. Its trusted callable port matches the delivered
ML `export_and_compare` keyword arguments and returns its dataclass as a dictionary;
only the ML process imports the vendor implementation. Before any object access,
the stage reuses `training.resolve` to recheck owned durable human intent, historical
profile/request, frozen truth and fenced compiler authority. The shared artifact
validator checks saved checkpoint reports; request/epoch/key and actual shape/
retention evidence are rechecked before a checkpoint can reach the exporter.

The execution helper rehashes the published manifest, selected checkpoint and
compiled files in fresh worker-owned temporary paths. It deterministically chooses
at most 16 distinct validation derivatives in path order, never training or final
test pixels for comparison. The callback receives only those paths, verified digest
and server-pinned inference policy, not arbitrary output paths or commands from a
stage artifact. Only the fixed adjacent `selected.onnx` is collected. Returned
comparison coverage must match the supplied samples, the selected checkpoint must
remain unchanged, and all temporary paths are cleaned after success or failure.
Transport bounds still belong to the object adapter. No initialization download,
retraining or new controller is needed after a saved training stage.

Trusted deployment composition uses existing `JobRunner` stages `training` then
`export`, with `CanonicalLocalModelPublisher.publish` as the completion sink.
No default handler/worker, app mount or production grant is installed here. Native
lease renewal, bounded retries, immutable stages, cancellation and application
fencing remain the only scheduler/recovery authority. An export retry can reuse
saved training; saved training plus export can publish without ML/object I/O.
Unreferenced immutable objects after a storage or lease failure are not candidates.

New disposable Actions cases use real canonical/compiler/queue authority with an
explicitly synthetic exporter: both recipe sizes, owned-intent and saved-artifact
refusal before I/O, corrupt manifest/checkpoint/dataset, callback/output failures,
cleanup and saved-training retry followed by atomic candidate publication. Existing
actual CPU 320/640 smoke now runs the orchestration helper with the delivered
`export_and_compare`, without a second export. These cases are commissioned, not
verified until exact-head Actions completes. They still do not establish a DB-backed
new-project-to-CPU-export path. Standalone evaluation, detailed reads, full runtime
registration/grants, effective setup, admission and final mounting remain open.

### Standalone development-evaluation producer (partial, unmounted)

`CanonicalManagementEvaluation` binds `start_evaluation` to the canonical caller
transaction and existing shared queue. `ApprovedDevelopmentProtocol` is immutable,
project-scoped trusted server configuration: a stable identifier/revision and a
specific compiled variant UUID. The request selects only that approved protocol
and its exact dataset-release ID, never a split, role, weights, threshold, path,
class map or arbitrary inference arguments. This producer supports development
on the variant's validation split only; it grants no final-test access, threshold
tuning, promotion or qualification. Final-test lifecycle remains with the existing
qualification service, not a second implementation here.

Before enqueue, the adapter checks workspace/project ownership, immutable model
revision, a unique fenced model-export receipt with saved training/export stages,
and the delivered model-store lineage/export validator. It reuses the compiler's
historical snapshot and publication validation for the evaluation variant; recipe,
class-map and preprocessing identity must match the candidate. Model identity and
all inference parameters come from the verified atomic model release. No object
bytes are fetched, inference run, checkpoint selected or report attached during
intent creation.

The canonical project lock serializes new model/protocol/revision intents across
actors, without incrementing project configuration or modifying the initial model.
Existing actor/workspace idempotency locks reconcile exact retries before current
protocol/revision checks; removing/revising server configuration does not erase an
accepted receipt. Changed bodies/targets conflict. A distinct protocol revision
is a distinct pinned experiment; duplicate fresh keys for the same revision are
refused. Queue kind `evaluate_local`, bounded retries, attributed receipt and result
identity commit together through `enqueue_in_transaction`. The intent pins model
record digest, publishing job, weights key, historical compiler job/manifest,
validation-only development protocol and the candidate's inference policy.

Disposable Actions cases commission both sizes through real canonical/compiler,
queue and model-publication authority, using explicitly synthetic ML artifacts.
They cover precommit invisibility, retry/config removal, concurrent submissions,
missing-grant/caller rollback, identity/revision/protocol/context refusal, unpublished
or duplicate model evidence, incompatible compiled recipes, versioned protocols,
and real restricted shared-session/CSRF/admin/reviewer composition. Small unit
cases cover trusted-protocol validation and scope/bounds. These additions require
exact-head Actions verification; they do not demonstrate real evaluation or CPU
project-to-export acceptance. Execution is the separate stage below; fenced
report/link publication, lifecycle-safe final-set composition, runtime registration/
grants, detailed reads and final app/admission mounting remain incomplete.

### Standalone development-evaluation execution (partial, unmounted)

`CanonicalLocalEvaluationWorker` rechecks the durable human-attributed intent,
workspace/project ownership and fenced model/compiler publication in one read-only
repeatable-read snapshot before object access. It rehydrates historical canonical
truth through the existing compiler authority, never current labels. The accepted
protocol remains pinned even if deployment configuration later changes. Neither
model output nor a caller can substitute a run, split, role, recipe or threshold.

Trusted ML composition injects the delivered `predict_samples` adapter. The helper
hash-checks the stored manifest, selected weights and validation raster bytes;
only validation image files are materialized, not train/test pixels or labels.
Temporary paths are code-owned and cleaned on success and failure. It requires one
prediction group for every compiled derivative with exact canonical transform and
source identity, observed single-image tensors at the recipe size, and an unchanged
checkpoint. Predictions may supply detections only, not metrics or lineage.
The delivered `EvaluationRunner` computes original-frame development metrics and
coverage against every frozen validation source, including a source excluded by
the recipe; excluding difficult pixels must not remove its truth from the score.
The bounded inline stage record contains the report, actual tensor shapes and
sample count. This stage uploads no objects, publishes no report/link, changes no
selection/registry state and installs no default worker or application route.

Disposable DB cases use real canonical/compiler/queue/model authority with
explicitly synthetic predictions at both sizes. Unit fixtures cover positive
source-frame scoring, excluded-source coverage and refusal before I/O. The existing
actual CPU smoke exercises this helper with `predict_samples` at 320/640 and retains
its report; it is still not a DB-backed new-project-to-export proof. These are
commissioned cases until exact-head Actions verifies them. Completion fencing and
report/link publication must be composed before this job is available end to end.

### Fenced standalone evaluation publication (partial, unmounted)

`CanonicalLocalEvaluationPublisher.publish` is the existing queue's completion
sink for the single `evaluation` stage. It rechecks the durable human intent,
workspace/project/model ownership, pinned protocol and saved stage digest inside
that caller-owned psycopg transaction. Shared native compiler/model authority
helpers serve both the SQLAlchemy intent/execution readers and this sink; no
second connection, commit, object read or inference call is used for publication.
Historical source membership, class support, coverage identities, derivative
counts and observed recipe tensor shapes must match the pinned development run.
Locked-final-test or substituted run/weights/recipe evidence is refused.

The model store's existing evaluation lineage gate is shared by its SQLAlchemy
and native transaction adapters. Publication appends the report and model link,
then an attributed completion receipt carrying requester, worker, protocol, job,
generation and stage digest. The existing queue application fence commits all
three with job success, or rolls them all back on grant, caller or deadline
failure. The immutable model payload and training selection are never rewritten;
`load_release` includes the separately linked evidence. No registry promotion,
qualification or final-test authority is added.

Disposable Actions cases use real canonical/compiler/queue/model authority with
explicitly synthetic predictions at both sizes. They cover precommit invisibility,
unchanged model history, linked reads, three missing-grant rollbacks and saved
retry, caller/deadline rollback, expired/cancelled/superseded leases, saved-stage
resume without ML/object access, forged intent/broad audit-role refusal and pinned
report/support/coverage/sample/shape refusals. These new cases are commissioned,
not verified until exact-head Actions passes. Default worker registration, runtime
grants, final mounting and DB-backed actual CPU project-to-export acceptance
remain incomplete; the real asset-admission Security condition is unchanged.

### Explicit request-bound worker registration (partial, opt-in)

`labelloop_api.management_workers` composes the delivered `JobRunner`, rather
than implementing another polling loop, scheduler or stage store:

| Factory | Fixed job kind | Ordered saved stages | Transactional completion sink |
| --- | --- | --- | --- |
| `compilation_runner` | `compile_variant` | `compilation` | `CanonicalCompilationWorker.publish` |
| `local_training_runner` | `train_local` | `training`, `export` | `CanonicalLocalModelPublisher.publish` |
| `local_evaluation_runner` | `evaluate_local` | `evaluation` | `CanonicalLocalEvaluationPublisher.publish` |

Deployment-owned code supplies the existing queue connection factory, restricted
SQLAlchemy engine, content-addressed object adapter and trusted workspace. The
compiler does not receive an ML backend. ML composition supplies the delivered
`LocalUltralyticsBackend`, `export_and_compare` and `predict_samples` through the
existing typed boundaries; no vendor import crosses the licence boundary. The
API package explicitly depends on the already-locked first-party queue package.
No external version, artifact, build backend or registry source changes.

Each factory **requires one specific durable job ID**, supplied by trusted
request-to-worker dispatch. Empty/unscoped claims are refused, so an invocation
cannot drain another workspace's same-kind queue accidentally. Job IDs are not
credentials: stages and completion still recheck owned durable intent, human
receipts and lineage. Constructing a runner opens no database connection, reads
no object and starts no computation. Call `run_once(worker_id)` only inside the
correct deployed worker process; no supervisor or default registration is added.
Native lease/runtime bounds, heartbeats, generic failure codes, cancellation,
stage recovery and application fencing remain the existing runner's authority.

Actions registration cases cover both recipes, a queued decoy left untouched,
training-stage recovery through transient export failure, saved export completion
without object/ML access, and fresh/saved development evaluation with single
report/link/audit publication. ML artifacts and predictions in those DB cases
are explicitly synthetic, not CPU training/export or quality evidence. Unit
contracts check fixed kinds/stages/sinks, shared dependency identity, no I/O and
unscoped/invalid-bound refusal. Newly commissioned tests require exact-head Actions
verification. Automatic dispatch, default worker startup, final app mounting,
production grants and DB-backed actual CPU project-to-export acceptance remain
open. Real asset admission remains disabled pending the existing Security gate.

### Explicit one-job dispatch (partial, opt-in)

`management_dispatch.CompilationDispatch` and `LocalMLDispatch` accept one durable
job ID and a trusted worker identity. A SELECT-only repeatable snapshot resolves
its persisted kind, joins its actual project/workspace and requires exactly one
matching human intent receipt with the same actor, aggregate, project and canonical
JSON inputs. Missing, foreign, ambiguous or mismatched evidence refuses before
claiming a lease or accessing objects/ML. Fixed compiler versus local-ML lanes
cannot route provider/LLM kinds or dispatch one another's jobs. Compiler composition
receives no backend, exporter or predictor.

The snapshot is a dispatch preflight, not new authorization or execution state.
The existing runner claims only that ID; stages and the completion sink recheck
historical intent, lineage and native lease/application fences. Completed, active,
cancelled and retry-delayed work remains governed by the native queue. Construction
performs no I/O; no loop, supervisor, queue scan, endpoint, automatic startup,
credential or production role provisioning is added. Deployment-owned worker code
must supply the existing restricted connections, object capabilities and local ML
callbacks inside the correct process. Default entry points remain unchanged.

The existing two separated-role CPU cohorts now use this dispatcher for all three
jobs, retain replay and cross-lane refusals and record dispatch evidence. Four new
disposable DB cases require foreign-workspace, wrong-lane, unreceipted and duplicate
receipt refusals with no native job claim or canonical mutation. New unit contracts
cover the fixed factory/dependency wiring, bounded reads and identity mismatches.
Exact `18167d3f5da7626fd5a56fae4df0579a0783688b` passed
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42869474513):
[943 management DB, 24 ledger and 21 CPU cases](https://github.com/TogetherWeOwn/labelloop/actions/runs/42869474513/job/130016608534),
[2,004 unit cases with 1,044 skipped](https://github.com/TogetherWeOwn/labelloop/actions/runs/42869474513/job/130016608401),
remaining CI, CodeQL and gitleaks. The
[retained artifact](https://github.com/TogetherWeOwn/labelloop/actions/runs/42869474513/artifacts/13790170507)
confirms both separated-role CPU cohorts, 32 permission refusals per cohort, explicit
owned-job dispatch and cross-lane refusal. Both sizes have four validation sources
and four nonempty ONNX comparisons, matching checkpoint/export hashes and static
signatures. Both have zero recall: execution/equivalence, not model quality or
independent gold. This verifies dispatch and the preceding grouping/configuration
HTTP slice, not operational installation, database-enforced workspace isolation,
production provisioning or qualification.

### Bounded content-object transport (opt-in, not installed)

`management_objects.BoundedMinioObjects` implements the existing worker object
port using an explicitly supplied MinIO client, bucket and per-object byte limit
(up to 256 MiB). Construction performs no I/O and resolves no credentials or
endpoints. Trusted deployment code can supply it to the existing dispatchers or
grouping reader; nothing selects it automatically. The existing `MinioStore` and
default entry points are unchanged. Upload authority is not implicitly granted
to the API or to a read-only grouping capability.

Every key must be a lowercase SHA-256 content key with the matching prefix. Reads
request at most 1 MiB at a time and consume at most the configured limit plus one
sentinel byte before refusal, even without a Content-Length. Short reads continue
until EOF; truncated or mismatched content refuses by digest. Responses close and
release their connection on success and failure, including a close error. Uploads
validate bytes, size and digest before I/O and use binary content type for compiler,
checkpoint and ONNX artifacts rather than marking them as JPEGs. No custom retry,
URL fetch, bucket creation, policy/grant mutation or object deletion is introduced.

The limit bounds returned transport bytes, not total process RSS, SDK-internal
buffers, request duration or aggregate job storage. Native SDK timeouts and deployment
resource limits remain required; worker-stage aggregate bounds are unchanged. Unit
contracts cover chunk/boundary/EOF/digest/key/input refusals, cleanup and no retry.
They use a synthetic MinIO response, not a live storage service. Exact head
`c86196407d8f7a52d4891c08a54254e0b30d3fa6` passed
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42873352942), including
[2,151 unit tests / 1,044 skipped](https://github.com/TogetherWeOwn/labelloop/actions/runs/42873352942/job/130030161887)
and [943 management DB, 24 ledger and 21 CPU cases](https://github.com/TogetherWeOwn/labelloop/actions/runs/42873352942/job/130030162170).
CodeQL and gitleaks passed. That verifies the synthetic transport contracts, not
live storage. Its [CPU artifact](https://github.com/TogetherWeOwn/labelloop/actions/runs/42873352942/artifacts/13791281184)
retains filesystem-backed cohorts with four validation sources at 320 and five at
640, nonempty ONNX comparisons and matching configuration/checkpoint/export pins.
Both have zero recall: execution/equivalence is not quality or independent gold.

### Disposable live object-transport acceptance (commissioned)

The existing two CPU cohorts now use `BoundedMinioObjects` for normalized fixture
admission, grouping reads, initialization, compiler artifacts, checkpoints and
ONNX artifacts, with a 64 MiB per-object cap and no filesystem content fallback.
Canonical admission and annotations remain explicit fixture preparation, not HTTP
admission acceptance. All stored object types must be binary; the pipeline record
retains the adapter, cap and observed object count without endpoints or credentials.
Six additional live cases cover empty/exact/chunk-boundary round trips, stored
corruption, an oversized response and missing content. Refusal controls reuse the
client for an intact subsequent read. These new cases **await exact-head Actions**;
no live transport or expanded CPU pass is claimed yet.

Actions starts only the existing checksum-pinned `dev/minio` service immediately
before CPU tests, waits for health under a 120-second readiness limit and retains
the job's 20-minute bound. Each test creates an isolated disposable bucket, removes
its objects and bucket in fixture teardown and records confirmed bucket absence
in `minio-cleanup.json`. An `always()` step removes the service and its volume on
success, failure or cancellation; the GitHub-hosted runner is also ephemeral.
Nothing runs between jobs or outside this bounded Actions use window.

The fixture uses fixed loopback plaintext access and the existing local-only root
identity, never deployment endpoints or provider credentials. This is not storage
IAM/least-privilege provisioning, production installation, TLS qualification,
process-RSS/time isolation, default mounting or real-admission clearance. Those
remain separately gated; no production code, grant or automatic startup changes.

## Canonical summary reads (partial, unmounted)

`CanonicalManagementReads` supplies `list_projects`, `list_resources` and `read`
for canonical projects, assets and dataset releases. Each entry point requires a
human admin/reviewer with review permission and constrains every query to the
trusted deployment workspace. Unknown and foreign IDs return the same not-found
response. Projects without a scoped management event are omitted pending explicit
adoption. Compiled variants, completed local training runs, standalone development
evaluations and exported candidates additionally use the persisted-publication
indices below. This remains a partial adapter: queued/running/failed jobs are not
invented from completed records or returned as completed resources.

Lists use bounded UUID keysets (1–100 entries), fetch at most one lookahead row,
and return a cursor within the published identifier bounds. Its scope tag binds
workspace, project and resource kind to prevent accidental cursor reuse. The tag
is not a signature, authorization or stable snapshot: the SQL query always applies
ownership independently, and concurrent inserts before the position require a new
traversal. No count-all query, unbounded response or offset walk is used. Project,
asset and dataset-release summaries need no per-item query. Persisted-publication
summaries revalidate each returned item's lineage with the existing detail readers
inside one SELECT-only repeatable snapshot; lookahead rows are not validated until
returned. This bounds the number of validations to the page size, not total
history. Project revisions come from attributed management events; asset revisions
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

## DB-backed real CPU pipeline

The Actions ML job additionally runs
`workers/ml/tests/test_management_pipeline.py` at 320 and 640. It reuses the
API's existing disposable restricted-role fixtures and the checked-in non-game
interface domain. The expanded cases mount `CanonicalManagementOperations` through
`create_app` in a test-only HTTPS client with the existing restricted SQL-backed
session store. Project creation, authoritative freeze-plan reads/freeze, compilation,
training and development-evaluation commands now pass through that HTTP boundary.
Real request-bound queue runners execute and publish each accepted durable job.
Training uses `LocalUltralyticsBackend` on CPU with a seeded local architecture;
export uses `export_and_compare` with nonempty native/ONNX predictions, and the
standalone development job uses `predict_samples` plus source-frame evaluation.
No weights or images are downloaded, paid provider invoked or domain engine edited.

Original-byte normalized ingest deliberately remains independent fixture setup,
using the canonical adapter directly: the HTTP admission operation stays unavailable.
The fixture owner seeds the known annotation/completeness evidence and creates
temporary test grants. Initial grouping from canonical rasters and setup/configuration
publication now use the HTTP operations described above. Review writes use the existing
append-only revision transaction. These coarse fixture labels are not independently
adjudicated gold. The cases do not prove the complete grouping lifecycle, production role
provisioning, real asset-admission clearance or default app mounting. Request-time
authentication, canonical and worker calls retain restricted roles and normal
receipt, revision, retry, lineage and application-fence checks.

The CPU cases now separate the canonical API/enqueue role from a new disposable
NOLOGIN execution role, in addition to the existing restricted authentication role.
Both the worker's SQLAlchemy reads and native psycopg queue/publication transactions
use that execution role. The API reads completed evidence but cannot update a queue
job or insert stage/application, compiler or ML publication rows. The worker can
claim/fence/publish existing jobs, but cannot enqueue jobs, insert canonical sources,
annotations, completeness, assignments, references or releases, or change project
policy, source metadata, annotation attribution, split history or reference approval.
Neither API nor worker can read authentication principals or update/delete canonical
events. Thirty-two SQL insufficient-privilege controls run before and after each
existing CPU cohort; exact role identities are checked through both worker connections.
The retained pipeline artifact records these capability checks without role names.
No extra training cohort, production role/grant, credential or deployment provisioning
is introduced. These changed cases await exact-head Actions; disposable role separation
is not operational provisioning, automatic dispatch or a claim of database-enforced
event actor attribution.

The expanded cases check CSRF refusal and exact HTTP replay for every command,
queued/completed job reads, server-configured profile reads, completion attribution
to the persisted session principal, and post-completion replay without another
application. After a persisted role change, the same session can read actual
training/export/development details and completed resource/job indices as a reviewer,
but cannot replay an admin mutation. Logout revokes subsequent reads. The retained
pipeline artifact explicitly records this HTTP scope and the admission/grouping
limitations. These changed cases are commissioned, **not verified until their
exact-head Actions run passes**; prior direct-adapter evidence below does not prove
the expanded HTTP path.

Immutable server training profiles can now pin an optional `InferencePolicy`.
Its shape must match the delivered recipe; omission preserves the previous
0.25-confidence/300-detection defaults. The CPU fixture pins the existing seeded
smoke's 0.005-confidence/1000-detection cohort, avoiding an empty or capped export
comparison. This is development instrumentation, not a quality target or permission
to promote. The public command still selects only a server-approved profile ID;
no threshold, shape or arbitrary argument is accepted from a request or model.
Worker execution and publication reconstruct that policy and require exact equality
with the durable attributed intent and saved public profile arguments.

Assertions cover normalized source hashes, all 16 manifest members, class identity,
actual tensor shapes/retention, selected checkpoint, ONNX signature/hash/equivalence,
persisted model/evaluation links, full validation-source support and coverage,
unchanged source-frame metrics, one application per job, attributed completion,
exact retry replay and worker-path cleanup. The existing ML evidence artifact retains
`management-pipeline.json`, export equivalence, runtime shapes, target retention and
development reports. Exact source `fe28a1c58cb5ba30b212c4562e9db5301c957e40`
passed [Actions](https://github.com/TogetherWeOwn/labelloop/actions/runs/42506014766):
566 management DB, 24 ledger and 21 real CPU cases (including both new pipeline
sizes), plus 1,621 unit tests. Retained pipeline artifacts show four validation
sources per size, matching checkpoint/export hashes and static signatures, and
four nonempty ONNX comparison samples. Both coarse fixture cohorts have zero
recall: successful execution is **not model quality or domain qualification**.
Default management/admission stay disabled;
final mounting, full setup/runtime composition, Security/QA and qualification gates
remain open. The single promotions implementation remains with its existing owner.

## Completed development-evaluation detail

`CanonicalManagementReads.read_evaluation` now projects a persisted standalone
local development report for an authorized human admin/reviewer in one read-only
repeatable snapshot. Ownership resolves before receipt or job lookup. It reuses
the existing compiler/model publication validators and model-store evaluation
lineage gate, then requires the unique attributed intent/completion, current
successful application generation, exact saved stage/hash, report and model link.
A hand-inserted report or borrowed completion cannot establish this authority.

The result exposes the actual source-frame report, not object keys, local paths
or raw provider output. It remains explicitly unqualified with no incumbent,
quality target or qualification ID. `opened_final_test_count: 0` describes this
validation-only development operation, not a project-wide final-test ledger.
Epoch checkpoint reports, queued jobs and final-test/qualification details are
not supported by this adapter. The existing detail DTO and generated contract
are unchanged; default mounting and real admission remain disabled.

Twelve disposable Actions cases are commissioned for both recipes, precommit
invisibility, enforced read-only operation, identity/workspace refusal, application
generation/status, duplicate completion and hand-inserted/borrowed evidence.
These new detail cases are not verified until their exact-head Actions completes.

## Completed local-training detail

`CanonicalManagementReads.read_training` resolves a persisted completed run only
after human admin/reviewer workspace ownership, in one read-only repeatable
snapshot. It checks the unique human intent and attributed completion, current
successful queue application generation, saved training stage/hash, receipt-backed
profile and compiler publication. The existing core training and runtime-evidence
validators recheck the immutable result, selected checkpoints, persisted checkpoint
reports, manifest, tensor shapes and lossless dataloader audit. The only project
identity translation is the verified canonical UUID to the model store's public
project key. It does not require current server profile configuration to retain a
historical completed run.

The existing DTO returns pinned requested/resolved arguments only, never additional
provider arguments, paths, object keys or raw runtime metadata. `images` counts
training derivatives; `epoch` counts completed epochs, while checkpoint epochs stay
zero-based. Retention totals describe recorded dataloader audit observations, not
unique source objects or a new independent identity audit. The checked baseline
permits no drops, so its dropped-object and mismatch lists are empty. Completion
time comes from the attributed receipt. This is execution evidence, not model
quality or qualification. Queued/running/failed/cancelled jobs and live progress
remain unsupported here rather than being represented as completed runs.

Fourteen disposable Actions cases cover both recipes, precommit invisibility,
enforced read-only operation, identity/workspace refusal, current training/compiler
application generations, failed jobs, duplicate intent/completion and hand-inserted
runs/borrowed receipts. These cases await exact-head Actions; synthetic DB training
artifacts do not establish real CPU or full mounted HTTP acceptance. No schema,
client, dependency, migration, runtime grant or default mounting change is needed.

## Published recipe preview (partial, unmounted)

`CanonicalManagementReads.read_recipe_preview` exposes one emitted full-frame
320/640 sample from an owned frozen release, only after successful fenced compiler
publication. It reuses the existing historical snapshot/receipt/application
validator and reads the source's exact annotation revision in the same read-only
repeatable snapshot. Core geometry computes actual raster gains, padding and mapped
boxes; the transform must exactly match the published compiler manifest. Current
labels never replace historical labels. Review tier, source hash, dimensions,
object identity/class/geometry and retained policy must agree with the pinned sample.

This is **not a pre-compilation planning surface**. Uncompiled recipes, excluded or
nonmember sources and final-test samples return missing evidence rather than an
invented preview or another final-test label channel. Only emitted training and
validation samples are supported. The immutable code-owned v1 recipes have revision
one. Counts describe this selected emitted sample: clipped/below-minimum/excluded
counts are zero after retained full-frame policy validation; a target at or below
six derived pixels is flagged as a small-target warning. No model-quality pass is
inferred. Artifact IDs are content digests, not object keys, signed URLs or a new
download capability. No object bytes, image decoding or compiler run occurs on read.

Fifteen new disposable Actions cases cover precommit invisibility, SELECT-only
access, principal/workspace/release/recipe/source isolation, current application
and unique receipt requirements, and synthetic projection-boundary corruption
including final-test refusal. Both existing positive compiler cases additionally
check exact transformed boxes and historical reads after later source rejection.
These additions await exact-head Actions; no local suite or test-pass claim is
substituted. Schema/client, runtime grants, default mounting and real asset admission
are unchanged. A usable pre-compilation preview and excluded-source planning remain
explicit gaps, as do complete runtime/setup and final HTTP acceptance.

### Published tiny-target exclusions

The same preview route now distinguishes `evidence_state: excluded` from both a
prepublication plan and an emitted `compiled` sample. After the normal unique,
current fenced-compiler checks, an excluded manifest member is rehydrated through
the existing historical frozen-release hash gate in the same SELECT-only snapshot.
Only selected training/validation sources whose every target is below the
code-owned full-frame minimum can use this path. Negative, mixed-size, unselected
and final-test sources remain refused. Every original target stays visible in the
explanatory geometry; none is represented as an emitted negative or raster.

The manifest and validation artifact IDs identify the published variant, not an
excluded image. No object bytes, exclusion-file bytes, raster decoding, job or
state mutation occurs. The membership observation is verified publication evidence;
its size explanation is deterministic frozen geometry, not fresh file verification,
model quality or qualification. Corrupt publication still fails closed without
falling back to planning. Default mounting and real admission are unchanged.

Seven new disposable Actions DB cases commission both sizes, SELECT-only/no-object
access, later rejection without historical rewrite, surviving validation membership,
failed/stale generations and synthetic projection-boundary corruption. Eight pure
cases cover explicit excluded state and negative/mixed/final-test/membership
refusals. These new cases require exact-head Actions verification, not a local
suite or claimed pass. The prior frozen-planning head `9af1a0a` passed
[Actions](https://github.com/TogetherWeOwn/labelloop/actions/runs/42763118902):
711 management DB, 24 ledger, 21 CPU and 1,788 unit tests (812 skipped); this does
not verify the new excluded-source extension. Editable crop/ROI planning, full
setup/runtime/registry composition, final mounting and native reviews remain open.

## Source-pair decision transactions (partial, opt-in)

The facade now exposes `resolveSourcePair` through the same shared principal and
canonical mutation authority. The current pending pair must have two owned,
distinct endpoints and finite supported metric evidence. Its existing metric,
value and algorithm are recorded alongside the human decision/reason. Client
fields cannot replace endpoints, select splits or assert metric provenance.
`related` adds a conservative canonical similarity edge; `unrelated` resolves the
suggestion without deleting any automatic edge. The pair state, relation, project
approval/qualification invalidation and attributed retry receipt commit together.

Related decisions check all existing endpoint assignment versions. Both endpoints
must already share a group, split and policy in every version, or neither may have
assignment history. Otherwise the operation fails closed pending real regrouping/
quarantine support, including when only an older version conflicts. Historical
assignments, sources, annotations and releases are never rewritten. The helper
reuses the canonical tables and group-closure contract, not a new grouping engine.
No migration, production grant, dependency, default mount or asset admission is
introduced. The disposable role adds only `UPDATE (review_state)` for this path.

Twenty-seven new disposable Actions DB cases commission both metrics/decisions,
receipt/retry/revision races, unchanged compatible history and automatic edges,
five unsafe historical configurations, invalid persisted metrics, principal/
workspace/endpoint/context refusals, four missing-grant rollbacks, stale approval
restoration refusal and restricted shared-session/CSRF/replay/logout composition.
The shared transport mutation matrix, strict-input and generated-client request
cases also cover this operation. These cases are commissioned, not yet passed.
Actual source-pair discovery, assignment publication, quarantine, full project/
reference publication and final mounting remain incomplete.

The prior exact `4969317` head passed
[Actions](https://github.com/TogetherWeOwn/labelloop/actions/runs/42765426791):
718 management DB, 24 ledger, 21 CPU, 58 queue and 1,796 unit cases (819 skipped),
plus CodeQL/gitleaks. This verifies the excluded-source preview extension, not
these pair changes or complete bootstrap. Previously inspected coarse CPU cohorts
have zero recall; execution/equivalence is not quality or independent gold.

## Published export identity (partial, unmounted)

`CanonicalManagementReads.read_export` resolves an owned persisted candidate in a
single SELECT-only repeatable snapshot. It reuses the existing fenced model-store
validator and completed-training detail validator: unique human intent and model/
training receipts, successful current job applications, hash-checked saved stages,
immutable profile, compiler history, checkpoint/report lineage and static export
comparison evidence must agree. A hand-inserted model or borrowed completion
receipt is not export authority. No object storage read, inference, export or
artifact download occurs on this path.

The existing `ExportRead` returns only the model/recipe IDs, weights and ONNX
SHA-256, static input height/width and content-digest artifact ID. The artifact ID
is not a storage key, URL or download grant. This is persisted execution evidence,
not qualification, registry promotion, production availability or a fresh runtime
verification of the bytes. Missing/inconsistent authority fails closed.

Sixteen disposable Actions cases are commissioned for both sizes, precommit
invisibility, enforced read-only access, principal/workspace isolation, failed/
superseded jobs, duplicate intent/completion receipts, saved-stage digest mismatch
and hand-inserted models with/without borrowed receipts. Their ML artifacts remain
explicitly synthetic; exact-head Actions verification is required. No schema/client,
dependency, migration, runtime grant, default mount or real asset admission changes.

## Persisted-publication indices (partial, unmounted)

`list_resources` and `read` now support `variant`, `training-run`, `evaluation`
and `model` in addition to the canonical summaries. Every returned publication
is checked with the same compiler, training, standalone evaluation or export
validator used by its detail path. Missing, duplicated or superseded application
and receipt authority fails the read/page closed with `missing_evidence`; no
hand-inserted row, saved stage or precommit result implies completion. Candidate
`completed` means exported, not qualified, promoted or deployed.

The indices contain only committed persisted publications at revision 1. Pending,
running, failed and cancelled jobs require a separate owned intent/job projection;
they are not discoverable through these completed-record indices. Evaluation
indices select only reports with a scoped human standalone evaluation intent.
Checkpoint-development reports stay inside training details instead of being
duplicated as standalone evaluation resources. Unknown or inaccessible resource
IDs return `not_found`; an authorized empty index is an observed empty result,
not a claim that no asynchronous jobs exist. Current annotation edits do not
replace historical frozen lineage.

Workspace/project/kind-scoped UUID keysets and limits remain unchanged. Ownership,
row selection and every publication check share the same read-only repeatable
snapshot. Only IDs, project, kind, revision and `completed` cross the summary port;
no object I/O, storage keys, provider metadata, logs or qualification authority.
The export detail gate is factored for reuse, not weakened or independently copied.

Thirty disposable Actions cases are commissioned: both recipe sizes, SELECT-only
access, precommit invisibility, standalone/checkpoint-report separation, UUID page
traversal, foreign project/workspace/unknown isolation, cursor scope, principal
refusals, duplicated receipts and stale application generations for every kind.
ML artifacts in these DB cases remain synthetic. Exact-head Actions verification
is pending; no schema/client, migration, runtime grant, default mount or real
asset-admission change is made.

## Owned intent/job observations (partial, unmounted)

The existing `job` resource kind now has a bounded read/list projection for
canonical compile, local-training and standalone development-evaluation intents.
Ownership comes from the canonical project/workspace plus one attributed human
intent; an arbitrary queue row is not a discoverable management job. The receipt's
accepted resource, requester, target and exact immutable inputs must match the
native queue identity. Duplicate or inconsistent receipt evidence fails closed.
No raw input, error text, worker identity, storage key, provider field or log is
returned: the existing `Resource` contains job ID, project, kind, revision and state.

`queued`, `running`, `failed` and `cancelled` are native queue observations;
`succeeded` maps to `completed` only with a matching current application generation.
A retry returns to `queued`. An expired running lease is still observed as native
`running` until the queue claims or exhausts it; reads do not heartbeat, reclaim,
cancel or otherwise mutate jobs. `revision: 1` is the immutable accepted-intent
revision, **not** a lifecycle CAS token. Queue completion is not an artifact-detail,
current-byte, qualification, promotion or deployment grant. Those surfaces keep
their existing independent publication validators.

Stable canonical-intent UUID cursors retain workspace/project/kind scope and the
1–100 page limit. Queue selection and receipt/application checks share one
SELECT-only repeatable snapshot. The separate completed-publication indices are
unchanged, so a pending job never fabricates a variant, training record or report.

Twenty-eight disposable Actions DB cases are commissioned, not yet verified:
both sizes across queued/running/retry/exhausted/cancelled/completed states;
SELECT-only keyset traversal and cursor/ownership isolation; principal refusal;
seven inconsistent-authority controls, duplicate receipts, current application
generation, training/evaluation job visibility, unowned queue omission and
completion precommit invisibility. ML artifacts remain explicitly synthetic.
No schema/client, migration, grant, scheduler, default mount or real-admission
change is made. Full mounted HTTP acceptance and specialist gates remain open.

## Initial grouping publication (opt-in, not mounted by default)

`CanonicalInitialGrouping` consumes the existing original-raster collector and
canonical mutation authority in one project-locked transaction. The strict typed
request pins project revision, current ontology, receipt-verified source-policy
intent digest and exact source/annotation membership. Source-row locks synchronize
with review writers before annotation pins/counts are read. No request selects a
split, threshold, seed, model, storage key or eligibility state.

Core grouping supplies exact-hash/pHash relations. Conservative transitive closure
also includes attributed ingest `source_group` declarations and every persisted
owned relation. An internal synthetic session key feeds those components back to
core's unchanged seeded class-count allocator; it is not stored as observed source
metadata. The new immutable assignment version pins raster observations, current
annotation counts, declaration/edge identity, thresholds, seed, algorithm and
Pillow/JPEG/NumPy versions. Automatic relations, locked train/validation assignments,
setup/automatic-acceptance invalidation and an attributed retry receipt commit
atomically. Empty observed counts do not assert completeness or verified background.

This is deliberately **first publication only**: any assignment history, reference
row, persisted borderline evidence or newly computed review-band pair refuses.
There is no regrouping, holdout reassignment, quarantine bypass, final-test creation,
review-pair discovery/publication, metadata-value producer, embedding producer or
full project publication. Source-policy metadata-key intent remains distinct from
actual observations (`observed_metadata_keys` is empty); declared ingest groups are
still mandatory conservative constraints. Fewer than two independent groups refuse.
The adapter admits at most 256 sources, the collector's per-raster byte/pixel caps
and 64 MiB aggregate returned bytes; the trusted object reader still returns one
whole object before byte-size validation, not a hard streaming-memory bound.

The internal adapter is verified at `fdfccda245d26d7977e18bdaebc97556a2ad5068`:
[Actions](https://github.com/TogetherWeOwn/labelloop/actions/runs/42777542242)
passed 776 management DB, 24 ledger, 21 CPU and 1,836 unit cases (877 skipped),
including the 25 first-publication DB cases. These cover atomic publication,
replay/races, ownership/context/principal refusals, four missing-grant rollbacks,
source-lock synchronization, historical/foreign-edge/pixel/byte-budget refusals and
stale approval restoration. This is prior-head internal evidence, not verification
of the following HTTP binding or full bootstrap/quality acceptance.

The opt-in router now exposes `publishInitialSourceGrouping` with the same strict
request in the generated schemas/client and existing admin/session/CSRF boundary.
`CanonicalManagementOperations` requires an explicit optional `grouping_objects`
reader; missing capability fails closed after actor/context validation, and malformed
reader configuration fails at construction without I/O. The upload store never
implicitly grants read access. The delegate uses the existing publisher unchanged;
no request supplies an object key, threshold or split. Default mounting, admission,
worker startup, production grants, tables, migrations and dependencies are unchanged.

The HTTP binding is verified at `91c197be210917db5dcd3dfd1a91c360b7c7dd71`:
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42781152793)
passed [780 management DB, 24 ledger and 21 CPU cases](https://github.com/TogetherWeOwn/labelloop/actions/runs/42781152793/job/129711304168)
and [1,865 unit cases, 881 skipped](https://github.com/TogetherWeOwn/labelloop/actions/runs/42781152793/job/129711303893).
CodeQL, gitleaks, queue, web/drift, dev-stack, migrations, lint, types and import
checks also passed. Four HTTP cases cover restricted shared-session attribution/
role reload, anonymous and CSRF refusal, missing-reader refusal, exact replay and
conflicting-body refusal, no-regroup/history protection, and assignment/audit
rollback with same-key recovery. Transport/composition/generated-client controls
also passed there. These results do not establish full bootstrap acceptance.

A further disposable DB case is commissioned, not yet verified: the actual
16-image interface fixture goes through normalized canonical ingest and explicit
fixture annotation preparation, then shared-session HTTP source-policy save and
first grouping. No assignment, relation or grouping receipt is seeded. Assertions
cover 15 locked groups, train/validation membership, actual canonical-JPEG pHashes,
annotation-derived class counts, attributed receipt and replay. A covering pure
case checks the actual normalized rasters preserve the fixture's declared group
partition and split allocation. Golden fixture pHashes were measured on uploaded
bytes; they are not substituted for canonical-raster observations.

**The grouping-to-freeze publication gap is retained, not bypassed.** Even with
complete eligible fixture annotations and first grouping, saved source-policy
intent makes the freeze plan unavailable and POST freeze refuses `missing_evidence`
without changing assignments, receipts or releases. Effective setup/project
publication must resolve this through its own validated authority. Intent approval
or a successful grouping receipt is not that authority. Consequently the existing
HTTP-to-CPU cohorts still use explicit seeded grouping; this new fixture case does
not connect first grouping to CPU training or qualify its labels as independent
gold. Default management mounting and real-asset admission remain disabled.

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
