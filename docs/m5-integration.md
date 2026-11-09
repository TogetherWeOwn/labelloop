# M5 qualification and recovery integration

Status: **integration in progress; milestone acceptance is not established**.

Scope is [plan v0.3](plan.md) §§11–12 and acceptance checks 16, 17, 19 and 21.
This record joins independently delivered modules; it does not commission a second
qualification engine, registry, ledger, secret store, UI or backup tool.

## Committed annotation policy changes and historical reads — pending Actions

Both-recipe disposable PostgreSQL controls change the current reviewer prompt,
condition scope and targets to null through separate committed policy appends.
Positive and negative annotation writes use current annotation/registry revisions,
not stale proposal jobs. The identical writes must succeed in a rolled-back
savepoint before each change and after append-only restoration, but reject the
changed policy specifically without appending any annotation/review/completeness/
teacher row. Every registry head/event and qualification journal must remain
unchanged except the single intended policy event per configuration append.

During each changed configuration and after restoration, real authenticated admin
HTTP must still recover the exact original acceptance metadata. Historical reads
must not substitute the changed policy, grant acceptance or mutate evidence. The
existing champion rollback/recovery controls follow these new cases. Assertions
are unverified until exact-head Actions; no local tests ran. No production code,
route, gate, migration, dependency or original execution producer is changed.
REA: reuse immutable original events, the existing current-policy guard and real
session read boundary rather than another policy or provenance engine.

## Authenticated original-acceptance metadata — verified baseline

The admin-session read
`GET /projects/{project_id}/assets/{asset_id}/annotation-history/{revision}/automatic-acceptance`
resolves the exact project/asset UUID pair and consumes the existing immutable
original-event reader. It returns historical qualification/release/evaluation
identities, original pipeline/policy digests and an explicit fixture-only marker.
It never reloads today's policy/champion, returns final scores or population
membership, or grants current acceptance. Missing/legacy/malformed provenance is
unavailable, not backfilled. Reviewers and workers cannot read this admin surface.
No automatic-annotation write route, new storage, migration or dependency is added.

Adapter controls cover both recipes, authentication/permission denial before
canonical access, expired sessions, exact scope, invalid revisions, redacted
failures and outer rollback. Both-recipe PostgreSQL controls recover committed
positive/negative historical metadata through real session HTTP before and after
champion rollback, reject absent/foreign sources and missing original revisions,
and preserve all annotation, registry and qualification rows after successful and
injected post-load-failure reads.

Exact `e356a9fea6aa34ce0fd9bc9763a351ef79302241`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42551076638),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42551076638/job/128946601596),
[unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42551076638/job/128946601436),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42551076308) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42551076366)
succeeded, including queue/migration cycles/all four parent upgrades,
web-build/dev-stack/lint/types/import-contract. These verify the metadata controls,
not the new committed policy-change cases above or complete M5. Totals not
extracted; no local tests ran. This metadata port is not the complete operational
UI projection, original execution proof, independent measurements or domain gold.
REA: reuse original-event validation and the delivered canonical read/auth boundary.

## Committed fixture annotation recovery — verified baseline

Exact `c4b632a440bf5df1d32930b51ae114ba2a42d533`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42549033988),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42549033988/job/128940272916),
[unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42549033988/job/128940273011),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42549033792) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42549033756)
succeeded, including queue/migration cycles/all four parent upgrades,
web-build/dev-stack/lint/types/import-contract. This verifies the committed
annotation recovery controls below, not the new metadata endpoint or full M5.
Totals not extracted; required independent/native reviews remain.

The both-recipe PostgreSQL smoke now separately commits synthetic completeness and
completed proposal inputs, then calls the internal current-champion annotation
service in independent transactions for positive and negative canonical revisions.
Closed-connection readback must recover the exact original policy, qualification,
release digest, final evaluation, both contributing proposal IDs and deduplicated
teacher lineage. A later authenticated champion rollback must leave that historical
annotation evidence unchanged, rather than substitute the restored champion.

Stale registry/annotation revisions, a missing later proposal and an injected
origin-write failure must preserve every annotation revision, review event,
completeness row and teacher origin. Each success must append exactly one row to
each table while preserving all historical and unrelated rows. Registry heads and
all other qualification journals remain unchanged. These assertions passed
exact-head Actions at the baseline above; no local tests ran. These are internal fixture-policy storage
mechanics, not HTTP annotation authorization, real inference/reviewer execution,
source/condition truth, independent measurements or domain gold. Real automatic
annotation remains closed. No production code, schema, dependency or gate changed.

## Current-champion automatic annotation orchestration — verified baseline

Exact `ce1112ededbe3364717f1c99c4708e01cb495bd7`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42547388775),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42547388775/job/128935251616),
[unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42547388775/job/128935251694),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42547388766) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42547388593)
succeeded, including migration round trips/all four parent upgrades, queue,
web-build and dev-stack. This verifies the previously pending service and scoped
remediation controls below, not the new committed annotation extension or complete
M5 acceptance. A missing remediation binding rejects affected cells; unaffected
cells retain acceptance, with nonempty coverage required for both paths.

The internal `automatic_review_service.append_current_review` locks the selected
project, verifies the asset belongs to it, and reuses the existing current-champion
promotion reader. It derives the final evaluation and qualification from that
committed promotion, never a supplied context/report/model/target. Only explicit
fixture policy is permitted. It fixes machine/system approval fields and delegates
to the existing revision writer for current policy, canonical groups, final state,
adverse vetoes, drift, completeness, human-label protection and atomic proposal
origin/revision/event writes. It adds no HTTP grant, migration or dependency.

Adapter controls cover both recipes/positive and negative writes, stale or missing
promotion, missing report, foreign source, unsupported scope, non-fixture policy,
invalid identities/revisions and delegated failures. After actual committed HTTP
promotion, both-recipe PostgreSQL smoke reuses the full annotation exercise through
this service: positive/negative writes, original-policy recovery, two contributing
proposals/teachers, changed-policy and scoped-pause vetoes, and injected revision
and second-origin failure rollback. These annotation controls use a rolled-back
fixture savepoint, not committed annotation recovery or HTTP authorization.

Authentication, original reviewer execution, authoritative source/condition scope
and complete influence selection remain caller obligations. No route constructs
this port from submitted claims; real automatic annotation remains closed. These
savepoint assertions and the committed extension are verified at their respective
baselines above. REA: reuse the recorded promotion reader and atomic annotation
writer, not another policy or provenance engine.

## Committed HTTP promotion canonical-group controls — verified baseline

Exact `e1053c72cbf562b2d79da3a08c5a28444ec31a45`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42542272852),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42542272371) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42542272477)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42542272852/job/128919659370),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42542272852/job/128919659524),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. Canonical-group HTTP promotion controls below are verified;
new service and full M5 acceptance are not. Totals not extracted; required reviews remain.

Both-recipe committed promotion smoke now adds a canonical relation from an actual
CPU training input, actual compiled validation input or canonical development
member to the candidate's final population. The relation commits before fresh
connections check the historical context and reassemble promotion. Both must reject
the specific current-group conflict; the actual admin HTTP request must return
redacted 409/no-store. After client close, both complete registry heads, all promotion
events, qualification journals and the committed relation must be unchanged.

Only each generated relation in the disposable Actions fixture is removed afterwards.
All pre-existing canonical and historical evidence remains intact. Fresh connections
must recover the identical original context after each cleanup, before the existing
same-candidate/request successful promotion and rollback. No production code, gate,
migration or dependency changed. These assertions passed exact-head Actions at
`e1053c7`; no local tests or builds ran. REA: reuse canonical group readers and real promotion transactions.

## Committed HTTP promotion policy-change controls — verified baseline

Exact `39bd6acaee4b1111fc53784205aa3e841b2ce623`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42540674168),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42540673498) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42540673495)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42540674168/job/128914681504),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42540674168/job/128914681554),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. This verifies the policy-change controls below, not the new
canonical-group controls or full M5 acceptance. Totals not extracted; required reviews remain.

Both-recipe committed promotion smoke now appends three valid configuration changes
before the same candidate/request is promoted: reviewer prompt, supported condition
scope, and null targets with their matching acceptance-policy digest. New connections
must recover each change and reject the historical context. The real promotion
service must also reject reassembly at its specific pipeline, coverage or null-target
gate; a stale registry revision or generic broken fixture cannot satisfy that check.
Actual admin HTTP must return redacted 409/no-store, preserving both complete heads,
every promotion event and every qualification journal after client close/reconnect.

Each change and its restoration append exactly one policy event, retaining every
historical row and every other qualification table. Restoring the original policy
must recover the identical qualified context without any registry mutation. The
existing subsequent same-candidate HTTP promotion/rollback remains the positive
control. No production code, gate, migration, dependency or second policy engine
changed. These assertions passed exact-head Actions at `39bd6ac`; no local tests or
builds ran. REA: reuse current policy, persisted outcome assembly and the real promotion transaction.

## Committed HTTP canary freshness and completeness vetoes — verified baseline

Exact `dec34269c3f750f49bde7c2631966c95a964b7b8`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42538901835),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42538901543) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42538901536)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42538901835/job/128909276571),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42538901835/job/128909276652),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. This verifies canary vetoes below, not the new policy-change controls,
independent measurements or full M5 acceptance. Totals not extracted; required reviews remain.

The same synthetic candidate used by the committed HTTP promotion/rollback positive
control now has seven additional canary populations: reused actual compiled training
and validation groups, canonical development and final groups, the ordinary audit
population's group, incomplete observations alone, and complete plus competing partial
outcomes. Each population commits before the service/HTTP reads it. Reused-group
outcomes first pass the existing sample/measurement quality check in isolation;
matching canonical splits prevent unrelated split conflicts from satisfying the test.
These fixtures do not change any CPU manifest, historical outcome or production gate.

The internal service must reject each specific freshness, completeness or ambiguity
gate. Actual admin HTTP promotion must return redacted 409/no-store and preserve both
complete registry heads, every promotion event and qualification journal after client
close/reconnect. The original fresh canary must still recover the identical qualified
context, then the same candidate/request must promote and roll back successfully.
Every rejected outcome remains stored: no score-based cherry-picking or cleanup can
make the positive control pass. The new partial-only fixture option defaults to the
existing complete observations. These assertions passed exact-head Actions at
`dec3426`; no local tests ran. No production code, policy, migration, dependency or second engine changed.
REA: exercise the existing sample/outcome selector and freshness gates through the
real authenticated promotion transaction. Fixtures are software mechanics, not gold.

## Committed HTTP final-test independence vetoes — verified baseline

Exact `8fa5a0a960cda53beb090444db367eb871bd557b`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42537198292),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42537198015) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42537197961)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42537198292/job/128904052870),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42537198292/job/128904052845),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. This verifies the final-test controls below, not the new canary
controls, independent measurements or full M5 acceptance. Totals not extracted;
required Devin/fresh-context/native QA/Security reviews remain gates.

Both-recipe CPU smoke prepares two separate synthetic sole-final candidates, each
with committed canonical populations, selection/exposure, unique audit/canary
outcomes and a same-population incumbent comparison. A fresh-connection preflight
must qualify each candidate before the real admin HTTP endpoint independently
adapts or quarantines its final test. The existing invalidation helper checks
actor/revision recovery, immutable membership and assessments, metadata-only reads,
denial/replay controls and failed-append rollback.

The historical qualification still passes in isolation, but the internal promotion
service must reject specifically because current final-test independence was
invalidated. Actual admin HTTP promotion must then return redacted 409/no-store;
after client close/reconnect, both complete registry heads, every promotion event
and all qualification journals must remain unchanged. Adaptation and quarantine
use separate final populations, so neither control inherits the other's invalidity.
The existing subsequent successful committed promotion/rollback remains the positive
control. No production code, lifecycle gate, schema, migration or dependency changes.
These assertions passed exact-head Actions at `8fa5a0a`; no local tests ran. REA:
reuse the existing sole-final fixture, authenticated invalidation and promotion transactions.

## Committed HTTP support and critical-slice vetoes — verified baseline

Exact `02e82dcabbe7b9921e53cc50687248de35f377e1`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42535731738),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42535731395) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42535731315)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42535731738/job/128899521918),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42535731738/job/128899521831),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. This verifies support/slice vetoes below, not the new final-test
controls or full M5 acceptance. Totals not extracted; required reviews remain gates.

Both-recipe CPU smoke now commits five separate synthetic candidates with one
adverse dimension each: unsupported class, insufficient class support, missing
critical slice, insufficient critical-slice support, or critical-slice AP50
regression against a same-population incumbent. Aggregate mAP remains unchanged.
Each candidate retains its own canonical final population, real selection/exposure,
unique ordinary and candidate-bound canary outcomes, and incumbent comparison.
The internal service must reject with the specific quality-gate error; an otherwise
valid authenticated admin promotion must then return redacted 409/no-store and
preserve both complete registry heads, every promotion event and qualification
journal after client close/reconnect. The existing successful committed promotion
and rollback remain the positive control. No guard, policy, schema, migration or
dependency changed. These assertions passed exact-head Actions at `02e82dc`; no local
tests ran. Synthetic copied metrics prove software gates only, never model quality.
REA: reuse the real promotion service, canonical fixture and HTTP transactions.
Automatic annotation remains an internal port until trusted execution and scope
producers exist; no HTTP acceptance authority is introduced here.

## Committed HTTP promotion and rollback — verified baseline

