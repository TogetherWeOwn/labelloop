// Read projections, not generated API contracts. Live adapters must validate their
// responses and bind authorization to the server session, never these role props.
export type Role = 'admin' | 'reviewer';
export type ViewState<T> =
  | { status: 'loading' }
  | { status: 'error'; retry: () => void }
  | { status: 'ready'; data: T; updatedAt: string; stale: boolean };

// Matches core BudgetPolicy / SpendRequest: integer micro-USD, not cents.
export interface BudgetPolicy {
  monthly_limit: number | null;
  iteration_limit: number | null;
  operation_limit: number | null;
  autonomous_spend: boolean;
}
export type OperationKind = 'Training' | 'LLM review' | 'Auto-annotation' | 'Predict';
export type CostScope =
  | { kind: 'project'; id: string }
  | { kind: 'iteration' | 'run' | 'review_batch'; id: string };
export interface CostEntry {
  operation_id: string;
  project_id: string;
  iteration_id: string;
  object_id: string;
  recipe_id: string | null;
  month: string;
  at: string;
  kind: OperationKind;
  approved_by: string | null;
  estimate: number;
  // Refusals are durable dispatch attempts, not core LedgerEntry reservations.
  state: 'reserved' | 'dispatched' | 'unconfirmed' | 'settled' | 'cancelled' | 'insufficient_credits' | 'refused';
  actual: number | null;
  result: string;
}
export interface CostsRecord {
  project_id: string;
  revision: number;
  month: string;
  iteration_id: string;
  price_version: string;
  policy: BudgetPolicy;
  pause_reason: 'insufficient_credits' | 'estimate_overrun' | null;
  entries: readonly CostEntry[];
}
export interface CostCommands {
  // Atomic server checks include admin, CSRF, revision and budget invariants.
  savePolicy: (policy: BudgetPolicy, expectedRevision: number) => Promise<void>;
  resumePaid: (expectedRevision: number) => Promise<void>;
}

export type AuditKind = 'Review' | 'Promotion' | 'Connection' | 'Budget';
export interface AuditEvent {
  id: string;
  at: string;
  principal: { id: string; name: string; type: 'person' | 'service' };
  kind: AuditKind;
  sentence: string; // Server-redacted display text; never raw provider payloads.
  refused: boolean;
  record: { label: string; href: string } | null;
}
export interface AuditQuery {
  kinds: readonly AuditKind[];
  principals: readonly ('person' | 'service')[];
  search: string;
  from: string;
  through: string;
}
export interface AuditPage {
  events: readonly AuditEvent[];
  total: number;
  next_cursor: string | null;
}
export interface AuditPort {
  // The server restricts reviewer reads AND exports to review events before
  // filtering/pagination/counting. No role or principal ID comes from the client.
  query: (query: AuditQuery, cursor: string | null, signal: AbortSignal) => Promise<AuditPage>;
  exportJsonLines: (query: AuditQuery, signal: AbortSignal) => Promise<Blob>;
}

// Field-for-field ModelRelease projection from core/registry.py.
export interface ModelRelease {
  release_id: string;
  project_id: string;
  recipe_id: string;
  weights_sha256: string;
  preprocessing_sha256: string;
  class_map_sha256: string;
  class_ids: readonly string[];
  policy: { imgsz: 320 | 640; nms: 'external' | 'end2end'; iou: number; conf: number; max_det: number; agnostic_nms: boolean };
  ultralytics_version: string;
  torch_version: string;
  export_sha256: string;
  export_format: string;
  static_input_signature: readonly number[];
  evaluation_run_ids: readonly string[];
  tiling_mode: 'off' | 'application';
}
export interface QualificationEvidence {
  id: string;
  set_version: string;
  set_role: 'locked_final_test' | 'development' | 'rotating_audit';
  source_groups: number;
  images: number;
  objects: number;
  protocol: string;
  pipeline_id: string;
  classes: readonly ({ id: string; support: number } & (
    | { supported: true; result: string }
    | { supported: false; reason: string }
  ))[];
}
export interface RecipeOperationsRecord {
  project_id: string;
  recipe_id: string;
  revision: number;
  champion: ModelRelease | null;
  candidate: ModelRelease | null;
  qualification: { state: 'evaluating' | 'qualified' | 'unqualified'; reason: string; evidence: readonly QualificationEvidence[] };
  canary: { completed_fresh_groups: readonly string[]; planned_fresh_groups: readonly string[]; excluded_training_groups: readonly string[]; evidence_id: string } | null;
  drift: readonly { scope: 'class' | 'source'; id: string; label: string; reason: string; audit_sample_id: string }[];
  rollback: { release: ModelRelease; previous_release_id: string; promoted_at: string; principal: string; reason: string; gate_results: readonly string[] } | null;
}
