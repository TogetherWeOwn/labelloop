# Local training and evaluation — implementation reference

These contracts describe the M2 pipeline, not model-quality qualification.
Exact-head Actions results and review status are recorded in the PR's Verification
section rather than duplicated here as time-bound status claims.
Scope remains plan v0.3 §§9, 11 and acceptance checks 12–15, 17, 19 and 26.
Implementation runtime binding was verified as `ANTHROPIC_MODEL=gpt-6.1-sol`.

## Deterministic contracts

`labelloop_core.training` defines `TrainingBackend`, immutable requests,
checkpoint/result records and an explicit baseline argument set. Both recipes pin
native tiling off, NMS mode, square geometry and every baseline augmentation,
including erasing. Resolved arguments are compared key by key, including types;
missing keys fail rather than silently inheriting vendor defaults. The baseline
rejects implicit Albumentations transforms. Training results must record the
pinned Ultralytics version and actual Torch version.

Tensor assertions require nonempty N×3×S×S input and actual stride divisibility.
Checkpoint selection accepts only development-validation evidence, uses the
maximum fixed-shape score and resolves ties by earliest epoch. Calibration and
locked final test evidence cannot select a checkpoint.

## Original-frame evaluator

`labelloop_core.evaluation.EvaluationRunner` consumes the complete frozen source
membership, recorded derivative transforms and class-validated predictions. It
clips to valid derivative content before inverse mapping, then clips to the source
and applies stable source-frame NMS. Duplicate tile detections count once;
objects outside the crop remain in the source ground-truth denominator. A source
with no emitted derivative remains in the evaluation release.

Metrics use non-crowd, one-to-one matching at IoU 0.50–0.95 in 0.05 increments,
stable score ordering and 101-point interpolated AP. Overall AP averages only
supported classes; an absent class has `supported=False` and null AP/recall,
never the aggregate fallback. Precision and recall use the release confidence
threshold at IoU 0.50, while AP retains the full supplied score ranking.
Callers must supply low-threshold predictions for AP, not confidence-truncated
operating predictions. Max detections are bounded by the release contract.

The initial slices report source-area buckets, derived minimum side ≤6 pixels,
occlusion, truncation, negative images and targets outside all derivative coverage.
Object-cohort slices ignore detections matched to non-cohort ground truth while
retaining unmatched false positives. They are cohort diagnostics, not pycocotools
area-range summaries: an unmatched detection counts as a false positive in every
object-cohort slice, making slice precision/AP pessimistic relative to COCO's
area-range ignoring. Crop union area, wholly uncovered targets and partially
covered targets are recorded per source.

Tests compare overall mAP50 and mAP50–95 with pinned `pycocotools==2.0.11` on
perfect, missed, duplicate, imperfect-localization and negative-image golden
cases. They are ordinary axis-aligned, non-crowd fixtures; crowd annotations and
segmentation evaluation are outside this domain contract.

## Registry decisions

`labelloop_core.registry` defines the atomic model release payload and pure
compare-and-swap state transitions. A release binds weights, preprocessing,
class map, recipe, inference thresholds, NMS/tiling, package versions, static
ONNX signature, export digest and evaluation IDs. Promotion checks artifact and
policy identity, candidate per-class support/quality and incumbent non-regression
on the same frozen evaluation release and identical class catalogue/map hash.
The incumbent must have matching lineage, but need not meet today's absolute
candidate gates. Rollback swaps the complete previous release; it cannot cross
project or recipe boundaries. A second rollback toggles back to the demoted model.

`labelloop_api.model_store` persists completed training runs, per-epoch evaluation
reports, model release payloads and export evidence in PostgreSQL. It verifies the
frozen variant's project, recipe, class map, manifest and split lineage before
inserting evidence. A model release must match its persisted selected checkpoint,
recipe digest, class mapping, versions and static-export verification.

Registry changes create/lock one project/recipe row, check the expected revision,
qualify against persisted reports, and advance the complete champion/rollback
pointers together with an append-only principal-attributed event. Each operation
uses a savepoint inside the caller's outer transaction; even a caught event-write
failure cannot leave an advanced pointer. Concurrent first promotions use an
insert-on-conflict followed by row locking. Other recipes are never locked or
advanced by that transition. Migration 0002 freezes its table definitions locally,
independent of future application metadata, and adds immutable-evidence and registry-scope
guards. No HTTP promotion endpoint is exposed; authentication and admin
authorization remain required at the future service boundary. The internal
store's actor ID is provenance, not an authorization capability.

## Controlled CPU backend

`labelloop_worker_ml.training.LocalUltralyticsBackend` consumes worker-owned paths,
a hash-pinned initialization checkpoint, a compiled manifest and complete source
validation truth. It verifies manifest/file/class-map hashes and request lineage
before starting. Validation membership must match exactly, without duplicates.
Each compiled sample pins `source_truth_sha256`: SHA-256 of sorted-key compact JSON
for the complete source `EvaluationImage`, with objects sorted by object ID. This
binds original dimensions, pixel digest, annotation revision, object IDs/classes,
source boxes and occlusion/truncation flags, including objects outside crops.
Missing lineage or a stale truth digest fails before creating a run directory;
derivative-retained boxes alone are not authoritative validation truth.
A disposable dataset copy receives vendor label caches; the
compiled release is never edited. The worker strips the test split from the run's
data configuration and refuses validation sources present in training/test.