Exact `ad58f2dc02c00f63c6a32896b9ae84138cf097e2`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42534166735),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42534166369) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42534166415)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42534166735/job/128894691496),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42534166735/job/128894691512),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. This verifies committed HTTP recovery below, not the new support
and slice controls, independent measurements or full M5 acceptance. Totals not extracted.

Both-recipe CPU smoke now prepares a separate synthetic sole-final candidate using
one shared fixture helper; earlier savepoint ambiguity controls retain their exact
setup and rollback. The new setup commits canonical final membership, selection,
exposure, unique ordinary/candidate-bound outcomes and same-population incumbent
comparison. Authenticated HTTP requests use the actual engine, not a borrowed
connection, and no outer fixture transaction surrounds them.

New controls require committed promotion and rollback to recover after each client
closes, with complete metadata, authenticated actor, selected report IDs and typed
qualification. Injected event-append failures must roll back each request's preceding
head write. Stale/already-champion replay must preserve history; both recipe heads,
all prior promotion events and qualification journals are compared after reconnect.
The rollback returns the prior champion without deleting either new event or any
fixture evidence. This runs after the existing drift acceptance controls, before
the shared adverse audit. Copied metrics and synthetic sources remain explicitly
fixture-only, not independent measurement or domain gold. These assertions passed
exact-head Actions at `ad58f2d`; no local tests/builds ran. No production code, schema, migration,
dependency or gate changed. REA: reuse the sole-final fixture and real registry/auth
transactions rather than build another engine or claim savepoints prove durability.

## Authenticated fixture promotion — verified baseline

Exact `2ce222090392ebe34ab053f20f45cd61cf8a31b3`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42532381205),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42532380958) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42532380965)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42532381205/job/128889219126),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42532381205/job/128889219165),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. This verifies the nested HTTP controls below, not the new committed
recovery controls, independent measurements or full M5 acceptance. Totals not extracted.

The existing promotions endpoint now discriminates `rollback` and `promote` without
adding a second route. Only a human admin session with same-origin CSRF can select
the candidate and ordinary/canary population identities. The service derives final,
measured outcomes, current policy, lineage and incumbent comparison. No request may
supply actor, gate results, report/outcome IDs, scope, targets, qualification or the
fixture restriction. Rollback remains limited to the persisted pointer.

Real promotion remains closed at this boundary: the adapter passes a fixed
`require_fixture_policy=True`, requiring the current persisted policy's explicit
`fixture_only` flag even when real targets are configured. Original execution and
measurement provenance is still incomplete. This permits the accepted synthetic
mechanics demonstration, not a domain-quality claim or a real-policy grant. Null
real targets and every existing quality/current-context/veto gate remain fail-closed.

New adapters cover both recipes, strict action/identity/revision fields, every
non-admin principal, CSRF/origin, forbidden authority fields and redacted outer
transaction rollback. Service controls require real-policy rejection before the
registry writer. Both-recipe PostgreSQL controls use real login/session/CSRF and
production stores/service through nested request transactions on the sole-final
fixture connection. They require denied/stale/missing-evidence no-ops, injected
event-append rollback, complete success metadata and authenticated actor, other-recipe
isolation, unchanged qualification journals and stale/already-champion replay
rejection. The enclosing fixture is rolled back: no new committed promotion or
fresh-connection recovery claim is made for these nested controls. These assertions
passed exact-head Actions at `2ce2220`; no local tests/builds ran. No new schema,
migration, dependency or engine.
REA angle: reuse the delivered human-admin boundary and trusted promotion service.

## Trusted promotion orchestration — verified baseline

Exact `cdb33f9a88c432e3b2efe5b382423d43cb11affe`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42530428838),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42530428587) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42530428668)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42530428838/job/128883238146),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42530428838/job/128883238097),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. This verifies the earlier service/ambiguity/atomic-write controls,
not the new HTTP adapter or full M5 acceptance. Totals were not extracted.

The internal `promote_candidate` service takes a trusted candidate, two population
release identities, authenticated actor identity and expected registry revision.
It holds the existing project lock from current registry inspection through context
assembly and the existing atomic head/event write. It accepts no evaluation or
outcome keys, lineage, targets, scope or qualification claims. The assembler derives
the sole final and sole ordinary/candidate-bound outcomes; configured targets supply
the exact registry policy. The service generates the qualification identity.

When an incumbent exists, the shared report selector requires exactly one linked
final report on the candidate's frozen final population. Other populations cannot
substitute for missing comparison evidence. All linked report contracts are checked,
and every matching report counts regardless of score; duplicates fail closed.
The registry retains current-policy/canonical-influence/final-state, durable veto,
class/critical-slice non-regression and atomic rollback gates. Stale revisions and
already-promoted candidates reject before qualification reads.

New adapters cover both recipes with/without an incumbent, scoped delegation,
invalid identities/revisions, stale/repeated decisions, read/write failure propagation
and incumbent population ambiguity/substitution. Both-recipe PostgreSQL controls use
the disposable sole-final candidate: reject absent or competing incumbent comparisons;
inject a failure after the head update and require exact head/event rollback; then
promote, verify complete candidate/rollback metadata, actor, selected report IDs and
stored qualification, and reject stale/repeated decisions without changes. Additional
ordinary/canary outcome competitors must also veto service orchestration. These new
assertions passed exact-head Actions at `cdb33f9`. No local tests/builds ran.

That baseline is not the new HTTP fixture adapter or independent measurement evidence. The
trusted caller still owns candidate/population selection, original execution and
measurement provenance; those authenticated producers/consumers remain incomplete.
No schema, migration, dependency or second qualification/registry engine is added.
REA angle: consume existing persisted selectors and the atomic registry transaction.

## Population-scoped measured-outcome selection — verified baseline

Exact `4b11fb5a0f0d2a357bcf863b3f7bf84e684189b0`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42528689428),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42528689084) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42528689046)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42528689428/job/128877879975),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42528689428/job/128877879865),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. This verifies the population-scoped selection controls below, not
the new promotion service or full M5 acceptance. Totals were not extracted.

A trusted internal caller can supply an `AnnotationOutcomeRef` containing only the
canonical population release key. Assembly selects the sole committed measured
outcome for that project, population and role: ordinary outcomes have no canary
model, and canaries require the exact selected candidate. Both audit and population
project identities are checked in the join. The bounded query counts all matching
outcomes, including partial, adverse and stale-digest evidence. Missing or multiple
outcomes fail closed; scores, completeness, timestamps and digest freshness do not
break ties. The exact reader still validates immutable audit binding, current
canonical groups and the complete current model digest before all quality gates.
Explicit trusted references remain available; partially specified references reject.

New adapters cover query scoping, zero/one/multiple outcomes, exact-reader failures,
strict references and both-recipe assembly/quality delegation. PostgreSQL controls
require ordinary release-only equality and complete-plus-partial canary ambiguity
after reconnect. A disposable sole-final candidate gets a separate unique canary
population for automatic/explicit context equality; additional clean or low-score
outcomes in either population must reject, and savepoint rollback recovers the
unique control without changing registry heads/events or historical evidence.
These assertions passed exact-head Actions at `4b11fb5`. No local tests ran.

Population choice remains a trusted-service duty; uniqueness is not independent
measurement truth, original pipeline execution, HTTP authorization or promotion.
No endpoint, schema, migration, dependency or second qualification engine is added.
REA angle: reuse immutable outcome/audit/population joins and the exact reader.

## Service-owned final-report selection — verified baseline

Exact `babed777ff1ed420e8e43af2a6ae6409edd53151`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42526895513),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42526895218) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42526895134)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42526895513/job/128872425237),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42526895513/job/128872425304),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. This verifies the corrected sole-final fixture below, superseding
its earlier pending label, not the new outcome selector or full M5 acceptance.

The persisted qualification assembler may omit its evaluation key. It reads every
linked report from the scoped complete model release and requires one unambiguous
locked-final report. Every linked report must match its key, recipe, weights,
class map and inference policy. Selection uses neither scores nor recency; multiple
final reports reject even if only one would pass. The existing explicit selector
remains a trusted internal port, not an HTTP request field.

The selected report still needs its exact committed exposure, current canonical
membership, current policy, independent final state, measured audit and fresh
candidate-bound canary. This selects only report identity: it does not authorize
promotion, create measurement truth, choose audit/canary outcomes or expose final
scores. No endpoint, schema, migration, dependency or second qualification engine.

Adapter controls cover both recipes, absent/ambiguous finals, substitutions in
final and development links, unexposed/adapted/quarantined finals and changed report
payloads. At `0c414f2`, [ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42525180854/job/128867149781)
failed **2 tests, with 17 passed**: both recipes reached the selector, which correctly
rejected the shared fixture's multiple historical finals. The positive assertion
incorrectly assumed that existing model had a sole final. Unit (including all 42
new adapters), queue/migration cycles/all four parents, web-build/dev-stack,
lint/types/import-contract, CodeQL and gitleaks passed; overall CI failed.

The corrected smoke preserves the shared model's complete committed history and
requires automatic ambiguity rejection after reconnect. A separate synthetic candidate
in a disposable savepoint reuses its training run/development evidence, adds a fresh
canonical final population, real selection/exposure and its own candidate-bound canary.
It requires equality between automatic and explicit assembly, partial-canary rejection,
and second-final rejection regardless of scores; savepoint rollback restores the
unique control and leaves the original registry heads/events and explicit context
unchanged. This unique-candidate control is transactional, not a new reconnect or
independent measurement claim. No production selector, gate, historical evidence,
migration or dependency changed. Corrected controls passed exact-head Actions at
`babed77`; no local tests/builds ran. REA angle: reuse immutable
model-evaluation links, the final journal and the existing assembler, not another
selection or qualification store. Trusted execution/measurement producers,
service-owned outcome selection, authenticated promotion/automatic annotation and
expanded restore/operational UI acceptance remain unfinished.

## HTTP canonical-group resumption replay controls — verified baseline

Exact `987e973896eea7f9ddf1336830083e2b44e38bdd`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42523401284),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42523400749) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42523400781)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42523401284/job/128861624563),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42523401284/job/128861624518),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. This verifies the canonical-group HTTP replay controls below,
not the new final-report selector, full UI/expanded restore or milestone acceptance.
Totals were not extracted. Required Devin/fresh-context/native QA/Security remain.

Both-recipe disposable PostgreSQL smoke commits a new canonical relation after
remediation promotion, connecting an actual compiled training input, an actual
compiled validation input, or a canonical development member to the final-test
population. A fresh connection must recover the conflict and the existing current
context guard must reject the historical qualification. The otherwise-valid admin
HTTP resumption must then return redacted 409 with `no-store`, preserving every
qualification table, complete recipe heads, promotion events and all relation rows.

Only the generated conflict relation is removed during disposable fixture cleanup;
all pre-existing canonical relations and every qualification row remain unchanged.
Each control rechecks the restored valid context. The existing identical successful
HTTP resumption follows all controls, so invalid incidents/revisions or a permanently
broken fixture cannot satisfy rejection alone. The shared snapshot now includes
samples, selections and exact-revision teacher origins as well as policy, drift,
final-test, audit and measured-outcome history. No production code, migration,
dependency, gate or second grouping engine changed. These controls passed exact-head
Actions at `987e973`; no local tests or builds ran. REA angle: exercise the existing canonical
readers through the real session/resumption transaction, not another lifecycle engine.

## Metadata-only final-test inspection — verified baseline

Exact `35d9035f21614e9f4972273155aa4f9316c86db3`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42521793412),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42521792815) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42521792793)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42521793412/job/128856701903),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42521793412/job/128856701951),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. This verifies the metadata-only lifecycle reads below, not the new
group-replay controls, full UI/expanded restore or milestone acceptance. Totals were
not extracted. Required Devin/fresh-context/native QA/Security gates remain.

## Authenticated final-test lifecycle inspection — verified baseline

`GET /projects/{project_key}/dataset-releases/{release_key}/final-test-state`
uses the existing human-admin session boundary and scoped, project-locked journal
reader. It returns only release identity, current journal revision, effective role
and a quarantine boolean, sharing the invalidation response projection. It never
returns membership, scores, pass/fail, assessments, reasons or qualification; no
final exposure or other journal write is performed. Missing scope, invalid state
and storage errors are redacted. Responses remain `no-store`.

Adapter controls cover lifecycle states, anonymous/reviewer/service denial, strict
path bounds and outer-transaction rollback on errors. Both-recipe disposable
PostgreSQL HTTP invalidation controls now inspect state before/after committed
adaptation and quarantine over fresh connections, require exact metadata-only
responses, and preserve every policy/acceptance/final/audit/measured-outcome row,
both registry heads and promotion history on reads. Missing/foreign scope and an
injected post-load read failure are covered. These assertions passed exact-head
Actions at `35d9035`; no local tests or builds ran. No migration, dependency, design or second
lifecycle engine. Operational UI mounting, authenticated promotion/automatic
annotation, trusted execution/measurement provenance and expanded restore remain
unfinished. REA angle: reuse the scoped lifecycle reader and existing invalidation
metadata, not expose the final result or invent another operational state store.

## HTTP current-policy replay controls — verified baseline

Exact `efc2835ac96ec70dc877f904f3df9b4609f7ccfa`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42520061639),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42520061258) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42520061259)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42520061639/job/128851413874),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42520061639/job/128851413880),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. This verifies the policy replay controls below, not the new
lifecycle read, full UI/expanded-restore or milestone acceptance. Totals were not
extracted. Required Devin/fresh-context/native QA/Security gates remain.

