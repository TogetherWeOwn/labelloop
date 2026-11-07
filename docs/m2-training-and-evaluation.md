# Local training and evaluation — implementation checkpoint

Status: **partial implementation, not a qualified training pipeline**.
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
area-range summaries. Crop union area, wholly uncovered targets and partially
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
policy identity, per-class support/quality and incumbent non-regression on the
same frozen evaluation release. Rollback swaps the complete previous release;
it cannot cross project or recipe boundaries.

These transitions **do not yet provide durable atomic persistence**. The database
adapter must lock the recipe row, verify the expected revision, persist the
release/event and advance the champion in one transaction. No HTTP promotion
endpoint is exposed by this increment; authenticated principal enforcement stays
at the service boundary.

## Controlled CPU backend

`labelloop_worker_ml.training.LocalUltralyticsBackend` consumes worker-owned paths,
a hash-pinned initialization checkpoint, a compiled manifest and complete source
validation truth. It verifies manifest/file/class-map hashes and request lineage
before starting. A disposable dataset copy receives vendor label caches; the
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
the CPU index. Audit records are uploaded as an Actions artifact. Execution and
exact-head success remain to be verified in Actions.

## Remaining before milestone acceptance

- Verify and repair the real CPU backend and retention/shape evidence in Actions.
- Add real-loader small-target and negative-image boundary cases beyond the
  fixture training run and synthetic transform-loss regression.
- Export static ONNX, assert its runtime signature and compare native/exported
  outputs within a declared tolerance.
- Persist training/evaluation lineage and atomic per-recipe registry transitions.
- Extend the Actions CPU smoke through native/ONNX equivalence and persistent,
  independent promotion and rollback for both recipes.
- Record exact-head Actions run/job conclusions and complete independent review
  before marking the draft PR ready. Existing required checks remain mandatory.

No paid Platform or LLM calls are needed. Full suites and CI gates run only on
GitHub Actions. No local training or test execution is claimed by this checkpoint.

REA angle: use the pinned Ultralytics implementation to determine supported
trainer/validator hooks; use pycocotools as an independent metric oracle. No
vendor code is copied into core and no vendor imports cross the licence boundary.
