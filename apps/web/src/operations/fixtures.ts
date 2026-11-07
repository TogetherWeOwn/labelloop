import type { AuditEvent, AuditPort, AuditQuery, CostEntry, CostsRecord, ModelRelease, RecipeOperationsRecord } from './contracts.ts';

// Invented, licence-clean display records adapted from the accepted screen
// mockups. They are not provider responses or evidence of real qualification.
export const fixtureTime = '2026-11-07T02:00:00Z';
const cost = (entry: Partial<CostEntry> & Pick<CostEntry, 'operation_id' | 'state' | 'estimate' | 'result'>): CostEntry => ({
  project_id: 'shelf-demo', iteration_id: 'iteration-4', object_id: 'run-0030', recipe_id: 'full-letterbox-320-v1',
  month: '2026-11', at: '2026-11-07T01:52:00Z', kind: 'Training', approved_by: 'Ade', actual: null, ...entry,
});
export const costsFixture: CostsRecord = {
  project_id: 'shelf-demo', revision: 7, month: '2026-11', iteration_id: 'iteration-4', price_version: 'v4',
  policy: { monthly_limit: 40_000_000, iteration_limit: 5_000_000, operation_limit: 5_000_000, autonomous_spend: false },
  pause_reason: 'insufficient_credits',
  entries: [
    cost({ operation_id: 'op-118', state: 'reserved', estimate: 1_380_000, result: 'Queued' }),
    cost({ operation_id: 'op-117', object_id: 'batch-49', kind: 'LLM review', recipe_id: null, at: '2026-11-07T01:12:00Z', state: 'settled', estimate: 410_000, actual: 370_000, approved_by: 'Policy (autonomous)', result: 'Done' }),
    cost({ operation_id: 'op-116', object_id: 'run-0029', recipe_id: 'full-letterbox-640-v1', at: '2026-11-06T12:10:00Z', iteration_id: 'iteration-3', state: 'unconfirmed', estimate: 1_320_000, result: 'Completed · billing attribution unconfirmed' }),
    cost({ operation_id: 'op-115', object_id: 'run-0028', at: '2026-11-05T18:40:00Z', iteration_id: 'iteration-3', state: 'settled', estimate: 1_320_000, actual: 170_000, result: 'Failed · stopped at 14 m' }),
    cost({ operation_id: 'op-114', object_id: 'batch-900', kind: 'Auto-annotation', recipe_id: null, at: '2026-11-05T09:02:00Z', state: 'refused', estimate: 5_900_000, approved_by: null, result: 'Over per-operation limit' }),
    cost({ operation_id: 'op-113', object_id: 'predict-1', kind: 'Predict', at: '2026-10-30T09:00:00Z', month: '2026-10', iteration_id: 'iteration-2', state: 'settled', estimate: 20_000, actual: 20_000, result: 'Done' }),
  ],
};

const event = (id: string, at: string, kind: AuditEvent['kind'], name: string, type: 'person' | 'service', sentence: string, label: string, refused = false): AuditEvent => ({
  id, at: `2026-11-07T${at}Z`, kind, principal: { id: name.toLowerCase(), name, type }, sentence, refused,
  record: { label, href: `/records/${encodeURIComponent(label)}` },
});
export const auditFixture: readonly AuditEvent[] = [
  event('audit-6', '01:58:12', 'Promotion', 'Ade', 'person', 'Promoted m-0009 for 640, replacing m-0007', 'eval-0044'),
  event('audit-5', '01:52:40', 'Budget', 'Ade', 'person', 'Approved $1.38 for run-0030', 'ledger-118'),
  event('audit-4', '01:41:03', 'Review', 'Mora', 'person', 'Accepted IMG_0412.png: 5 proposals decided, 1 box added', 'revision-7'),
  event('audit-3', '01:30:00', 'Connection', 'worker-platform', 'service', 'Checked Ultralytics key ••••9f3c: workspace shelf-team', 'connection-fingerprint'),
  event('audit-2', '01:12:55', 'Review', 'worker-llm', 'service', 'Proposed a class change on object 4 in IMG_0412.png', 'revision-5'),
  event('audit-1', '00:58:21', 'Review', 'worker-llm', 'service', "Tried to mark object 2 reviewed: refused, models can't set human fields", 'revision-4', true),
];