## HTTP current-policy resumption replay controls — verified baseline

Both-recipe disposable PostgreSQL smoke now commits three valid policy changes
*after* the remediated champion's promotion: reviewer prompt identity, supported
scope, and null targets with their matching acceptance-policy digest. A fresh
connection must recover each version and the real current-policy reader must
reject the recorded promotion context. The same authenticated admin resumption
request, with current acceptance/registry revisions and real incident IDs, must
then return a redacted 409 without changing any policy, acceptance, final-test,
audit or measured-outcome row, either recipe's registry or promotion history.

Each control restores the original configuration through a new expected-revision
append, preserving every historical policy row. Restoring configuration alone
must not resume acceptance. The existing successful HTTP resumption follows all
three controls using the unchanged champion/incidents/request, so a generic
stale-revision or incident rejection cannot satisfy the negative controls alone.
No production code, domain gate, schema, migration or dependency changed; these
assertions passed exact-head Actions at `efc2835`. No local test or build ran.

## Incident-linked resumption — verified baseline

Exact `200f5b3ae9e225af850b2674e74ab91fee9cbd44`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42518322944),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42518322738) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42518322632)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42518322944/job/128846139949),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42518322944/job/128846140016),
queue/migration cycles/all four parent upgrades, web-build/dev-stack/lint/types and
import-contract. This verifies the previous authenticated resumption controls,
superseding their pending labels below; it does not verify the new policy-replay
controls or establish complete milestone acceptance. Totals were not extracted.
Required Devin/fresh-context/native QA/Security gates remain.

## Authenticated incident-linked resumption — verified baseline

`POST /projects/{project_key}/registry/{recipe_key}/resumptions` accepts only
expected acceptance/registry revisions and a bounded list of incident IDs, behind
human-admin sessions, same-origin and CSRF. The service reuses the containment
reader to select the current champion's committed promotion context and final
report identity. It cannot accept a caller's candidate, evaluation, qualification,
scope or actor. Missing historical promotion evidence fails closed.

The existing resumption transaction rechecks current policy, canonical influence,
final-test state and durable adverse-audit vetoes. Its domain transition requires
a new qualified release covering affected scope, with qualification and fresh
canary bound to every incident. Rollback or a historical promotion alone cannot
resume acceptance. No evidence is manufactured from current policy, and this port
does not create a qualification, measurement or promotion.

Adapter cases cover identity substitution, service delegation, authentication,
strict selectors and redacted outer rollback. Both-recipe persisted HTTP controls
replace the successful direct-store resumption and retain reconnect/qualification
and subsequent automatic-acceptance checks. They additionally require rejection
of the original incident release, denial/stale/foreign/duplicate no-ops, post-insert
failure rollback, exact session actor and unchanged history/registry/other recipe.
These assertions passed exact-head Actions at `200f5b3`; no local tests or builds ran.
No migration, dependency, domain transition or duplicate drift engine. REA angle: revalidate
committed promotion evidence through the existing append-only acceptance journal.

## Containment fixture correction — verified baseline

Exact `e16d0e29db8820d9a86ce5fc43e8b2dcd0d5227a`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42516579597),
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42516579299) and
[gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42516579307)
succeeded, including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42516579597/job/128840848870),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42516579597/job/128840848840),
queue/migration cycles/all four parent upgrades, dev-stack/web-build/lint/types and
import-contract. Corrected both-recipe typed foreign-history service rejection and
persisted HTTP containment controls are verified, superseding pending notes below.
No resumption, complete HTTP/UI/expanded-restore or milestone acceptance is inferred.
Required independent/native review gates remain. Totals were not extracted.

## Authenticated scoped drift containment — verified baseline

`POST /projects/{project_key}/registry/{recipe_key}/pauses` requires the existing
human-admin session, same-origin and CSRF boundary. Strict input accepts expected
acceptance and registry revisions, a bounded reason, and optional class/source/
condition selectors. The service expands selectors only over the current champion's
recorded promotion scope. It derives historical qualification identity and a new
incident identity; callers cannot supply releases, qualifications, cells, principal,
resume or clearing authority. The shared project lock survives through append and
outer commit. A stale head cannot redirect containment to another champion.

Historical promotion evidence is inspected for identity, not granted again. Later
linked evaluations may extend the same release; rollback retains its original
qualification lineage. Containment does not require the now-invalid champion to pass
current quality/final/policy gates. Missing historical promotion provenance rejects
rather than substituting today's policy; legacy/imported provenance is still an
explicit service gap. No resumption, automatic acceptance or promotion grant is added.

Adapter cases cover both recipes, server-expanded axes, extended report history,
missing/substituted provenance, current-gate independence, authentication, strict
inputs, attribution and outer-transaction rollback/redaction. Both-recipe PostgreSQL
HTTP smoke replaces its direct operational pause with real admin HTTP after rollback.
It covers denials/stale/foreign/unknown scope, injected post-insert failure rollback,
reconnect, historical qualification/session actor recovery, exact scoped containment,
unchanged historical rows/other recipe/registry events and stale replay rejection.
These are new unverified assertions; no local tests or builds ran.

No migration, dependency, second drift engine or UI design. Trusted original execution,
measurement/score/threshold/source/condition provenance, service-owned selectors for
promotion/acceptance/resumption and expanded restore/UI acceptance remain incomplete.
Real null targets stay fail-closed. REA angle: reuse persisted promotion scope and the
existing append-only acceptance journal, never a request-supplied qualification grant.

## Composite-key invalidation fixture repair — verified baseline

Exact `ec8d80e2091296643e7a0888fd00175d8a9c9986`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42513291908) succeeded,
including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42513291908/job/128830806323),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42513291908/job/128830806255),
queue PostgreSQL, migration cycles/all four parent upgrades, web-build, dev-stack and
lint/types/import-contract. [CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42513291456)
and [gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42513291436)
also succeeded. This verifies the composite-key repair and persisted HTTP whole-test
invalidation/replay controls, not the new containment slice or milestone acceptance.
Totals were not extracted. Required independent/native reviews remain gates.

Exact `004451c734951ffb9135dadc85144c9a80aff8c7`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42511849574) failed:
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42511849574/job/128826445149)
reported **2 failed, 17 passed**. Both recipes reached the new HTTP invalidation
fixture, but its pre-request journal snapshot raised `AttributeError: id`.
Final-test and acceptance journals have composite primary keys, not an `id` column;
none of the new HTTP invalidation assertions completed. Unit, queue PostgreSQL,
migration cycles/all four parent upgrades, web-build, dev-stack, lint/types/import
contract, CodeQL and gitleaks passed, but do not clear failed CI.

The fixture now orders each journal by its actual primary key and identifies the
new event by full-row difference, additionally requiring every prior row to remain
unchanged. The existing failing both-recipe controls are the regression cases.
No production store, transition, migration, dependency or gate changed. Corrected
persisted assertions passed exact-head Actions at `ec8d80e`; no local tests or builds ran.

## Authenticated whole-test invalidation — verified baseline

`POST /projects/{project_key}/dataset-releases/{release_key}/final-test-events`
permits only human admins, using the existing session, same-origin and CSRF boundary.
The strict body accepts only `adapt` or `quarantine`, a bounded nonblank reason and
an expected journal revision. The existing store reloads the scoped persisted test
under the project lock, applies its whole-release transition and appends the session
actor in one transaction. No membership, role, principal, selection, qualification,
reset or independence-restoration input is accepted. The response contains only
release/revision/effective-role/quarantine metadata, never final scores or assessments.
Missing scope returns 404; rejected transitions return 409; storage failures return a
redacted 503 and roll back. This is invalidation, not exposure or qualification.

Thirty-one adapter cases cover both actions, attribution, authentication, CSRF,
strict inputs and outer-transaction rollback/redaction. The existing both-recipe
PostgreSQL drift smoke now obtains its adaptation and quarantine through real admin
HTTP. Denial/stale/foreign/duplicate controls, post-insert injected failure rollback,
reconnect recovery and exact session-actor events precede retained actual registry
and automatic-acceptance replay rejection. Frozen membership and prior assessments
must remain identical; complete registry heads/events and acceptance history must not
change. These assertions passed exact-head Actions at `ec8d80e`; no local tests ran.

No new state machine, store, migration, dependency or UI design. Authenticated
promotion/automatic-annotation/operational consumers, trusted original execution and
measurement provenance, service-owned selectors and expanded restore/UI acceptance
remain unfinished. REA angle: mount the existing append-only final-test invalidator
behind sessions and retain its actual consuming transaction checks.

## Authenticated sample-bound audit writes — verified baseline

Exact `153d2e79e6f77398467809010bb536e3d39cbccd`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42510132085) succeeded,
including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42510132085/job/128821218010),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42510132085/job/128821217996),
queue PostgreSQL, migration cycles/all four parent upgrades, web-build/dev-stack,
lint/types/import-contract, CodeQL and gitleaks. This verifies the adapter and
persisted audit-write/veto controls below, not the new final-test invalidation route
or complete milestone acceptance.

`POST /projects/{project_key}/dataset-releases/{release_key}/audits` accepts bounded,
strict human observations against an existing persisted sample. Admins and reviewers
use the existing human-session, same-origin and CSRF boundary; service principals
cannot write independent human observations. The session supplies both actor and
reviewer identities. Request bodies cannot supply a verdict, qualification, policy,
reviewer identity or sample membership. The existing store checks exact frozen
membership, sample selection, revision and independence before appending evidence.
The outer transaction commits before the 201 response; failures are redacted and
roll back. Duplicate audit identities reject with 409 and cannot replace evidence.
The response acknowledges storage only, never qualification or independent truth.

Twenty-eight adapter cases cover authority, strict input, session attribution,
transaction rollback and redacted scope/conflict/database errors. The existing
PostgreSQL registry-veto smoke now records its actual wrong-identity and missed-object
observations through authenticated reviewer HTTP rather than a direct store call.
It checks denial and stale/foreign/duplicate observations without writes, injected
post-insert failure rollback, persisted actor/sample recovery and replay rejection.
The existing clean-replacement, actual promotion/rollback and descendant veto
assertions then consume those HTTP-written records with unchanged heads/events.
These audit assertions passed exact-head Actions at `153d2e7`; no local tests ran.

No new store, migration, dependency, independent measurement producer or qualification
engine. The service cannot sample arbitrary request-supplied candidates, clear old
vetoes, or turn a clean membership observation into promotion authority. Original
execution/measurement/score/threshold/source/condition provenance, service-owned
outcome selection, authenticated promotion/automatic-annotation/operational consumers
and expanded restore/UI acceptance remain unfinished. REA angle: mount the existing
sample-bound immutable audit writer behind the delivered session boundary and retain
its actual registry-veto smoke, rather than create another audit engine.

## Authenticated registry and drift inspection — verified baseline

Exact `cdb9fc5a9f0f22ebde70d30fdb07867fd3c03c3c`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42508299861) succeeded,
including unit, ML smoke, queue PostgreSQL, migration cycles/all four parent upgrades,
[web-build](https://github.com/TogetherWeOwn/labelloop/actions/runs/42508299861/job/128815664656),
[dev-stack](https://github.com/TogetherWeOwn/labelloop/actions/runs/42508299861/job/128815664703)
and lint/types/import-contract. CodeQL and gitleaks also passed. The read controls
below are verified on that head, not the new audit-write controls or final M5 acceptance.

`GET /projects/{project_key}/registry/{recipe_key}` requires an authenticated human
admin and returns complete registry metadata plus the persisted acceptance journal.
It reuses the existing readers, taking the project lock before both reads in one
outer transaction. Missing project/recipe returns 404; invalid stored state returns
409; database errors return a redacted 503 without partial state. Responses remain
`no-store`. A champion or an empty pause history is not a qualification grant. The
response deliberately has no invented `qualified` flag or caller-supplied authority.
Promotion and resumption remain closed until their trusted producer/service gaps close.

New both-recipe adapter controls cover roles, scope validation, same-transaction
read ordering, complete release/rollback metadata, active and resumed incident
lineage, empty history and redacted partial-read failures. The existing disposable
PostgreSQL HTTP smoke now reads the persisted resumed history before and after each
rollback, appends a synthetic scoped pause through the existing store, then reloads
it through actual authenticated HTTP. Reads must leave heads, promotion events and
acceptance state unchanged, including after an injected read failure. These read
assertions passed exact-head Actions at `cdb9fc5`; the accepted UI is not yet connected
to this port and no operational-view acceptance is claimed.

Resolved the actual `main.py` conflict with delivered annotation reads, retaining
both routers and the delivered module/tests unchanged. No new migration, dependency,
UI design, runtime or policy engine. REA angle: reuse the existing registry and drift
journal readers under their existing project-lock protocol, not another state store.

## Persisted authenticated rollback — verified baseline

Exact `156fc251c95264425137a3b5e7bf49559957ccfc`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42506562699) succeeded,
including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42506562699/job/128810386259),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42506562699/job/128810386345),
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42506562699/job/128810386287),
migration cycles/all four parent upgrades, web-build, dev-stack and lint/types/import
contract. [CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42506565351)
and [gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42506567547)
also succeeded. This verifies the previous rollback slice below, not the new read
port, full operational UI, promotion or expanded-schema restore acceptance.

