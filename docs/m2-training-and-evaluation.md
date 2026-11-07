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

## Remaining before milestone acceptance

- Wire the pinned `local_ultralytics` backend to actual train/validation/inference
  tensor hooks and per-epoch locally hashed checkpoints.
- Audit sample target identities/counts before and after actual dataloader
  transforms, including targets at or below six pixels.
- Export static ONNX, assert its runtime signature and compare native/exported
  outputs within a declared tolerance.
- Persist training/evaluation lineage and atomic per-recipe registry transitions.
- Add the Actions CPU smoke: yolo26n, 1–3 epochs, at least 16 fixture images,
  independently at 320 and 640, then source evaluation, promotion and rollback.
- Record exact-head Actions run/job conclusions and complete independent review
  before marking the draft PR ready. Existing required checks remain mandatory.

No paid Platform or LLM calls are needed. Full suites and CI gates run only on
GitHub Actions. No local training or test execution is claimed by this checkpoint.

REA angle: use the pinned Ultralytics implementation to determine supported
trainer/validator hooks; use pycocotools as an independent metric oracle. No
vendor code is copied into core and no vendor imports cross the licence boundary.