// The caller supplies a pre-authorized set: this simulates the SERVER, not a
// client-side access check. The real adapter must not download admin rows first.
export function recordedAuditPort(authorizedEvents: readonly AuditEvent[]): AuditPort {
  const select = (query: AuditQuery) => authorizedEvents.filter((row) => query.kinds.includes(row.kind)
    && query.principals.includes(row.principal.type)
    && `${row.sentence} ${row.principal.name} ${row.record?.label ?? ''}`.toLowerCase().includes(query.search.toLowerCase())
    && (!query.from || row.at.slice(0, 10) >= query.from) && (!query.through || row.at.slice(0, 10) <= query.through)
  ).sort((a, b) => b.at.localeCompare(a.at));
  return {
    query: (query, cursor, signal) => {
      signal.throwIfAborted();
      const all = select(query), offset = cursor === null ? 0 : Number(cursor);
      // Small page exercises pagination without inventing extra display events.
      return Promise.resolve({ events: all.slice(offset, offset + 3), total: all.length, next_cursor: offset + 3 < all.length ? String(offset + 3) : null });
    },
    exportJsonLines: (query, signal) => { signal.throwIfAborted(); return Promise.resolve(new Blob([select(query).map((row) => JSON.stringify(row)).join('\n')], { type: 'application/x-ndjson' })); },
  };
}

const release = (id: string, size: 320 | 640): ModelRelease => ({
  release_id: id, project_id: 'shelf-demo', recipe_id: `full-letterbox-${size}-v1`,
  weights_sha256: '4'.repeat(64), preprocessing_sha256: '5'.repeat(64), class_map_sha256: '3'.repeat(64),
  class_ids: ['Oat milk', 'Whole milk', 'Price tag', 'Almond milk'],
  policy: { imgsz: size, nms: 'external', iou: 0.7, conf: 0.25, max_det: 300, agnostic_nms: false },
  ultralytics_version: '8.4.204', torch_version: '2.9.1', export_sha256: '6'.repeat(64), export_format: 'onnx',
  static_input_signature: [1, 3, size, size], evaluation_run_ids: [size === 640 ? 'eval-0044' : 'eval-0043'], tiling_mode: 'off',
});
export const recipesFixture: readonly RecipeOperationsRecord[] = ([640, 320] as const).map((size) => ({
  project_id: 'shelf-demo', recipe_id: `full-letterbox-${size}-v1`, revision: 3,
  champion: release(size === 640 ? 'm-0007' : 'm-0006', size), candidate: release(size === 640 ? 'm-0009' : 'm-0008', size),
  qualification: { state: size === 640 ? 'qualified' : 'unqualified', reason: size === 640 ? 'Passes the recorded fixture gates for this pipeline.' : 'Fails the small-target recall gate. Almond milk has no evaluation support.', evidence: [{
    id: size === 640 ? 'eval-0044' : 'eval-0043', set_version: 'shelf-final-v2', set_role: 'locked_final_test', source_groups: 60, images: 120, objects: size === 640 ? 820 : 780,
    protocol: 'Original-frame evaluation v1 · complete source ground truth', pipeline_id: `fixture-pipeline-${size}-v1`,
    classes: [{ id: 'Oat milk', support: 300, supported: true, result: 'Recorded gate passed' },
      { id: 'Whole milk', support: 280, supported: true, result: 'Recorded gate passed' },
      { id: 'Price tag', support: 200, supported: true, result: size === 640 ? 'Recorded gate passed' : 'Small-target recall gate failed' },
      size === 640 ? { id: 'Almond milk', support: 40, supported: true, result: 'Recorded gate passed' } : { id: 'Almond milk', support: 0, supported: false, reason: 'Absent from this evaluation set; aggregate mAP cannot establish support.' }],
  }] },
  canary: size === 640 ? { completed_fresh_groups: ['fresh-1', 'fresh-2', 'fresh-3'], planned_fresh_groups: Array.from({ length: 10 }, (_, i) => `fresh-${i + 1}`), excluded_training_groups: ['train-session-1', 'train-session-2'], evidence_id: 'canary-fixture-1' } : null,
  drift: size === 640 ? [{ scope: 'class', id: 'price-tag', label: 'Price tag', reason: 'audit errors rose to 4%.', audit_sample_id: 'audit-sample-18' }, { scope: 'source', id: 'store-9', label: 'Store 9', reason: 'changed camera/layout conditions.', audit_sample_id: 'audit-sample-19' }] : [],
  rollback: { release: release(size === 640 ? 'm-0005' : 'm-0004', size), previous_release_id: size === 640 ? 'm-0007' : 'm-0006', promoted_at: '2026-10-30T14:02:00Z', principal: 'Ade', reason: 'Recorded per-recipe promotion', gate_results: ['Export valid', 'Fixed-pipeline evaluation passed', 'Supported-class coverage recorded'] },
}));