The existing CPU registry smoke now drives the actual mounted HTTP route, session
login, CSRF/admin permission boundary and registry transaction against Actions'
disposable PostgreSQL. Only connection configuration is replaced; no dependency,
authentication or registry function is bypassed. Both recipes roll back twice,
recover complete heads over new connections, retain the other recipe unchanged,
and check exact authenticated-actor promotion events with complete before/after
release metadata. Anonymous/reviewer/service requests, missing/wrong CSRF or origin,
caller-supplied authority, missing scope and stale revisions must leave both heads
and all promotion events unchanged. An injected event append failure after the head
write must return a redacted 503 and undo the head write. Stale HTTP replays cannot
append duplicate events. After durable adverse ordinary-label observations and a
clean replacement sample, actual admin HTTP rollback must still reject the affected
recipes with complete heads/events unchanged.

These assertions passed exact-head Actions at `156fc25`, linked above. They establish
fixture-only recovery mechanics, not original pipeline execution, domain quality,
authenticated promotion or automatic annotation, operational views or expanded-schema
restore acceptance. That test-only slice changed no production code, migration,
dependency, policy or gate. REA angle: reuse the delivered session/auth middleware
and atomic registry store inside the existing CPU smoke.

Baseline `3f835616b03bd91c9dbde4a5ed895e0f60c34643`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42504965433) succeeded,
including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42504965433/job/128805542555),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42504965433/job/128805542585),
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42504965433/job/128805542497),
migration cycles/all four parent upgrades, web-build, dev-stack and lint/types/import
contract. [CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42504967751)
and [gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42504970079)
also succeeded; the scanner passed its 20 controls and full-history scan. This
supersedes the older unresolved-scan/pending-baseline notes below, not the remaining
milestone gaps. Required Devin, fresh-context and native QA/Security reviews remain
gates. Real targets stay null and fail closed. The fixed 2026-11-09T12:00:00Z
checkpoint and exact-gap/owned-fallback obligation are unchanged.

## Original-policy sampling at qualification consumers — pending Actions

Strict producers now bind the complete original-pipeline digest into the immutable
population fingerprint. Strict reads require that binding and reload original
acceptance events for every member, including unselected members. Generic and
pre-binding snapshots remain available for historical inspection but cannot be
upgraded into original-provenance proof by passing today's policy.

For non-fixture targets, both persisted context assembly and the shared consuming
transaction guard require current canonical groups and exact original provenance
for the annotation and canary samples. This covers registry promotion, automatic
acceptance and drift resumption. The server-owned current policy must match before
those consumers run; caller-provided fixture flags are not a grant. Explicit
fixture-only mechanics remain distinct and never establish adjudicated truth.

Adapter cases cover both recipes, whole-population recovery, missing/legacy/changed
provenance, foreign project, fingerprint/selection substitution and mandatory
consumer calls. Disposable PostgreSQL controls reload real positive/negative
annotation samples, reject changed-pipeline reads, and reject legacy synthetic
samples at assembly and actual registry/acceptance consumers under non-fixture
policy while preserving complete heads/event counts. These assertions await Actions.
No migration, codec root, endpoint, dependency or second sampling engine is added.

Baseline `60530273857f2e1b23e4bf62a881b772770ebbbf`
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42499984223) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42499983862) succeeded,
including [ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42499984223/job/128790465046).
Separate [gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42499983912/job/128790428585)
failed on a historical generic-api-key match: `compile_request`'s static
`compile-evaluation-640` idempotency key in
`apps/api/tests/test_management_canonical_db.py:419`, commit `5aa876d`.
The helper passes the value to `MutationContext.bind` as a compilation operation
key, not a provider credential. Metadata and AST dataflow were inspected without
printing matched secret fields. No scanner configuration, historical revision or
ignore entry was changed; the failed gate remains unresolved, not a scan PASS.

Original reviewer execution, score/threshold and source/condition/measurement
producers, authenticated services and expanded restore/UI evidence remain
unfinished. Real null targets fail closed; fixture mechanics are not domain gold.
REA angle: reuse original review-event reads and the single canonical sampler in
existing transaction-bound consumers, not another provenance or qualification engine.

## Server-derived qualification lineage — pending Actions

The existing context assembler can now derive lineage without a supplied assertion.
It uses the same persisted checkpoint/teacher/reference input reader for training
and recorded reference groups, and the committed final exposure for development
and optional calibration identities. Canonical evaluation readers still reload
complete current membership and enforce identity/role. The existing qualification
engine and input-isolation checks still reject final/fresh-canary reuse of training,
compiled selection, recorded selection/calibration or recorded reference groups.
No second grouping/ancestry engine, endpoint, schema, migration or dependency is added.

Trusted callers may retain conservative additional lineage assertions; omission of
an actual recorded group still fails closed. Derivation does not invent missing
historical/external/imported influence, original acceptance/reviewer execution,
score/threshold calibration, source/condition truth or independent measurements.
The authenticated service still owns outcome selection; there is no new HTTP grant.
Ordinary sampling still needs explicit handling of missing original automatic
provenance and human attribution rather than backfilled policy labels.

Both-recipe adapter cases cover derived training/reference influence, optional
calibration, missing/conflicting readers and final/canary reuse. Both-recipe
PostgreSQL controls reconnect and assemble without a lineage argument, compare
against independently reloaded input groups, consume the context in the actual
automatic-acceptance guard and reject a clean canary on a compiled selection group.
They retain complete registry heads/event count. New assertions await exact-head
Actions; copied fixture metrics are software mechanics, not domain gold.

Baseline `febaf9062e1c42d3556a668915fb5d878a5171f8`:
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42496406613) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42496406204) succeeded,
including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42496406613/job/128779638747),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42496406613/job/128779638888),
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42496406613/job/128779638738),
migration round trip/all four parent upgrades, dev-stack, web-build and lint/types/
import-contract. This verifies the original snapshot slice below, not this new
lineage derivation or HTTP/UI/expanded-restore/milestone acceptance. Required
independent reviews remain gates. Real null targets fail closed; the fixed
2026-11-09T12:00:00Z checkpoint and owned fallback are unchanged.

REA angle: reuse the current stored selection and canonical input/evaluation
readers instead of a second lineage producer or request-supplied group lists.

## Original automatic-acceptance policy snapshot — verified baseline

After the current qualification/drift guard succeeds, the annotation transaction
now records the checked pipeline and qualification targets using the existing
strict `QualificationPolicy` codec in the immutable review event. It also retains
the qualification identity, complete release digest, final evaluation identity,
actual accepted scope, and fingerprint of every canonical revision decision field.
The snapshot shares the revision/teacher-origin savepoint, including negatives
and accepted labels that are not yet training-eligible. Human/proposed revisions
do not acquire an automatic-acceptance snapshot.

The exact-revision reader checks project/source, parent revision, unique apply
event, principal identity, record schema, canonical fingerprint, typed policy and
scope. Historical revisions lacking the snapshot fail closed; no current champion,
policy or latest annotation is substituted. Existing event mutation guards apply.
No new table, migration, endpoint, dependency or core codec is introduced.

Adapter controls exercise positive/negative round trips on both recipes, missing/
duplicate events, substituted revisions/principals, malformed policy/scope/digests,
legacy records and pre-write failure. Both-recipe PostgreSQL annotation exercises
now reload the actual saved snapshot, change current reviewer policy and retain the
original snapshot while rejecting old-context acceptance, and verify injected
revision/origin failures leave no next-revision provenance before a successful retry.
These assertions passed exact-head Actions at `febaf90`, linked above; no local
tests ran and no HTTP/UI/expanded-restore or milestone acceptance is inferred.

This records the policy actually checked by the internal acceptance guard, not
proof the pinned reviewer/prompt/reference pipeline executed, score/threshold
calibration, authenticated source/condition truth or independent measurement.
Ordinary population sampling does not yet require this reader; historical and
human acceptance attribution still need explicit handling. Authenticated consumers,
full audit/canary provenance and expanded restore/UI acceptance remain unfinished.
Real targets remain null and fail closed; synthetic fixture policy is preserved
as fixture-only. The fixed 2026-11-09T12:00:00Z checkpoint is unchanged.

Previous exact `5a574009af70dbbc8f8b8cf4235d53d273e9d2e8`:
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42494499212) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42494498908) succeeded,
including unit, ML smoke, queue PostgreSQL, migration round trip/all four parent
upgrades, dev-stack, web-build and lint/types/import-contract. This verifies the
prior reference slice, not the new snapshot reader or milestone acceptance.

## Recorded proposal reference isolation — historical preparation

Annotation review events now retain every supplied completed proposal run in their
existing correction JSON, atomically with the revision and teacher origins. The
shared completed-proposal reader checks job/project/source/expected revision,
application generation, result/stage hashes and immutable inputs once per read port.
Reference assets come from all pinned job references, including unused alternatives,
not a new caller-supplied reference-group declaration. Empty reference collections
are explicit; missing collections and substituted result identities fail closed.

The existing checkpoint/teacher run closure now recovers reference influence from
these events at or after the exact annotation revision. Later corrections retain
influence; earlier frozen revisions and unrelated runs do not acquire it. Canonical
group membership is rechecked for each reachable assignment version through the
existing transitive-group reader. Qualification assembly and actual registry,
automatic-acceptance and drift consumers reject omitted reference groups and their
reuse as evaluation holdouts or fresh canary evidence. This is not proof of reference
approval, original reference-pack execution or completeness of historical lineage.
Unlinked historical reviews, external detectors/imports and omitted contributing
proposals remain explicit trusted-producer gaps, never inferred teacher/reference-free.

Inspection found review events were append-only in the service, but not protected
against database update/delete/truncate. New reversible migration `qualification_0008`
follows `m5_auth_0001` and adds those guards without changing historical rows or any
table shape. The reference read takes a global `SHARE ROW EXCLUSIVE` review-event
lock through outer commit. Validators serialize because automatic acceptance appends
an event later; a shared-lock upgrade across projects could deadlock. Keep these
transactions short. No new table, endpoint, codec or dependency is introduced.

New adapter cases cover completed-input substitution, empty/duplicate references,
revision boundaries, multiple proposals, assignment versions, current conflicts,
omitted lineage and both-recipe assembly/consumer replay. Both-recipe PostgreSQL
controls journal a real annotation write, reject event mutation, retain the earlier
compiled-revision boundary, and introduce a clearly synthetic revision-2 ancestor
through the existing teacher graph. Omitted/canary-reused reference groups must be
rejected by assembly and actual registry/acceptance with unchanged complete heads
and event count. Copied synthetic model metrics are mechanics, not retraining,
inference execution, independent truth or domain gold. Assertions await Actions.

Prior exact `3d83e402d794a64bac884ff1cbe2bebd40e60804`:
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42492177136) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42492176878) succeeded,
including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42492177136/job/128766756899),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42492177136/job/128766756811),
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42492177136/job/128766756856),
migration round trip/all four parent upgrades, dev-stack, web-build and lint/types/
import-contract. This verifies recorded selection isolation, not this new reference
slice or HTTP/UI/expanded-restore acceptance. Real null targets remain fail-closed;
trusted original acceptance/pipeline/score/threshold, source/condition and independent
measurement producers plus authenticated consumers remain unfinished. The fixed
2026-11-09T12:00:00Z checkpoint and Engineering Lead's owned fallback are unchanged.

## Recorded selection and calibration isolation — historical preparation

The existing compiled-input ancestry closure now also selects persisted candidate
selection journals for every registered checkpoint of each reachable run. Complete
canonical development/calibration populations are reloaded using the existing group
reader; typed selections must match their stored project/model/key and linked
reports. All recorded selections contribute, not only the one in the candidate's
current final exposure. Later report links can extend the atomic-release digest,
but never erase earlier selection influence. This is conservative influence
accounting, not authorization to reuse a historical selection for final exposure.

Recorded selection/calibration groups cannot become independent final or fresh
canary evidence in assembly or consuming registry/acceptance/drift transactions.
Shared development remains permitted. There is one ancestor traversal, no new
schema/codec, migration, endpoint or dependency. Reference and external/imported
influence still require trusted producers and are not inferred from missing rows.

Seventeen new reader adapter cases plus sixteen additional assembly/consumer cases
cover canonical reload, calibration, historical digest retention, identity/report/
root substitution, missing evidence and irrelevant-run exclusion. Existing graph
cases assert reuse of the exact checkpoint/teacher closure. Both-recipe PostgreSQL
controls add an unselected sibling checkpoint on the same stored run with a
noncompiled development or calibration release, then persist a clean candidate-bound
canary on that used group. Assembly and actual registry/acceptance consumers must
reject it without changing complete heads/event counts. These new assertions remain
unverified until exact-head Actions; synthetic scores are not domain gold.

Preceding exact `57046b1655343b6e1f5a47d23993cab3b0015531`:
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42490432167) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42490431737) succeeded,
including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42490432167/job/128761420933),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42490432167/job/128761420910),
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42490432167/job/128761421078),
migration round trip/all four parent upgrades, dev-stack, web-build and lint/types/
import-contract. This verifies compiled selection isolation, not the new recorded
selection reader or final HTTP/UI/expanded-restore acceptance. Real null targets
remain fail-closed. Trusted original acceptance/pipeline/score/threshold, source/
condition and independent-measurement producers plus authenticated consumers remain
unfinished. The fixed integration checkpoint and owned fallback remain unchanged.

## Compiled checkpoint-selection isolation — historical preparation

The existing compiled-input reader now returns distinct training and validation-use
sets from one traversal of the same stored checkpoint/recorded teacher ancestry.
Validation guided checkpoint selection, including for unselected checkpoints and
unregistered ancestor runs; it is not weight-fitting data, but cannot become
independent final or fresh canary evidence. Unrelated runs are excluded. All source
revisions, assignments and current transitive relations retain the existing checks
and locks. Compiled test members are checked, not assumed accessed.

