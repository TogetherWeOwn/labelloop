import type { ReactNode } from 'react';

export type Role = 'admin' | 'reviewer';
export type Screen = 'new-project' | 'releases' | 'recipes' | 'runs' | 'evaluation' | 'models';
export type RecipeId = 'full-letterbox-640-v1' | 'full-letterbox-320-v1';
export type SetupStep = 'Scope' | 'Classes' | 'Evidence' | 'Labelling guide' | 'Sources and groups' | 'Check and publish';
export type JobState = 'Queued' | 'Running' | 'Completed' | 'Failed' | 'Cancelled' | 'Paused';
export type Fact = readonly [string, ReactNode];
export interface ClassRow {
  id: string; name: string; aliases: string; parent: string; examples: number | null;
  exclusions: string | null; mirror_safe: boolean | null; provenance: 'Proposed' | 'Approved by an admin' | null;
}
export interface ReferenceRow {
  id: string; class_id: string; source: string; permitted_use: string; view: string;
  lighting: string; scale: string; confused_with: string; provenance: 'Proposed' | 'Approved by an admin';
}
export type ReferenceInput = Pick<ReferenceRow, 'class_id' | 'source' | 'permitted_use' | 'view' | 'lighting' | 'scale' | 'confused_with'>;
// Read-only view projections. Missing detail stays unavailable, never inferred
// from collection totals; final field mapping belongs to the generated client adapter.
export interface DatasetImportReport {
  id: string; source_name: string; rows: number;
  state: 'Rejected' | 'Proposed';
  errors: readonly { row: number; field: string; message: string }[];
}
export interface SourceAssetFact {
  id: string; width: number; height: number; sha256: string;
  source_group: string; split: string | null; assignment_revision: string | null;
}
export interface ProjectDraft {
  id: string; revision: number; saved_at: string;
  scope: { name: string | null; detect: string; representation: string | null; intended_use: string | null; specificity: string | null; permitted_use: string; external_providers: string };
  classes: readonly ClassRow[]; references: readonly ReferenceRow[] | null;
  dataset_import?: DatasetImportReport | null;
  source_assets?: readonly SourceAssetFact[] | null;
  annotation_guide: string;
  guide: { extent: string; truncation: string; occlusion: string; reflections: string; min_target_px: number; exhaustive: boolean; background: string } | null;
  sources: { images: number; groups: number | null; metadata_keys: string | null; pending_pairs: readonly { id: string; left: string; right: string }[]; split_summary: string | null };
  approved_steps: readonly SetupStep[] | null;
  versions: Readonly<Record<string, string>>; published: boolean;
}
export interface ReleaseRecord {
  id: string; revision: number; previous_id: string; state: 'Draft' | 'Building' | 'Frozen';
  images: number; objects: number; excluded: number; complete: number;
  deltas: readonly [number, number, number];
  provenance: { reviewed: number; auto_accepted: number; policy: string };
  classes: readonly { name: string; train: number; val: number; test: number; delta: number; minimum: number }[];
  changes: readonly { image_id: string; description: string }[];
  exclusions: readonly { reason: string; count: number; image_ids: readonly string[] }[];
  failed_checks: readonly string[]; identical: boolean; quarantine: string | null;
  manifest: string; manifest_sha256: string; frozen_at: string | null; frozen_by: string | null;
  progress: { done: number; total: number };
}
export interface RecipePreview {
  id: RecipeId; side: 320 | 640; locked: boolean; transform_record: string;
  source_id: string; source_width: number; source_height: number;
  resized_width: number; resized_height: number; padding: readonly [number, number, number, number];
  sx: number; sy: number; forward: readonly number[]; inverse: readonly number[];
  // Already transformed by the compiler. The browser never derives source boxes.
  boxes: readonly { id: string; class_name: string; xyxy: readonly [number, number, number, number]; provenance: 'Reviewed by a person' | 'Auto-accepted'; clipped: boolean; consequence: string; flag: 'none' | 'warn' | 'exclude' }[];
  flags: { tiny: number; below_minimum: number; clipped: number; excluded_images: number };
  job: JobState | null; failures: readonly string[]; manifest: string; validation: string;
}
export interface TrainingRun {
  id: string; recipe_id: RecipeId; dataset_release_id: string; state: JobState;
  backend: 'local_ultralytics'; epoch: number; epochs: number; reason: string | null;
  initialization_sha256: string; updated_at: string; images: number;
  best_validation: { value: number; epoch: number; set: string; n: number } | null;
  checkpoints: readonly { epoch: number; sha256: string }[];
  retention: { before: number; after: number; dropped: readonly string[] };
  mismatch: readonly { key: string; requested: string; resolved: string }[];
  arguments: Readonly<Record<string, string | number | boolean>>;
}
export interface Metric { current: number | null; candidate: number }
export type ClassMetric = { name: string; support: number; minimum: number } & (
  { supported: true; metric: Metric } | { supported: false; reason: string }
);
export interface EvaluationRecord {
  id: string; recipe_id: RecipeId; candidate_id: string; current_id: string | null;
  set_id: string; set_role: 'development' | 'locked_final'; frames: number; objects: number;
  opened: number; state: JobState; progress: number; conf: number;
  overall: { map50_95: Metric; precision: Metric; recall: Metric };
  classes: readonly ClassMetric[];
  slices: readonly { name: string; n: number; unit: 'objects' | 'frames'; current: string; candidate: string }[];
  lost_frames: number; gates: readonly { name: string; passed: boolean }[];
  verdict: string; qualified: boolean; diagnostics: string;
}
// Mirrors labelloop_core.registry.ModelRelease, including the atomic release fields.
export interface ModelRelease {
  release_id: string; project_id: string; recipe_id: RecipeId;
  weights_sha256: string; preprocessing_sha256: string; class_map_sha256: string;
  class_ids: readonly string[]; policy: { imgsz: 320 | 640; nms: 'external' | 'end2end'; iou: number; conf: number; max_det: number; agnostic_nms: boolean };
  ultralytics_version: string; torch_version: string; export_sha256: string;
  export_format: 'onnx'; static_input_signature: readonly [number, number, number, number];
  evaluation_run_ids: readonly string[]; tiling_mode: 'off' | 'application';
}
export interface RegistryRecord {
  project_id: string; recipe_id: RecipeId; revision: number;
  champion: ModelRelease | null; candidate: ModelRelease | null; rollback: ModelRelease | null;
  qualification: 'qualified' | 'unqualified' | 'evaluating'; reason: string;
  evidence: { set: string; frames: number; objects: number };
  history: readonly { time: string; change: string; principal: string }[];
}
export interface ManagementSnapshot {
  provenance: 'fixture' | 'server'; updated_at: string; stale: boolean; paid_paused: boolean;
  draft: ProjectDraft; release: ReleaseRecord | null; recipes: readonly RecipePreview[];
  runs: readonly TrainingRun[]; evaluations: readonly EvaluationRecord[]; registry: readonly RegistryRecord[];
}
export type ManagementCommand =
  | { kind: 'save_setup'; draft_id: string; expected_revision: number; step: SetupStep; values: Readonly<Record<string, string | number | boolean>> }
  | { kind: 'approve_step'; draft_id: string; expected_revision: number; step: SetupStep }
  | { kind: 'accept_class' | 'accept_reference'; draft_id: string; expected_revision: number; id: string }
  | { kind: 'add_class'; draft_id: string; expected_revision: number; name: string }
  // Fixture capture only; not a paid-generation authorization or generated wire command.
  | { kind: 'draft_assistant_classes'; draft_id: string; expected_revision: number }
  // Selection metadata only. No bytes, parsing, upload or import authority.
  | { kind: 'request_dataset_import'; draft_id: string; expected_revision: number; file: { name: string; size: number } }
  | { kind: 'request_source_upload'; draft_id: string; expected_revision: number; files: readonly { name: string; size: number }[] }
  | { kind: 'request_reference_upload'; draft_id: string; expected_revision: number; file: { name: string; size: number }; values: ReferenceInput }
  | { kind: 'edit_class'; draft_id: string; expected_revision: number; id: string; values: { name: string; aliases: string; parent: string; exclusions: string; mirror_safe: boolean } }
  | { kind: 'add_reference'; draft_id: string; expected_revision: number; values: ReferenceInput }
  | { kind: 'resolve_pair'; draft_id: string; expected_revision: number; pair_id: string; same_group: boolean }
  | { kind: 'publish_project'; draft_id: string; expected_revision: number }
  | { kind: 'freeze_release'; release_id: string; expected_revision: number }
  | { kind: 'compile'; release_id: string; recipe_ids: readonly RecipeId[] }
  | { kind: 'new_recipe_version'; recipe_id: RecipeId }
  | { kind: 'start_local_run'; release_id: string; recipe_id: RecipeId; initialization_sha256: string; epochs: number }
  | { kind: 'cancel_local_run'; run_id: string }
  | { kind: 'open_locked_test'; evaluation_id: string; acknowledge_development_reuse: boolean }
  | { kind: 'promote'; project_id: string; recipe_id: RecipeId; expected_revision: number; release_id: string }
  | { kind: 'rollback'; project_id: string; recipe_id: RecipeId; expected_revision: number; release_id: string; reason: string };
export interface ManagementPort {
  read(signal: AbortSignal): Promise<ManagementSnapshot>;
  // Absent on recorded fixtures. Real commands come only from the authenticated facade.
  execute?: (command: ManagementCommand, signal: AbortSignal) => Promise<{ message: string }>;
}
export interface Actions {
  role: Role; connected: boolean; busy: boolean;
  send: (command: ManagementCommand) => Promise<boolean>;
}
export interface ScreenProps { data: ManagementSnapshot; actions: Actions }
export interface ModelExtensions {
  // Slot for the separately owned canary/drift component; no second registry card.
  render?: (recipe_id: RecipeId) => ReactNode;
}
