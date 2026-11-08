# M5 qualification and recovery integration

Status: **integration in progress; milestone acceptance is not established**.

Scope is [plan v0.3](plan.md) §§11–12 and acceptance checks 16, 17, 19 and 21.
This record joins independently delivered modules; it does not commission a second
qualification engine, registry, ledger, secret store, UI or backup tool.

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