Both persisted assembly and the transaction-bound registry/acceptance/drift guard
reject final/canary groups that overlap those derived selection groups. Shared
current development data remains permitted. The old training-only read delegates
to the same reader, so there is no second ancestry engine, repeated graph per use,
new endpoint, schema/codec change, migration or dependency.

Thirty-six new adapter cases cover inherited validation through checkpoint/teacher
edges and mixed cycles, exclusion of unrelated/test groups, both recipes and both
assembly/consumer paths, rejection of final/canary reuse and shared-development
controls. Paired PostgreSQL controls persist a clean synthetic candidate-bound
canary on an already-used validation group, require rejection by assembly and the
actual registry/automatic-acceptance transactions, and retain complete heads/event
count. A new validation-to-audit relation also rejects historical-context replay.
These new assertions await exact-head Actions; no local tests or builds ran.

Preceding exact `70b7094a711c140212b91a7e202fa925b0ec0254`:
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42488733871) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42488733601) succeeded,
including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42488733871/job/128756244112),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42488733871/job/128756244068),
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42488733871/job/128756244016),
migration round trip/all four parent upgrades, dev-stack, web-build and lint/types/
import-contract. This verifies prior canonical training reload, not the new selection
checks or final HTTP/UI/expanded-restore acceptance. Reference use and noncompiled
ancestor selection/calibration, original pipeline/acceptance/score/threshold/source/
condition and independent measurement producers remain unfinished. Real null targets
stay fail-closed; synthetic mechanics are not domain gold.

## Canonical training reload in consuming transactions — historical preparation

The shared current-context loader now requires the consuming candidate/champion's
stored model identity. It reloads every compiled checkpoint/recorded label-teacher
ancestor through the existing training-group reader under the project lock, and
rejects omitted weight-fitting groups or newly conflicting canonical relations
before returning a context. Promotion, automatic acceptance and drift resumption
pass their actual stored model ID, not an identity selected by evidence. Canonical
locks remain held through the outer decision transaction. No new endpoint,
migration, dependency or ancestry/grouping engine was added.

Ten new adapter cases cover both recipes, exact current reads, conservative extra
lineage, missing/omitted groups and reader failures before evaluation loading.
Paired PostgreSQL controls replay omitted training lineage and a new relation
through actual registry and automatic-acceptance consumers, requiring unchanged
complete heads/event count. Synthetic registry/drift/journal preparation retains
actual compiled ancestor groups alongside conservative audit lineage. These new
assertions await exact-head Actions; static checks are not test acceptance.

Preceding exact `922692bb2358c856af468d123c0fc38bb7c0abe6`:
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42487013314) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42487013037) succeeded,
including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42487013314/job/128751007668),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42487013314/job/128751007679),
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42487013314/job/128751007602),
migration round trip/all four parent upgrades, dev-stack, web-build and lint/types/
import-contract. This verifies the earlier direct canonical evaluation reload,
not these new training controls or final HTTP/UI/expanded-restore acceptance.
Reference/ancestor selection-use lineage, original acceptance/pipeline/measurement
provenance, authenticated consumers and expanded restore/UI evidence remain open.
Real null targets fail closed; synthetic fixtures are not domain gold.

## Canonical evaluation reload in consuming transactions — historical preparation

The shared current-context loader now rechecks development, optional calibration
and final canonical populations before registry promotion, automatic acceptance or
drift resumption can consume an older context. Both the historical final snapshot
and current journal must match canonical membership. Missing frozen members or new
transitive conflicts fail closed, with canonical locks held to outer commit.
This is not HTTP authorization or proof of reference/ancestor-selection lineage,
original pipeline execution, independent audit truth or complete training lineage
in direct callers. Those remaining producer/consumer obligations remain explicit.

Twenty new both-recipe adapter cases cover successful current reads, each missing
population, group substitution, calibration and current-journal substitution.
PostgreSQL smoke replays an assembled context through both actual registry and
acceptance transactions after new development/final relations, requiring rejection
and unchanged complete heads/event counts. Registry/drift fixtures now use separate
synthetic canonical populations, not declared groups over copied training members.
Copied scores are still fixture-only, not independent measurements or model gold.
No schema, dependency, endpoint or second grouping/qualification engine was added.
These new assertions await exact-head Actions; no local tests or builds ran.

Previous exact `f97be6b5e5683c8c9afbbad4e627bbf8307f77b2`:
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42485473501) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42485473034) succeeded.
[Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42485473501/job/128746394470),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42485473501/job/128746394560),
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42485473501/job/128746394519),
migration round trip/all four parent upgrades, dev-stack, web-build and lint/types/
import-contract passed. This verifies the earlier assembler and corrected assertion,
not the new direct reload or remaining HTTP/UI/expanded-restore acceptance.

## Canonical evaluation-group consumer — historical assertion correction

At first consumer head `4304ffc34f1419b55053aea2cc93ba69d6987cde`,
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42485253073/job/128745648441)
reported **2 failed, 17 passed**. Both recipes successfully loaded the independent
canonical fixtures, assembled the context and rejected declared-group substitution.
The next new relation control correctly hit the production cross-split rejection,
but the test expected the same-split group-merge message. The correction changes
only that exact expected error; no production gate or fixture membership changed.
Later smoke assertions did not complete; corrected-head Actions remain required.
[Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42485253073/job/128745648419),
queue PostgreSQL, lint/types/import-contract and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42485252713) succeeded;
this is not overall CI or milestone acceptance.

The persisted qualification assembler now requires the canonical evaluation-group
producer for the committed selection's development and optional calibration release,
and for the final report's dataset. Exact project/release/role/group identities must
match the supplied lineage and current final journal. Missing canonical populations,
incomplete membership, substituted groups and new transitive grouping conflicts
cannot yield a context; the existing quality, support and freshness gates still run.
This is not yet a mandatory check in direct registry/acceptance consumers or HTTP.
Reference use and ancestor selection-use provenance remain unfinished.

The journal/assembly smoke now prepares separate synthetic canonical development and
final populations before selection/exposure, instead of declaring independent groups
for copied training membership. Two correlated source records count as one group.
Their labels remain machine-reviewed and not gold-eligible. Copied CPU scores,
fixture variant flags and reused recipe/class metadata exercise persistence only:
there is no new image compilation, independent measurement or domain-quality claim.
The fixture variant does not retain the CPU manifest's source samples or image hashes.

Twenty-five added adapter cases cover missing producer reads, both recipes,
project/release/role/group substitution and selected calibration recovery, omission,
missing membership and group mismatch. Both-recipe PostgreSQL controls reconnect and
assemble canonical lineage, reject invented development groups and new development/
final relations, and retain registry heads/event count. New assertions await Actions;
changed-file Ruff/format and patch checks passed. No local tests or builds ran.
No migration, dependency, endpoint or second qualification/grouping engine was added.

At preceding exact head `9fba7232cd1d9b8c1014684310dc92c4ad28edd6`,
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42483648034) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42483647624) succeeded.
[Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42483648034/job/128740758166)
reported **1443 passed, 80 skipped**;
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42483648034/job/128740758132)
reported **19 passed**, plus **24 passed** in its separate budget step;
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42483648034/job/128740758283)
reported **39 passed**. Migration round trip and all four parent upgrades, dev-stack,
web-build and lint/types/import-contract passed. This verifies the earlier producer,
not the new consumer or HTTP/UI/restore/domain-quality acceptance. The PR stays draft;
required Devin, fresh-context and native QA/Security reviews remain gates.

## Canonical evaluation-group producer — historical preparation

`evaluation_group_store.load_evaluation_set` derives sorted, distinct groups from
all exact source revisions in a scoped frozen canonical release. It uses that
release's pinned assignment version and the existing current transitive-group
checker, including exact duplicates and unselected intermediates. Missing sources,
missing exact revisions, excluded members, absent assignments and newly conflicting
relations fail closed rather than shrinking the evaluation population. Known final
reservations override requested development/calibration use through the existing
lifecycle guard. Project and canonical group locks remain held to outer commit.

This is a grouping producer, not independent label truth, a measured score, an
authorization boundary or authority to assign roles. The trusted service must select
the release and role. The context assembler and direct registry/acceptance consumers
**do not yet consume this producer**: their synthetic declared evaluation groups must
be replaced with canonical, independently grouped fixtures before that wiring can be
claimed. Reference and ancestor selection-use provenance are also still pending.
No migration, endpoint, dependency, current consumer gate or second grouping engine
is introduced.

Twenty-four adapter cases and both-recipe PostgreSQL controls exercise recovery from
committed canonical members, correlated support, missing/excluded/unfrozen revisions,
pinned assignment identity, final-role replay and new cross-group relations. Scoped
savepoint rollback retains all earlier qualification evidence. These new assertions
await exact-head Actions; no local tests or builds ran.

At preceding exact head `c448ec276138bc8778edeb65422ff843322b26a4`,
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42482104676) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42482104354) succeeded.
[Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42482104676/job/128736104396)
reported **1419 passed, 80 skipped**;
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42482104676/job/128736104351)
reported **19 passed**, plus **24 passed** in its separate budget step;
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42482104676/job/128736104322)
reported **39 passed**. Migration round trip and all four parent upgrades, dev-stack,
web-build and lint/types/import-contract passed. This verifies earlier persisted
mechanics and the audit-smoke coexistence repair, not this producer, authenticated
HTTP/UI/restore acceptance or domain quality. The PR stays draft and required Devin,
fresh-context and native reviews remain gates.

## Historical final-test fixture repair evidence

At exact head `c38285fa1cdfc48c761ef94f6f1d4e62d8fce13b`,
[CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42479456754) failed:
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42479456754/job/128728030122)
reported **2 failed, 17 passed**. Canonical seeding completed, but both recipes
reached a fixture that attempted adaptation of an unexposed final test. The
production lifecycle guard correctly rejected that attempt; later qualification
assertions did not complete. The separate budget step passed **24 tests**.
[Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42479456754/job/128728030083)
passed **1419 tests, 80 skipped**. Queue PostgreSQL, migration round trip and all
four parent upgrades, dev-stack, web-build, lint, types, import-contract and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42479456350) passed.
These successes do not clear failed CI or establish HTTP/UI/restore acceptance.

The corrected paired fixture retains the role-replay rejection controls, explicitly
asserts unexposed adaptation fails without changing the journal, then prepares a
separate synthetic development selection and exposes the reserved dataset through
the real store before adapting it. Quarantine uses the resulting revision. All
auxiliary reports, links, releases, selections and events remain inside the rolled-back
savepoint so the original durable selection remains unchanged. Copied scores and
invented groups prove mechanics only, never independent quality. No production
transition, migration, authorization or quality gate is changed. The new assertions
await corrected-head GitHub Actions; no local tests or builds ran.

## Qualification/auth migration composition

The independently delivered auth/jobs repair is merged in [PR #31](https://github.com/TogetherWeOwn/labelloop/pull/31),
squash `ad0f525b5ea07d1c6b68639bd8cf3c4c04601ac1`. This integration consumes that
repair and its auth dependency unchanged, then adds the no-DDL `m5_auth_0001`
join of `qualification_0007` and `auth_jobs_0001`. Every historical parent and
migration body remains unchanged. The delivered auth schema, sessions and guard
implementation are reused, not independently reimplemented. This does not mount
the pending promotion or operational consumers.

Graph checks now require exactly the composed head while retaining both delivered
subgraphs and all qualification ancestors. Both join revisions must have empty
upgrade/downgrade bodies. Actions' disposable PostgreSQL parent-upgrade matrix
retains both original auth/jobs parents and adds both new composition parents;
the clean upgrade/downgrade/re-upgrade, queue and CPU registry exercises remain.
New composition assertions await exact-head Actions, not local CI.

At fixture-alignment head `bfe0a4e`, [Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42476767969)
failed: [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42476767969/job/128719790060)
reported **2 failed, 1412 passed, 80 skipped**. All nine new fixture cases passed;
only global migration-head assertions failed. ML smoke, queue and dev-stack all
stopped at multiple-head migration setup, so PostgreSQL assertions did not run.
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42476767704), lint,
types, import-contract, gitleaks and standards passed; web-build was path-skipped.
No overall CI, PostgreSQL, HTTP/UI/restore or milestone PASS is inferred.

## Compiled training groups constrain context assembly

The persisted context assembler reads compiled training inputs across the selected
model's same-project checkpoint and recorded label-teacher ancestry, reusing the
veto's traversal rather than maintaining a second graph. Unregistered runs,
unselected checkpoints, every duplicate-digest parent and every contributing teacher
are included. Visited run IDs bound mixed cycles; future teacher origins do not
influence earlier frozen membership. Unknown external base hashes do not acquire
invented parents; explicitly missing local/teacher lineage fails closed.

Each reachable immutable input must retain a frozen canonical release, exact sample
revisions, assignment version and consistent derivative group/split identity. Every
compiled source is rechecked against current canonical transitive groups. Only
`train` samples count as weight-fitting groups; validation/test sources are checked
but are not misreported as training. Supplied qualification lineage must include
all inherited training groups. The existing pure gates then reject overlap with
development/final/canary groups even when that exposure is honestly reported.
Unrelated runs' inputs are not included. Project and canonical group locks remain
held through the caller's outer transaction.

