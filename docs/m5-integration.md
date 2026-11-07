# M5 qualification and recovery integration

Status: **preparation only; milestone acceptance is not established**.

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

The last case intentionally does **not** claim a promotion veto: the existing
`PromotionPolicy` does not consume annotation-audit evidence. Final integration
must submit that adverse evidence through the qualification module and prove
that **the real registry head and event count remain unchanged**. Testing only
`assess_audit(...).passed == False` is insufficient for acceptance check 16.

These are synthetic software-contract cases, not independently adjudicated domain
gold, trained checkpoint evaluation, empirical quality or production acceptance.
The explicit `PromotionPolicy(1, 0.9)` is fixture-only. Project targets stay null
until decided; null targets retain the incumbent. No paid request or real provider
secret is needed. No dependency, migration, HTTP endpoint or runtime is added.

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
- Missing independent domain gold/targets is an acceptance prerequisite, not a
  reason to stop offline preparation or invent adjudication. Record the provenance,
  independence, support and measured-target gap explicitly.
- Integration checkpoint: 2026-11-09T12:00:00Z. If unmet, publish the exact missing
  producer output/gold evidence and accountable fallback without silently moving
  the date or weakening acceptance.
- No live/shared database, production drill, deployment, credential change, spend
  or preview runtime is authorized or introduced by this work.