The pinned trainer overrides rectangular validation batching with square datasets.
Actual train and validation preprocessing and native inference inputs are asserted
against the recipe and observed model stride. The real training transform is
wrapped with per-sample before/after target counts and counts at or below six
pixels. Any lost target fails the run and persists the failed audit evidence.
Implicit Albumentations remains forbidden.

Every epoch checkpoint is hashed and evaluated through the source-frame runner.
The worker records a separate AP collection confidence floor (at most 0.001),
while precision/recall retain the release's operating confidence. All geometry,
NMS and tiling settings remain identical. Selection uses the source-frame score,
not the vendor diagnostic `best.pt`. The result binds the selected epoch digest
and resolved argument audit. Shapes, retention, per-epoch reports and arguments
are written even when training fails where available.

Actions now has an `ml-smoke` job required through `ci-ok`. It initializes a
seeded yolo26n architecture from the pinned package without downloading pretrained
weights, then trains one epoch on the 16-image CC0 interface fixture independently
at 320 and 640. This is pipeline evidence, not model-quality qualification or a
claim that pretrained weights were exercised. Linux Torch and torchvision use
the CPU index. Audit records are uploaded as an Actions artifact; the PR records
the run/job links and conclusions for its exact head.

## Static export and persistent registry smoke

`export_and_compare` exports a static float32 ONNX graph, validates its actual
CPU Runtime signature, then compares native and exported detections on identical
observed tensors from recipe samples. Tolerances are 0.01 input pixels and 1e-5
confidence, with exact class/count agreement. Wrong shapes, nonfinite output and
geometry/score/class drift fail closed. Export verification and model persistence
both require at least one detection across the sample set, rather than accepting
vacuous empty-output agreement. Individual negative samples may still be empty;
the synthetic smoke has stricter nonempty/below-cap assertions per sample.
This sample check is not a universal equivalence guarantee.

The real-loader boundary cases exercise the strict two-pixel cutoff, a greater-
than-two-pixel target, four/six-pixel targets, edge boxes and verified negatives
at both sizes. Pinned Ultralytics keeps `args.imgsz` as an integer and its
normalized size in `exporter.imgsz`; the audit checks both without rewriting or
relaxing requested arguments.

The extended Actions smoke uses a disposable pinned PostgreSQL service, runs
Alembic, persists real training/evaluation/export lineage, promotes competing
candidates using two independent database connections, rolls back, reloads the
whole release on fresh connections and verifies the other recipe is unchanged.
It injects an event-write failure to check transactional rollback, rejects stale
revisions and duplicate evaluation identities, and checks append-only guards.
The fixture's zero mAP target exercises wiring only; it is not a production
quality threshold. Registry snapshots join the existing Actions audit artifacts.

## Fresh-context review corrections

One fresh-context read-only pass found three correctness gaps, addressed with
regressions and accepted in the initial QA pass:

- Training now rejects unlisted files and symlinks, copies only the frozen file
  allowlist, and hashes those actual copies. A late unlisted file cannot enter
  the loader through a recursive directory copy. Real smoke negatives add an
  unlisted image/label and a symlink and require rejection before run creation.
- Training stores an immutable input-manifest snapshot. Model creation uses that
  training-time recipe/class mapping and rejects any divergence in current
  inputs. Database triggers prevent changing immutable recipes or validated
  variant content, while retaining the mutable external Platform mapping field.
- `save_evaluation` appends source-frame evidence for an existing model on another
  frozen variant of the same project/recipe/class contract. These records and
  model/evaluation links are append-only: the initial model payload is never
  rewritten. Loaded descriptors and promotion-event snapshots include additional
  linked evaluation IDs, allowing an incumbent and challenger trained on different
  releases to be compared on a shared development release. A synthetic persistence
  regression exercises that distinct-release lineage; it does not claim another
  actual training run or independent final-test qualification.

The synthetic smoke uses confidence 0.005 and max_det=1000 at both sizes. That
retains the complete seeded coarse-score cohort without cutting through nearly
tied random predictions at the detection cap. It asserts nonempty results
strictly below the cap and retains the 0.01-pixel/1e-5 tolerances and exact
class/count checks. This is fixture stability, not permission to accept drifting
real exports. Failed comparisons preserve both detection arrays for diagnosis.

## Milestone acceptance gates

- Exact-head GitHub Actions run/job conclusions must be recorded in the PR,
  including the CPU training/export/PostgreSQL smoke at both sizes.
- Independent native review, applicable security checks, finding dispositions
  and the merge gate remain required. Passing fixture tests alone do not qualify
  model quality or complete an unmerged milestone.

No paid Platform or LLM calls are needed. Full suites and CI gates run only on
GitHub Actions. No local training or test execution is claimed by this checkpoint.

REA angle: use the pinned Ultralytics implementation to determine supported
trainer/validator hooks; use pycocotools as an independent metric oracle. No
vendor code is copied into core and no vendor imports cross the licence boundary.