This narrows caller obligations, not all of them. Ancestor validation/selection use,
reference use, canonical development/calibration/final groups, original pipeline/
acceptance/score/threshold and independent measurements still require authoritative
integration. Historical missing provenance and cross-project imports are not inferred.
This preflight is not an authenticated route or an acceptance grant; direct trusted
registry consumers still own complete lineage. No endpoint, migration or dependency
is added. At inherited-group head `a80eef7`, [Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42475242880)
failed with **2 failed, 1403 passed, 80 skipped**; the 15 added adapter cases passed.
Only the global migration-head assertions failed in unit. ML smoke, queue and
dev-stack stopped at multiple-head migration setup, before PostgreSQL assertions.
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42475242501), lint,
types and import-contract passed; web-build was path-skipped. This is not an overall
CI or PostgreSQL acceptance PASS.

Selected-run head `3d49e96` completed [Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42473468132)
with **2 failed, 1388 passed, 80 skipped**: only the two global migration-head
assertions failed. All 22 added adapter cases passed. ML smoke, queue and dev-stack
stopped on multiple-head migration setup; PostgreSQL context/proposal assertions did
not run. [CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42473467880),
lint, types and import-contract passed. Unlike the previous head's MinIO HTTP 503,
this dev-stack failure is the migration gate. No overall CI or acceptance PASS.

Static fixture inspection found that `registry_smoke.seed_variant` seeded only
synthetic ordinary-label canonical members while retaining the CPU manifest's source
IDs. The smoke now seeds actual licence-clean fixture pixels' identities, dimensions,
exact annotation revisions and split assignments from the existing canonical fixture
adapter. Before writes it checks every compiled source/revision/hash/group/split
against that adapter. The original CPU manifest and its digest remain unchanged.

Synthetic ordinary members remain extra whole-release lineage controls, not claimed
CPU training inputs. Their separate audit-only release preserves the explicit tiny
synthetic population and shared-group controls; the veto still follows those exact
revisions through training-release membership. A reconnect assertion on both recipes
requires the real production reader to recover exactly the compiled train groups,
not validation/test groups or invented audit groups. Nine fixture substitution and
positive adapter cases are added. These changes await exact-head Actions; no local
test or PostgreSQL PASS is claimed. Migration composition still gates the database
exercise. No production reader, guard, migration or trigger was relaxed or mocked.

At previous `4a03da8`, [CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42471974397)
failed. [Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42471974397/job/128704659677)
reported **2 failed, 1366 passed, 80 skipped**; only the two global migration-head
assertions failed, and all 18 new multi-proposal cases passed. ML smoke and queue
stopped on multiple heads before PostgreSQL assertions ran. Dev-stack independently
failed downloading pinned MinIO with HTTP 503. CodeQL, lint, types, import-contract,
gitleaks and standards passed; web-build was path-skipped. No overall CI, new-head,
HTTP/UI/restore or domain-quality PASS is inferred. The auth/jobs repair remains
independently owned; later qualification composition is still required.

## Completed proposal evidence joins annotation provenance

When the authorized service supplies a proposal run to `append_review`, teacher
identity now comes from its completed fenced producer record, not a submitted
teacher name or the current champion. The loader checks project, source, exact
expected revision, immutable job binding, application generation, stage/result
digests and the complete registered model release. Project-first locking and a
shared job lock survive through the annotation transaction. Negative and partial
proposals still retain teacher influence; they do not gain approval by doing so.
Initial external detectors without registered lineage fail closed on this path.

The resulting revision, principal-bound review event and exact-revision teacher
link share one savepoint. Adapter cases cover substitution and failed lookups or
origin writes. Synthetic Actions PostgreSQL cases exercise both positive and
negative revisions, retain the pinned teacher and inject origin-write failure
that must leave revisions, events and links unchanged. New assertions await
exact-head Actions; no local tests or passing acceptance are claimed.

The service still must select every contributing proposal from trusted work
history. Omission of the optional run is not proof of teacher-free annotation;
the port accepts and validates a distinct collection of contributing runs, but
collection support does not prove completeness. Original acceptance/pipeline/threshold,
independent measurement producers and authenticated HTTP wiring remain required.
This store integration adds no endpoint, migration or dependency and is not an
inference-execution, domain-gold or authorization claim.

At prior head `98299de`, [CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42468848117)
failed: unit **2 failed, 1328 passed, 80 skipped** (both global migration-head
assertions); all 31 annotation repository cases passed. PostgreSQL annotation
assertions did not run because ML smoke stopped at multiple-head migration setup.
Queue/dev-stack separately failed setup-uv downloads with HTTP 504.
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42468847954), lint,
types and import-contract passed. These results do not verify this new slice or
clear migration, review, HTTP, recovery or operational-view gates.

## Annotation transaction consumes the current acceptance guard

`append_review` now requires internal `AutomaticReview` inputs for automatic
acceptance, including writes that do not yet mark labels training-eligible. A
legacy access-policy ID is not a qualification grant. Model/human claims cannot
select the automatic path. The project is resolved from the actual asset and
locked before the asset row, following registry/audit lock ordering. Every
class/source/condition cell is checked through the existing current-champion,
policy, final-state, annotation-veto and scoped-drift guard before either the
review event or revision is appended. Complete-image acceptance covers every
ontology class, including absent classes and negative images.

The authenticated service still must derive source/condition scope and current
qualification from trusted producers; this internal type is not an HTTP schema
or a provenance producer. No endpoint accepts it. Original pipeline/acceptance,
score/threshold and independent measurement provenance remain incomplete.

New adapter assertions cover denial with a policy ID alone, valid system-policy
mechanics, guard failures before writes, project-first lock ordering and incomplete
negative-image scope. Paired synthetic PostgreSQL assertions now append actual
positive/negative revisions and principal-bound review events, reject replay after
a persisted scoped pause, and inject a revision-insert failure to verify rollback
of the preceding review event. These assertions await exact-head Actions; no local
test or new acceptance PASS is claimed. No migration, dependency or endpoint is added.

Previous head `b2d91f3` completed [Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42467314609)
with **2 failed, 1316 passed, 80 skipped** in
[unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42467314609/job/128689754602).
The five earlier fixture setup failures are absent; the two migration-head
assertions still fail. Queue/ML PostgreSQL assertions did not start because
migration setup rejected multiple heads. Dev-stack failed a MinIO HTTP 504 download.
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42467314443) failed to
download its CLI with HTTP 504, before analysis. Lint, types and import-contract
passed. None of those results verifies this annotation-consumer change or clears
the remaining migration/security gates.

## Final-role adapter fixture correction

At `c0b395d`, [Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42466087162)
failed: unit **7 failed, 1309 passed, 80 skipped**. Four adapted-state adapter cases
attempted adaptation before any final exposure; the root-substitution case tried
to serialize an unsupported root before reaching the loader. The fixture correction
uses the actual development selection and final exposure transitions before
adaptation, and serializes a supported candidate-selection root for loader rejection.
Additional exposed-but-unadapted cases retain the development/calibration denial.
No production transition, codec or guard is weakened. Corrected assertions await
new-head Actions; no local tests or dependency installation ran.

The other two unit failures are global migration-head assertions. Disposable
PostgreSQL migration setup also failed on multiple heads. Auth/jobs repair remains
independently owned; later qualification composition remains an integration gate.
ML smoke, dev-stack, types and import-contract failed before their commands when
setup-uv release downloads returned HTTP 504; lint separately failed downloading
checksum-pinned actionlint with HTTP 504. Thus those job failures do not establish
results for the new PostgreSQL assertions. [CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42466086323)
passed at `c0b395d`. Neither prior-head green evidence nor these download failures
clear the migration or new-head verification gates.

REA angle: reuse the delivered original-frame evaluator, M2 registry and store
codecs, M1 compiler and audit primitives. Existing model-store PostgreSQL failure,
concurrency and append-only assertions live in `workers/ml/tests/registry_smoke.py`;
retain them rather than replacing real training evidence with synthetic scores.

## Prepared typed inputs

`apps/api/tests/test_m5_integration_contract.py` defines `RecipeCase` for each default
320/640 recipe using existing `ModelRelease`, `EvaluationReport`, `AuditSample` and
`AuditObservation` types. The two generations deliberately differ in weights,
export digest, NMS mode, confidence, max detections and class-agnostic NMS. Recipes
have distinct preprocessing digests, input signatures and IoU thresholds, but the
same class catalogue. Two targets, including an occluded/truncated target, and a
negative image pass through the original-frame evaluator, not handwritten metrics.

The tests prepare and check:

- JSON round trips through the actual model-store release/report decoders, including
  slice metrics and coverage;
- promotion-event-shaped snapshots followed by complete-payload rollback;
- rejection of foreign weights, class catalogue and inference-policy evidence
  after decoding;
- an ordinary accepted wrong identity and an ordinary accepted false negative,
  with an independent synthetic adverse audit, while detector metrics are good.

