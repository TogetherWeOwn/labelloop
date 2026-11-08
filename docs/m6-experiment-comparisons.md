# Frozen experiment comparisons

Plan §§9.4, 10.2 and M6 require controlled comparisons, not another trainer or
scheduler. `labelloop_core.experiments` owns immutable configuration and read-only
comparison. `labelloop_worker_ml.experiments.execute_cell` executes one explicitly
selected cell through `LocalUltralyticsBackend`; the future bounded controller
can call that port without giving model output authority to choose experiments.
No Platform requests, downloads, paid dispatch, promotion or state transitions
are performed here.

## Configuration and lineage

Construct an `ExperimentManifest` before dispatch. Its SHA-256 covers:

- experiment/project identity, split version and approved base checkpoint digest;
- source IDs, source groups, pixel and complete-truth digests and distinct curated,
  expansion, development, calibration and locked-final roles;
- two independently pinned 320/640 recipes and their complete recipe digests;
- per-recipe warm checkpoints, their approved-base ancestry and curated-only
  training groups (any expansion ancestry, even within a mixed group, is rejected);
- per-recipe/per-cohort dataset release, manifest and exact training membership;
- class-map digest and explicit class mirror-safety declarations;
- all seeds, epochs, batch, augmentation profiles, declared metric, class/slice
  support minima, regression tolerance and minimum gain.

The current ontology record predates mirror-safety metadata. Supply the approved
catalog's full safety declarations explicitly; absence is not approval. The worker
checks that flipping covers the entire compiled class map. CI's fixture has no
mirror-safety approval and uses a color-only alternative.

`manifest.cells()` enumerates the finite factorial configuration; it does not
schedule it. `manifest.request(cell, run_id)` creates a normal M2 TrainingRequest.
The baseline `no-scene-composition-v1` retains fliplr/scale/translate zero and the
other delivered explicit defaults. Alternatives may change only named horizontal
flip, scale, translation or HSV strengths; inherited scene composition, vertical
flip, perspective and optional Albumentations remain disabled. The reserved
baseline name cannot be reused with changed settings. All profiles retain the
same strict dataloader target-retention contract: a transform that drops a target
fails the run rather than producing a flattering comparison.

The caller supplies already-verified, immutable checkpoint ancestry. This module
cannot reconstruct ancestral training membership from a weights file. Persist
that provenance with checkpoint registration; do not infer it from filenames.
The worker checks recipe/source/truth/group/cohort bindings before training.
Checkpoint bytes, compiler membership/file digests and actual training/resolved
arguments are then verified by the existing backend.

## Evidence and interpretation

`collect_arm` reuses an already completed M2 training result, or `execute_cell`
trains one cell. Both retain actual training, validation and inference shapes,
loader before/after counts including small targets and negatives, selected epoch,
resolved arguments, static ONNX signature and native/export prediction checks.
Every compiled file is hash/membership checked again at collection, and inference
and export use a private, hash-verified copy of the validation pixels. Complete
and group evaluation reports carry per-source truth digests; imported evidence
must bind those digests to the experiment, not merely reuse source IDs/counts.
The existing `EvaluationRunner` scores the complete source-frame development
release and each source group from the same predictions. Full-frame coverage is
required, so difficult regions cannot disappear from a comparison.

`compare_experiment` requires every configured cell exactly once. It compares:

1. warm-start against the same approved base with all other settings held fixed;
2. each augmentation alternative against the reserved baseline;
3. expanded versus curated-only training when expansion is configured.

Results remain per recipe. They report seeds, independent group count, images,
object/class support, per-group deltas and regressions on each supported class
and declared critical slice. The declared statistic is
`source-group-macro-map50-95-v1`: average paired original-frame mAP50–95 deltas
across seeds within each source group, then equal-weight groups. It is deliberately
not pooled COCO AP. Repeated seeds are not extra independent images/groups.

The deterministic percentile paired cluster bootstrap uses 2,000 resamples of
source groups (all seeds within a group travel together), a recorded RNG seed,
and a nominal 95% interval. A single group gives no interval and reports
insufficient evidence. Few groups, nonrepresentative sampling, label uncertainty,
and adaptive development reuse limit interpretation; the interval is conditional
on these pinned seeds/data and does not establish universal quality or cover
unknown training-environment variation. Every group needs positive target support
for this AP metric. Negative-only groups make this metric unsupported; they are
not silently dropped. Negative images within a supported group remain included
in scoring. Likewise, an arm without a non-vacuous export check (at least one
operating-threshold detection) remains incomplete rather than being scored as
qualified. A future negative-scene metric/export policy requires its own explicit
contract; this harness does not weaken the existing M2 export guard to fill a
comparison. Aggregate gains cannot conceal a class/critical-slice regression. Other outcomes distinguish observed unqualified
gain, plateau/inconclusive evidence, regression and fixture-only wiring.

Calibration and locked-final sets are never accepted as comparison evidence.
Thresholds must be calibrated separately then pinned in the recipe policies.
Select using development results before obtaining a fresh locked final assessment;
a final set used to guide a change must be reclassified by its owning qualification
workflow. This harness cannot certify holdout independence or adjudication.

`m6_exit_established` is always false. Random initialization and fixture pipelines
can verify wiring only. Independently adjudicated non-game annotation quality and
an agreed project metric, followed by independent qualification, remain necessary
for the real M6 exit and belong to the existing quality-evidence source. No
experiment result promotes a model or satisfies that external gate.

## Verification

- `packages/core/tests/test_experiments.py`: deterministic paired statistics,
  curated/expanded cohorts, mirror safety, leakage, baseline drift, ancestry,
  malformed/missing evidence, unsupported coverage, honest gains and regressions.
- `workers/ml/tests/test_experiment_cpu.py`: eight one-epoch CPU cells (two sizes,
  base/warm and baseline/color alternative), no downloaded weights. It reuses the
  delivered M2 trainer, retention audit, evaluator and ONNX comparison.
- The existing Actions `ml-smoke` job runs both M2 and this experiment test and
  uploads JSON evidence. All full suites and real training run only on Actions.

REA angle: reuse the delivered M2 records and the already-researched pinned
Ultralytics transform behavior; no third-party code copied, trainer replaced,
experiment service introduced or dependency added.
