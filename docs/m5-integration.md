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