The preparation case alone does **not** establish a promotion veto. The delivered
qualification contract now guards pure promotion; this integration adds an immutable
annotation-audit store and checks persisted adverse evidence before both promotion
and rollback. `workers/ml/tests/registry_smoke.py::exercise_annotation_veto` commits
wrong-identity and missed-object observations, reconnects, records a clean replacement
sample, then attempts every persisted candidate and rollback at both recipe heads.
It asserts that **the real registry heads and event count remain unchanged**, including
models trained from a separately frozen release containing the same source labels.
At `90ed50f`, [Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42442337445)
failed in the [ML smoke job](https://github.com/TogetherWeOwn/labelloop/actions/runs/42442337445/job/128614029440):
the losing concurrent challenger had only a development evaluation, which the veto
fixture incorrectly reused as a final ID (`StopIteration` before the veto assertions).
The repair appends a separate explicitly synthetic final evaluation to each candidate,
reloads linked evidence, verifies clean fixture qualification, and snapshots registry
metadata after that preparation. It also requires coverage of the development-only
losing challenger. Corrected head `4bf3806` passed [Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42443635030),
including [ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42443635030/job/128617916952),
[dev-stack](https://github.com/TogetherWeOwn/labelloop/actions/runs/42443635030/job/128617916920),
and [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42443635030/job/128617916833)
(**757 passed, 38 skipped**). CodeQL, gitleaks and standards passed at that head.
This verifies the direct persisted veto fixture, not HTTP authorization, empirical
quality, or the checkpoint-ancestry extension below, which awaits new-head Actions.

`qualification_0001` follows the delivered ledger's `budget_0002`. The new
`qualification_annotation_audit` table is append-only at the database boundary and
must be included in the final restore inventory. Audits load frozen membership and
prior actors from canonical records. Audit writes and registry changes lock the
project before any recipe head, ordering adverse evidence against concurrent promotion.
Training appends now take that same project lock. Registry vetoes walk initialization
hashes through all same-project training checkpoints, including unselected checkpoints
and runs without registered models. Matching duplicate hashes include every possible
ancestor; visited run IDs bound cycles. External base hashes without a local matching
checkpoint have no inferred parent. This does not implement cross-project imports.

New synthetic PostgreSQL cases for both recipes persist a clean-data intermediate
training run without a model release, a clean-data child initialized from it, and an
unrelated clean-data control. After adverse evidence is committed on the original
ancestor, the child must fail registry mutation even though its own membership contains
neither audited source. The unrelated control must not be vetoed. All rejected changes
must preserve heads and event count. Unit cases additionally cover unselected parents,
duplicate digest branches and cycles. These additions await exact-head Actions.
No new migration, sampler, audit truth source or HTTP endpoint is introduced in this slice.

Vetoes currently hold every later revision of an affected source conservatively;
there is no clearing API. An authenticated correction plus complete descendant
re-tracing must precede any future clearing transition. Indirect teacher/generated-label
provenance, current-champion automatic-acceptance enforcement, representative population
sampling, authoritative acceptance-score/threshold provenance, lifecycle storage and
authenticated adapters remain integration obligations. The in-memory checkpoint index
is project-scoped and rebuilt from immutable records under the project lock; scale
optimization must preserve the same ancestry and concurrency semantics.
The internal audit writer is **not** an authorization boundary: its caller must derive
an authorized human principal from the existing session service. Passing a string
actor or synthetic clean qualification is never evidence of HTTP authorization.

These are synthetic software-contract cases, not independently adjudicated domain
gold, trained checkpoint evaluation, empirical quality or production acceptance.
The explicit `PromotionPolicy(1, 0.9)` is fixture-only. Project targets stay null
until decided; null targets retain the incumbent. No paid request or real provider
secret is needed. No dependency version, HTTP endpoint or runtime is added.

## Durable final-test lifecycle port

`final_test_store` consumes the existing pure `FinalTestState` and selection
transitions. Migration `qualification_0002` follows `qualification_0001`, adding
append-only `qualification_candidate_selection` and `qualification_final_test_event`
tables. Initialization must reference a frozen canonical release in the same project;
its trusted source-group/role loader is still a service integration obligation.
No HTTP body can be treated as that loader. Selection reads the persisted atomic
model and linked development/calibration reports. Exposure reads that selection
and the persisted final report, then appends a complete versioned state. Adaptation
and quarantine preserve historical results; no reset/unquarantine operation exists.

All transitions lock the project, compare expected revisions and append inside a
savepoint. New evaluation links take the same lock because selection pins the entire
release including evaluation IDs. The service must commit selection before final
access and exposure before returning even an aggregate or pass/fail result; a later
promotion failure cannot undo a real exposure. The store does not own the caller's
outer commit. `change_registry` now always reloads the current final-test journal
when a trusted qualification context is present, after checking project/recipe scope
and while retaining the project lock. A missing journal fails closed; adaptation or
quarantine cannot be bypassed by replaying a serialized historical success. The
promotion event stores the context actually checked, including its reloaded state.
Authoritative policy/pipeline/audit/canary/group loaders and authenticated service
routing remain necessary before production promotion. The existing production port
remains fail-closed without trusted qualification. Rollback is still a distinct
recovery operation and never grants automatic acceptance.

`workers/ml/tests/final_test_smoke.py` extends the existing Actions CPU/PostgreSQL
smoke for both recipes with explicitly synthetic, separately named final releases.
It prepares committed selection/exposure, reconnect recovery, duplicate reset and
repeat exposure rejection, expected-revision races, immutable update/delete guards,
injected event-insert failure and a historical context rejected after current-state
reload. Seven lightweight actor/role/action/DDL cases are added in
`apps/api/tests/test_final_test_store.py`. These are **new unrun assertions** until
exact-head Actions finishes, not final-test accuracy or HTTP/registry acceptance.
The checkpoint-ancestry head `9107e75` still had no Actions runs recorded at the
2026-11-08 monitor inspection, so its additions also remain unverified.

The expanded restore inventory must include both journal tables as well as
`qualification_annotation_audit` and all other delivered modules' tables. No live
migration, database access, dependency installation or production action is part
of this change. Frozen migration DDL deliberately does not import live metadata.

## Durable scoped drift and automatic-acceptance port

`acceptance_store` reuses `AcceptanceState`; migration `qualification_0003` follows
`qualification_0002` and adds the append-only `qualification_acceptance_event` journal.
Each project/recipe has its own revision sequence. Pauses record the current champion,
exact affected cells, qualification, reason and incident revision. They do not require
a failing champion to pass qualification before containment. Resumptions require a
new qualified current champion, incident-linked qualification **and** canary, and the
current persisted final-test state. The per-operation acceptance guard also checks the
current champion, durable annotation veto and scoped pause history under the project
lock; a rollback never clears that history. Callers must retain the outer transaction
through the annotation write. Empty actors, stale revisions and failed journal writes
cannot create a partial transition.

This is an internal trusted port, **not an authenticated route or a completed producer
integration**. The service must derive the actor, approved expanded scope, historical
qualification identity, policy/pipeline/audit/canary and canonical group provenance.
Without trusted qualification, resumption and automatic acceptance fail closed. The
service/worker acceptance path does not yet consume this port. The existing registry
port reloads current final state but still needs complete authoritative context
loading; this acceptance port does not substitute for that work.

The paired Actions PostgreSQL smoke now prepares synthetic affected/unaffected cells,
reconnect recovery, racing pauses, append-only guards and an injected event-write
failure. A newly promoted synthetic release must present persisted incident links;
pre-incident evidence cannot resume. A successful resume must survive reconnection,
and rollback to the invalidated release must still deny automatic acceptance even
with a new otherwise passing synthetic qualification. These new assertions await
exact-head Actions; no drift/HTTP acceptance or domain-quality PASS is claimed.
The recovery owner must add the journal to the expanded restore inventory.

The PR had real conflicts with delivered main `f68d897`; the restack retains both
proposal inference and qualification smoke coverage, not an optional up-to-date
refresh. No-DDL merge revision `m5_jobs_0001` joins `qualification_0003` and delivered
`0006_proposal_jobs` without rewriting either history. The graph regression requires
one head containing both chains; upgrade/downgrade/re-upgrade remains an Actions gate.
Delivered dependency and UI updates are inherited unchanged from main; no dependency
installation, version decision or regenerated lockfile is performed in this slice.

Corrected journal-loader head `80ef127` passed [Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42447535464)
and [CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42447537762).
[Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42447535464/job/128629548277)
reported **642 passed, 38 skipped**; [ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42447535464/job/128629548325)
reported **16 passed**, plus **24 passed** in the separate budget-persistence step.
Dev-stack, lint/types/import-contract and web-build also passed. This covers earlier
checkpoint ancestry and final-journal mechanics at that head, not this new drift
slice, HTTP enforcement, independent quality, or final milestone acceptance.

## Mandatory registry final-state read — new assertions pending Actions

`apps/api/tests/test_registry_final_state.py` checks that the registry adapter
forwards only the reloaded context, saves that exact context, rejects foreign
project/recipe/final identities before the read and cannot mutate the head/event
when the journal is missing. The existing paired PostgreSQL drift smoke now replays
an otherwise passing historical qualification after committed adaptation and after
quarantine, asserting unchanged complete registry state and promotion-event count
across new connections. These new assertions have not yet run on Actions.

The older synthetic registry helper now persists a separately named final release,
selection and exposure for each fixture candidate, using matching incumbent reports
on that same frozen test. Its membership/groups/scores remain explicitly invented
or copied for software mechanics, never independent final accuracy. It cannot use
an in-memory success as a substitute for the mandatory journal. Later comparison
links are retained in recovered rollback metadata. No migration or production gate
exception is introduced.

Previous head `34bcaab4078e5d8a650bf1d28d521f0d75e4c859` passed
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42449245275) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42449245102).
[Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42449245275/job/128634733563)
reported **960 passed, 80 skipped**;
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42449245275/job/128634733553)
reported **19 passed**, plus **24 passed** in the separate budget step.
[Queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42449245275/job/128634733606)
reported **39 passed**. Migration round trips, dev-stack and lint/types/import-contract
passed; web-build was path-filter skipped. This verifies the previous drift/journal
mechanics, not this new registry change, HTTP/UI/restore acceptance or domain quality.

## Canonical audit population and durable sample — new assertions pending Actions

`audit_sample_store` loads **every** exact annotation revision named by a frozen
canonical release, its frozen ontology, original actor and earlier review actors,
and source groups from the release's pinned assignment version. Missing source,
revision or group rows reject the entire population rather than shrinking it to
successful joins. Unsupported classes, invalid geometry, incomplete/draft labels
and excluded members fail closed. Later source revisions are not substituted.

It reuses the existing deterministic blind sampler, including ordinary positives
and negatives without a confidence filter. The new append-only
`qualification_audit_sample` table retains the complete canonical population,
selected membership, seed, actor and group snapshot with a content fingerprint.
Recovery reproduces selection and rejects a mismatched snapshot. Correlated images
retain one group identity and cannot inflate independent support. This is an
asset-level, positive/negative-stratified software sampler, **not** a claim of
independent random group draws or an unbiased population error estimate.

Migration `qualification_0004` follows the existing joined `m5_jobs_0001` history;
it changes no prior DDL. Restore must inventory this table. Actions PostgreSQL
smoke now covers missing canonical groups/members, frozen revision recovery,
pinned-versus-other assignment versions, correlated support, foreign scope,
immutable update/delete/truncate, same-identity races and failed-insert rollback.
New unit cases cover canonical decoding, exact-root codec and sample substitution.
These assertions have not yet run on the new head.

The service still owns authorized sampling policy and pipeline identity; attaching
a pipeline ID to a population is **not** proof that each original acceptance used
that pipeline. Source/condition provenance, independent audit truth/measurements,
score/threshold provenance, current group-relation revalidation, observation-to-sample
binding, authoritative qualification loading and authenticated HTTP consumption are
still required. A saved sample alone never qualifies a clean release or overrides
any durable adverse audit. No endpoint or paid operation is introduced here.

Previous exact head `56617387b69effaae71a76d2dcd3ee2ee2a9784f` passed
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42450706107) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42450705810).
[Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42450706107/job/128639124578)
reported **965 passed, 80 skipped**;
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42450706107/job/128639124424)
reported **19 passed**, plus **24 passed** in the separate budget step.
[Queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42450706107/job/128639124438)
reported **39 passed**. Migration cycles, dev-stack, lint/types/import-contract,
gitleaks and standards passed; web-build was path-skipped. This verifies previous
registry current-final enforcement, not the new sampler, HTTP/UI/restore acceptance,
independent domain quality or final milestone delivery. Devin and native reviews
remain gates; the PR stays draft.

## Sample-bound audit observations — new assertions pending Actions

Audit writes can now name a previously committed canonical sample. The writer
loads it by project, release **and** sample identity under the same project lock,
reproduces its selection, and uses the existing pure audit assessment to reject
unselected/stale/dependent observations. Principal and canonical prior-actor checks
still apply. Durable observations carry the sample key, population fingerprint,
group snapshot and computed membership verdict; none is accepted from a caller.
Partial and adverse observations remain durable veto evidence with explicit pending
members. A complete clean membership verdict is not truth, an error estimate,
a calibrated threshold or release qualification. Legacy unsampled writes remain
veto-only and cannot be treated as completed sampled evidence.

New adapter cases cover clean/partial/adverse/truncated and unsampled writes,
selection/revision/reviewer substitution, and release-scoped lookup. Actions
PostgreSQL smoke adds cross-connection binding recovery, missing-sample and
identical-membership foreign-release rejection, duplicate observations, same-key
races and injected append failure. The existing real-registry adverse-veto cases
now use the committed canonical sample, retain partial pending members, and still
require unchanged heads/event counts after a later clean full result. These are
**unverified new assertions**, not HTTP or domain-quality acceptance. No new table,
migration, dependency, endpoint or runtime is introduced.

Canonical sampling head `6edd3fa296ca5f7bc3a1e7165888b425fac7c034` passed
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42452679939) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42452679477).
[Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42452679939/job/128645069510)
reported **1077 passed, 80 skipped**;
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42452679939/job/128645069489)
reported **19 passed**, plus **24 passed** in the separate budget step;
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42452679939/job/128645069641)
reported **39 passed**. Migration cycles, dev-stack, lint/types/import-contract,
gitleaks and standards passed; web-build was path-skipped. This verifies the prior
canonical sample slice only. Current group-relation revalidation, source/condition
and original-pipeline provenance, trusted measurements/thresholds/teacher lineage,
authoritative qualification loaders, authenticated consumers and expanded restore/UI
acceptance remain pending. Required Devin, fresh-context and native reviews remain
gates; the PR stays draft. The fixed 2026-11-09T12:00:00Z checkpoint is unchanged.

## Persisted audit-result consumer — new assertions pending Actions

`annotation_audit_store.load_annotation_audit` loads one immutable result by exact
project/release/audit identity, then reloads its committed canonical sample. By default
it rechecks current groups for the entire frozen population under the project lock.
It strictly decodes observations, checks the authenticated writer identity, and
recomputes the pure membership verdict and adverse-revision set. Substituted group,
fingerprint, verdict, malformed observation or unsampled result fails closed. Partial,
adverse and truncated results remain non-passing; no clean-result selection or union
of partial audits is inferred. Historical reads may explicitly omit the current-group
check so adverse evidence remains inspectable after a new relationship conflict.

This is an internal persistence consumer, not an authenticated endpoint, independent
truth, representative error bound or full qualification loader. Source/condition,
original pipeline, score/threshold/measurement and teacher provenance remain required,
as do policy/canary assembly, HTTP/annotation/operational wiring and expanded restore.
No table, migration, dependency or endpoint is added. New adapter substitution cases
and PostgreSQL reconnect/foreign-scope/current-conflict/historical-adverse controls
await exact-head Actions.

The preceding corrected head `4716097038609251f00512cd3abf38174aafc55f` passed
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42456573018) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42456572619).
[Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42456573018/job/128656781929)
reported **1105 passed, 80 skipped**;
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42456573018/job/128656781995)
reported **19 passed**, plus **24 passed** in the separate budget step;
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42456573018/job/128656781976)
reported **39 passed**. Migration cycles, dev-stack, lint/types/import-contract,
gitleaks and standards passed; web-build was path-skipped. This verifies the earlier
current-group checks with the repaired locked-assignment fixture, not this new loader
or milestone acceptance. Required reviews remain gates and the PR stays draft.

## Current qualification configuration journal — new assertions pending Actions

`qualification_policy_store` persists the exact targets, full pipeline identity and
supported class/source/condition scope as append-only, expected-revision versions.
Null targets remain valid configuration but cannot qualify. Strict typed decoding
rejects substituted roots and foreign project/recipe records. The pipeline fingerprint
must bind the exact targets. `qualification_0005` follows `qualification_0004` and
adds `qualification_policy_event`; historical migration DDL is unchanged. Restore
inventory must include the new table and its full history.

Registry promotion, drift resumption and each automatic-acceptance guard now load
current configuration under the shared project lock before the current final-test
journal. Missing configuration or different evidence targets, scope or pipeline fails
closed. Historical contexts cannot select a permissive old configuration; rollback
remains distinct from automatic acceptance. Synthetic fixtures explicitly persist their
fixture-only configurations instead of bypassing the new gate. The Actions PostgreSQL
exercise checks reconnects, changed pipeline/scope/null-target rejection by the actual
registry and acceptance stores with unchanged heads/event counts, a restored-configuration
control, one expected-revision race winner, immutable update/delete/truncate and failed
append rollback. New assertions await exact-head Actions; no local tests were run.

This configuration store is not authentication or proof that a declared pipeline ran.
The authorized service must still validate canonical pipeline inputs, original acceptance
and score/threshold provenance, independent audit measurements, teacher lineage and
fresh canary evidence. Full qualification assembly, authenticated promotion/annotation/
operational wiring and expanded restore/UI acceptance remain incomplete. No new HTTP
endpoint, runtime, spend, credential change or production action is introduced.

The preceding audit-loader head `aa66860e699e6774d1f5064d7b64fb83f936e2da` passed
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42457825894) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42457825561).
[Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42457825894/job/128660559967)
reported **1131 passed, 80 skipped**;
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42457825894/job/128660559968)
reported **19 passed**, plus **24 passed** in the separate budget step;
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42457825894/job/128660559947)
reported **39 passed**. Migration cycles/dev-stack/lint/types/import-contract/gitleaks/
standards passed; web-build was path-skipped. This verifies the prior audit loader,
not the new policy journal, HTTP/UI/restore acceptance or domain quality. Native,
fresh-context and required Devin reviews remain gates; the PR stays draft.

## Generated-label teacher lineage — new assertions pending Actions

`generated_label_store` appends every known contributing teacher against an exact
canonical asset revision, including accepted negatives and later human-reviewed
revisions. The internal caller must derive the teacher from its trusted inference
job and append provenance in the annotation transaction; a model response cannot
select a teacher. The port verifies source revision and teacher in the same project.
Multiple teachers and cross-recipe teachers are retained, not overwritten. This is
not an authenticated route or proof that inference actually ran, and it does not
infer missing historical provenance or implement cross-project model imports.

The shared registry/automatic-acceptance veto now traverses these recorded teacher
runs as well as all checkpoint parents. A student initialized from an unrelated base
cannot escape adverse evidence in its label teacher's ancestry. Every teacher is
traced, including mixed teacher/checkpoint generations and cycles. Influence starts
at the recorded revision and conservatively survives later revisions until an
explicit authenticated correction/re-tracing operation exists. Future revision links
do not contaminate earlier frozen training membership. Recorded out-of-project
teachers fail closed rather than being treated as unknown external base weights.

Migration `qualification_0006` follows `qualification_0005`, adding immutable
`qualification_generated_label_origin` with exact-revision and model foreign keys.
Restore must retain the table and all contributing links. Unit assertions cover
multiple teachers, negative labels, revision boundaries, cycles, cross-project
substitution and frozen DDL. Actions PostgreSQL assertions add reconnects, exact-key
races, failed append, missing/foreign rows and update/delete/truncate guards. Both
recipe fixtures now include an independent-initialization, clean-data teacher student;
a committed adverse audit must reject its real registry promotion with complete heads
and event count unchanged, while unrelated clean-data controls remain unaffected.
All new assertions await exact-head Actions, not a domain-quality or HTTP PASS.

Prior exact head `2f35a74975e391b90ddc474a691fd01b8bec3685` passed
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42459295388) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42459295231).
[Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42459295388/job/128664936313)
reported **1149 passed, 80 skipped**;
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42459295388/job/128664936548)
reported **19 passed**, plus **24 passed** in the separate budget step;
[queue PostgreSQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42459295388/job/128664936325)
reported **39 passed**. Migration cycles/dev-stack/lint/types/import-contract/gitleaks/
standards passed; web-build was path-skipped. This verifies the prior current-policy
slice, not this new teacher traversal. Trusted inference/annotation consumer wiring,
original acceptance/score/threshold and source/condition provenance, full audit/canary
assembly, authenticated promotion/operational adapters and expanded restore/UI
acceptance remain incomplete. The PR stays draft and all existing reviews remain gates.

## Measured audit and candidate-bound canary outcome persistence

`annotation_evidence_store` retains the existing typed `AnnotationEvidence` contract,
including measured numerators/denominators, geometry error, truth-basis marker,
source/condition metadata and incident links. It binds every outcome to an exact
committed project/release/audit, sample, observations and canonical group map.
Complete, partial and adverse outcomes are preserved; storage never selects a clean
result or turns a membership verdict into quality. Historical writes remain possible
after a grouping conflict so evidence is not discarded. Default reads recheck current
canonical groups; explicitly historical reads do not confer qualification.

A canary outcome additionally binds the complete persisted candidate release hash,
not just weights. Readers must explicitly select that same model; ordinary audits,
another recipe's model and changed complete release metadata cannot substitute.
This is candidate-bound **outcome storage**, not proof that the candidate ran or that
groups are fresh. The trusted producer must still establish independent measurements,
source/condition and original-pipeline provenance. Full qualification assembly must
consume the committed outcomes and enforce current policy, freshness, final-test and
scope gates before authenticated promotion, resumption or automatic acceptance.
No endpoint or consumer bypass is added; real null targets remain fail-closed.

Migration `qualification_0007` follows `qualification_0006` and adds immutable
`qualification_annotation_evidence`; restore must inventory its audit/model links
and every outcome, including failures. The strict codec now supports a caller-selected
`AnnotationEvidence` root, without allowing input-selected types. Adapter assertions
cover exact bindings, malformed measurements and cross-model/result substitution.
Actions PostgreSQL assertions cover reconnects, both recipe model bindings, partial
and adverse retention, current-group conflict versus historical reads, same-key races,
immutable update/delete/truncate and failed append. New assertions await exact-head
Actions; no local tests, builds or live/shared database operations were run.

The preceding exact `4178c3ab45d12c1845e66280e8e311248d6f1c06` passed
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42460833619)
and [CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42460833528),
including unit, ML smoke, migration cycles and dev-stack. Web-build was path-skipped.
That verifies preceding teacher-lineage mechanics, not this new persistence slice,
fresh canary qualification, independent quality, HTTP/UI/restore or final M5 acceptance.
Required Devin, fresh-context and native reviews remain gates; the PR stays draft.

At `87451d0`, [ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42462504127/job/128674641224)
passed, including the new PostgreSQL measured-outcome exercise; CodeQL also passed.
[Unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42462504127/job/128674641291)
failed, so that head is not accepted. Inspection found that the migration-graph
regression still expected `qualification_0006` despite the new `qualification_0007`
head. The correction updates the expected head and adds the new ancestry assertion,
retaining all earlier graph checks. The focused diagnostic command
`uv run --no-sync pytest apps/api/tests/test_acceptance_store.py::test_qualification_and_proposal_histories_have_one_head_without_rewriting_ddl -q`
could not start: `pytest` is not installed in the isolated worktree (exit 2).
No dependencies were installed and no test ran locally. Raw-log escape protection
was retained; without a complete failure transcript, no claim is made that the
head assertion explains every unit failure. Corrected-head Actions remains required.

## Current persisted context assembly — new assertions pending Actions

`qualification_context_store.load_qualification_context` loads the exact scoped model
and linked report, current policy and final-test journal, committed measured audit,
and complete-model-bound canary. It derives the final release and exact selection
from stored report/exposure records rather than caller-provided state. Both outcome
reads require current canonical group validation; there is no historical-read option.
The existing pure qualification engine then enforces configured targets, full pipeline
identity, scope, independent final-test use, measured quality and canary freshness.
Partial/adverse outcomes, adapted/quarantined tests, changed complete model metadata,
missing records and canary reuse of training/evaluation/reference/prior-audit groups
cannot yield a passing assembled context.

This remains a trusted internal preflight, not an authenticated service or a complete
provenance producer. Its caller must derive canonical lineage and authorized selectors;
original pipeline execution, source/condition metadata and measurement truth are not
established by storage. Retain the same outer transaction and locks through registry
or acceptance checks. Do not cache contexts across transactions. Registry incumbent
non-regression, durable vetoes, current state and scoped drift remain separate gates.
No table, migration, endpoint, dependency or runtime is added.

Adapter tests exercise real pure gates with mocked reads for both recipes. The paired
Actions PostgreSQL smoke now prepares explicitly synthetic canonical positive/negative
sources on separate audit/canary groups, commits samples, observations and measurements,
reconnects, assembles the context and exercises the actual automatic-acceptance guard.
It rejects partial canaries and prior-audit group reuse, requiring registry heads/event
counts to remain unchanged. Counts, groups and truth are invented fixture mechanics,
not independent domain gold or HTTP acceptance. New assertions await exact-head Actions.

Corrected measured-outcome head `294746a612ffeef80f0ca0d4cfbdda7cc7f5dc79` passed
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42462759934) and
[CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42462759619), including
[unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42462759934/job/128675443277),
[ML smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42462759934/job/128675443213),
queue PostgreSQL, migration cycles and dev-stack. Web-build was path-skipped. That
verifies prior outcome storage, not this new assembly or final milestone acceptance.

## Persisted final identity overrides supplied development roles

The final-test store now checks the latest scoped final-test journal before
recording candidate selection, before exposing final results for an older selection,
and when reloading qualification for promotion, resumption or automatic acceptance.
The persisted context assembler performs the same check against the exact exposure's
selection. A supplied development/calibration role or a previously stored report cannot
hide a dataset currently reserved as a locked final test. Registration, invalidation,
selection and consumers share the existing project lock, so these checks remain bound
to the outer transaction rather than a cached earlier role.

An explicit adaptation event permits the entire former test release to become
development/regression evidence, as required by plan §11.1. It does not turn that
release into a calibration set or rehabilitate it as independent final evidence.
Quarantine blocks both selection and threshold tuning, even after adaptation.
An absent final journal is not proof of a canonical role, independent groups, original
pipeline execution or measurement truth. Those trusted producer obligations and the
remaining authenticated consumers are unchanged. No new endpoint or migration is added.

New adapter cases cover both use categories, locked/adapted/quarantined states,
foreign/substituted records and current-context replay. Paired synthetic PostgreSQL
assertions reject selection and final exposure after a later locked reservation,
permit explicitly adapted development use, and reject quarantine. The persisted
assembly exercise additionally replays an earlier passing context against the actual
automatic-acceptance and registry stores, requiring unchanged complete heads and event
count. Temporary role reservations in these fixtures roll back; they do not modify the
actual CPU fixture's canonical assignments or claim new domain-quality evidence.
These assertions await exact-head Actions; no local tests or builds were run.

Previous head `39f1734c600101e57df7d39bedb9e2b4355a9395` failed
[Actions CI](https://github.com/TogetherWeOwn/labelloop/actions/runs/42464174543):
unit had two migration-head assertion failures; queue/ML migration setup failed on
multiple heads before the new PostgreSQL assembly assertions ran. Dev-stack separately
failed on a MinIO HTTP 504 download. CodeQL passed, but does not clear failed CI.
Later composition must consume the independently owned auth/jobs repair without
rewriting historical migrations. No repair, complete acceptance or PASS is inferred.

## Remaining end-to-end cases and consumers

All cases below are **pending**, not skipped/xfail passes. Each must gain executable
integration assertions against delivered code before milestone acceptance.

| Case | Required operation and negative control | Producer/consumer boundary |
|---|---|---|
| Audit veto (16) | Submit adverse ordinary-acceptance audit with otherwise good candidate scores; reject promotion with unchanged persisted head, event count and authenticated principal trail. Missing, dependent, stale or truncated audit also fails. | Qualification evidence + M3 authenticated audit writes → registry transaction |
| Support and slices | Absent class, inadequate independent groups, failed critical slice, null target or mismatched pipeline fingerprint retains incumbent despite good aggregate mAP. HTTP body cannot override server policy/evidence. | Qualification records → privileged promotion service |
| Final-test independence (17) | Reject locked-final evidence for candidate selection/threshold tuning. After exposure guides adaptation, preserve historical result but quarantine the entire final release for later qualification. Reject forged development role and cross-group leakage through HTTP. | Final-test lifecycle + M3 principal/auth → evaluator and registry |
| Fresh-group canary | Reject any canary group related to training/calibration/development/final groups. Persist fresh membership and bounded outcome across new connections/restarts; incomplete outcome cannot expand auto-acceptance. | Qualification transitions → persistent service |
| Scoped drift | Pause affected class/source only, persist pause across restarts; unrelated scope continues. Resume only with a new matching qualified release, never a client flag or old qualification. | Drift transitions → persistent service and operational reads |
| Atomic release (19) | In PostgreSQL, promote and roll back each 320/640 recipe with all §9.8 metadata. Inject event-write failure and stale concurrent revision; neither partial head nor mixed metadata may commit. Verify linked later evaluations after restart. | Existing model store + qualification transaction |
| Isolated restore (21) | Restore disposable PostgreSQL/object storage, regenerate both variants from manifests byte-identically, reload model/evaluation/audit lineage and complete rollback records; inventory every newly added table. Corrupt/missing objects, stale schema and incomplete audit chain fail. | Backup manifest/drill + compiler + new schema registrations |
| Real operational reads | Accepted cost/audit/registry views consume authenticated persisted records, including unconfirmed actuals, paid pause, unsupported classes, canary and drift. Test server role filtering and secret-free responses, not just fixture ports. | Shared ledger/secret boundary + M3 API → accepted UI |

## Evidence and operating limits

- Full suites, CPU training, disposable PostgreSQL recovery and browser checks run
  on GitHub Actions only. Existing `unit` discovers the new contract test file;
  no new local suite or substitute CI command is introduced.
- Preparation has no test result until the exact-head Actions job finishes.
  Keep the integration PR draft until all acceptance rows have real evidence.
- Final evidence must identify head SHA, Actions run/job/artifact links, measured
  restore duration, native review decisions and all finding dispositions. A module
  fixture pass alone does not establish HTTP, persistence or UI acceptance.
- Explicit `fixture_only: true` targets may demonstrate M5 gate mechanics. Independent
  domain gold is not an M5 fixture-exit prerequisite. Real targets remain null and
  fail closed; synthetic evidence cannot support an empirical model-quality claim.
- Integration checkpoint: 2026-11-09T12:00:00Z. If unmet, publish the exact missing
  producer output/gold evidence and accountable fallback without silently moving
  the date or weakening acceptance.
- No live/shared database, production drill, deployment, credential change, spend
  or preview runtime is authorized or introduced by this work.
