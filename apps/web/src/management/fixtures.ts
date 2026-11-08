import type { EvaluationRecord, ManagementPort, ManagementSnapshot, ModelRelease, RecipeId, RecipePreview } from './contracts.ts';

export const fixtureTime = '2026-11-07T01:58:00Z';
export const recipe640: RecipeId = 'full-letterbox-640-v1';
export const recipe320: RecipeId = 'full-letterbox-320-v1';
const digest = (letter: string) => letter.repeat(64);
const model = (side: 320 | 640, id: string): ModelRelease => ({
  release_id: id, project_id: 'shelf-fixture', recipe_id: side === 640 ? recipe640 : recipe320,
  weights_sha256: digest('a'), preprocessing_sha256: digest('b'), class_map_sha256: digest('c'),
  class_ids: ['oat', 'soy', 'almond', 'tag'],
  policy: { imgsz: side, nms: 'external', iou: 0.7, conf: 0.25, max_det: 300, agnostic_nms: false },
  ultralytics_version: '8.4.204', torch_version: '2.9.1+cpu', export_sha256: digest('d'),
  export_format: 'onnx', static_input_signature: [1, 3, side, side],
  evaluation_run_ids: [side === 640 ? 'eval-0044' : 'eval-0045'], tiling_mode: 'off',
});
// Recorded compiler geometry for the licence-clean design scene. Never calculate
// transforms in a component. The Python contract check verifies these raster gains.
export const previews: readonly RecipePreview[] = [{
  id: recipe640, side: 640, locked: true, transform_record: 'fixture-transforms-640-v1',
  source_id: 'shelf-fixture.svg', source_width: 1920, source_height: 1080,
  resized_width: 640, resized_height: 360, padding: [0, 140, 0, 140], sx: 1 / 3, sy: 1 / 3,
  forward: [1 / 3, 0, 0, 0, 1 / 3, 140, 0, 0, 1], inverse: [3, 0, 0, 0, 3, -420, 0, 0, 1],
  boxes: [
    { id: 'oat-1', class_name: 'Oat milk', xyxy: [212 / 3, 250 / 3 + 140, 352 / 3, 520 / 3 + 140], provenance: 'Reviewed by a person', clipped: false, flag: 'none', consequence: 'Retained' },
    { id: 'tag-1', class_name: 'Price tag', xyxy: [560 / 3, 530 / 3 + 140, 602 / 3, 550 / 3 + 140], provenance: 'Auto-accepted', clipped: false, flag: 'none', consequence: 'Retained: 14 × 6.67 px' },
    { id: 'edge-1', class_name: 'Soy milk', xyxy: [1846 / 3, 610 / 3 + 140, 640, 880 / 3 + 140], provenance: 'Reviewed by a person', clipped: true, flag: 'warn', consequence: 'Clipped at right image edge; retained visible extent only' },
  ], flags: { tiny: 412, below_minimum: 0, clipped: 61, excluded_images: 0 }, job: null, failures: [], manifest: 'variant-640.json', validation: 'validation-640.json',
}, {
  id: recipe320, side: 320, locked: true, transform_record: 'fixture-transforms-320-v1',
  source_id: 'shelf-fixture.svg', source_width: 1920, source_height: 1080,
  resized_width: 320, resized_height: 180, padding: [0, 70, 0, 70], sx: 1 / 6, sy: 1 / 6,
  forward: [1 / 6, 0, 0, 0, 1 / 6, 70, 0, 0, 1], inverse: [6, 0, 0, 0, 6, -420, 0, 0, 1],
  boxes: [
    { id: 'oat-1', class_name: 'Oat milk', xyxy: [212 / 6, 250 / 6 + 70, 352 / 6, 520 / 6 + 70], provenance: 'Reviewed by a person', clipped: false, flag: 'none', consequence: 'Retained' },
    { id: 'tag-1', class_name: 'Price tag', xyxy: [560 / 6, 530 / 6 + 70, 602 / 6, 550 / 6 + 70], provenance: 'Auto-accepted', clipped: false, flag: 'exclude', consequence: '7 × 3.33 px, below 4 px. Exclude this image from 320 unless recropped; never drop only the label.' },
    { id: 'edge-1', class_name: 'Soy milk', xyxy: [1846 / 6, 610 / 6 + 70, 320, 880 / 6 + 70], provenance: 'Reviewed by a person', clipped: true, flag: 'warn', consequence: 'Clipped at right image edge; retained visible extent only' },
  ], flags: { tiny: 3920, below_minimum: 488, clipped: 61, excluded_images: 212 }, job: null, failures: [], manifest: 'variant-320.json', validation: 'validation-320.json',
}];
const evaluation = (side: 320 | 640): EvaluationRecord => ({
  id: side === 640 ? 'eval-0044' : 'eval-0045', recipe_id: side === 640 ? recipe640 : recipe320,
  candidate_id: side === 640 ? 'm-0009' : 'm-0008', current_id: side === 640 ? 'm-0007' : 'm-0006',
  set_id: 't-2', set_role: 'locked_final', frames: 1114, objects: 1207, opened: 1,
  state: 'Completed', progress: 1114, conf: 0.25,
  overall: { map50_95: { current: 0.602, candidate: side === 640 ? 0.634 : 0.488 }, precision: { current: 0.917, candidate: 0.921 }, recall: { current: 0.855, candidate: side === 640 ? 0.874 : 0.795 } },
  classes: [
    { name: 'Oat milk 1 L', support: 312, minimum: 100, supported: true, metric: { current: 0.71, candidate: 0.74 } },
    { name: 'Soy milk 1 L', support: 266, minimum: 100, supported: true, metric: { current: 0.69, candidate: 0.68 } },
    { name: 'Almond milk 1 L', support: 69, minimum: 100, supported: false, reason: '69 test objects, gate needs 100' },
    { name: 'Price tag', support: 560, minimum: 100, supported: true, metric: { current: 0.38, candidate: side === 640 ? 0.47 : 0.32 } },
  ],
  slices: [
    { name: 'Targets under 16 px (source)', n: 488, unit: 'objects', current: '0.31', candidate: side === 640 ? '0.42' : '0.25' },
    { name: 'Targets at or below 6 px (derived)', n: 412, unit: 'objects', current: '0.30', candidate: '0.32' },
    { name: 'Clipped at edge', n: 61, unit: 'objects', current: '0.52', candidate: '0.55' },
    { name: 'Occluded', n: 140, unit: 'objects', current: '0.48', candidate: '0.47' },
    { name: 'Empty shelves (negatives)', n: 40, unit: 'frames', current: '2 false detections', candidate: '1 false detection' },
  ], lost_frames: side === 640 ? 0 : 212,
  gates: [{ name: 'No critical class drops more than 0.02', passed: true }, { name: 'Recall on small targets does not drop', passed: side === 640 }, { name: 'Export matches native within 0.005', passed: true }, { name: 'Every class has 100 test objects. Almond milk has 69.', passed: false }],
  // Delivered core requires support for every class; do not invent subset qualification.
  verdict: side === 640 ? 'Not qualified: Almond milk is unsupported. Keep the current model until the server qualifies a complete release.' : 'Does not qualify: recall on small targets drops 0.06, and Almond milk is unsupported.',
  qualified: false, diagnostics: 'Provider val mAP50-95 0.652 · diagnostic only; not promotion evidence',
});
export const fixtureSnapshot: ManagementSnapshot = {
  provenance: 'fixture', updated_at: fixtureTime, stale: false, paid_paused: false,
  draft: {
    id: 'draft-shelf', revision: 7, saved_at: '2026-11-07T01:20:00Z', published: false,
    scope: { name: 'Shelf audit', detect: 'Plant milk 1 L and shelf price tags', representation: 'scene_object', intended_use: 'Stock audit', specificity: 'product', permitted_use: 'Invented CC0 fixture scene', external_providers: '' },
    classes: [
      { id: 'oat', name: 'Oat milk 1 L', aliases: 'oat, oat drink', parent: 'Plant milk', examples: 12, exclusions: 'Barista oat 1 L', mirror_safe: false, provenance: 'Approved by an admin' },
      { id: 'soy', name: 'Soy milk 1 L', aliases: 'soya', parent: 'Plant milk', examples: 9, exclusions: 'Soy dessert', mirror_safe: false, provenance: 'Approved by an admin' },
      { id: 'almond', name: 'Almond milk 1 L', aliases: 'almond drink', parent: 'Plant milk', examples: 4, exclusions: 'Other carton sizes', mirror_safe: false, provenance: 'Proposed' },
      { id: 'tag', name: 'Price tag', aliases: 'shelf label', parent: '', examples: 6, exclusions: 'Promo stickers', mirror_safe: false, provenance: 'Approved by an admin' },
    ],
    dataset_import: { id: 'fixture-import-1', source_name: 'Invented catalogue.csv', rows: 16, state: 'Rejected', errors: [{ row: 14, field: 'name', message: 'Class name is empty. Enter a name before importing again.' }, { row: 16, field: 'parent', message: 'Parent class is unknown. Use an existing class ID.' }] },
    source_assets: [
      { id: 'source-0041', width: 1920, height: 1080, sha256: digest('1'), source_group: 'session-fixture-1', split: 'train', assignment_revision: 'v3' },
      { id: 'source-0042', width: 1920, height: 1080, sha256: digest('2'), source_group: 'session-fixture-2', split: null, assignment_revision: null },
    ],
    references: [{ id: 'ref-1', class_id: 'almond', source: 'Invented fixture scene', permitted_use: 'CC0 fixture', view: 'Front', lighting: 'Store', scale: 'Native', confused_with: 'soy', provenance: 'Proposed' }],
    annotation_guide: 'Use visible extents for plant milk cartons and shelf price tags.',
    guide: { extent: 'visible', truncation: 'Retain visible extent', occlusion: 'Do not estimate hidden extent', reflections: 'Out of scope', min_target_px: 4, exhaustive: true, background: 'Other packaging' },
    sources: { images: 1326, groups: 240, metadata_keys: 'session, sequence, subject', pending_pairs: [{ id: 'pair-1', left: 'source-0041', right: 'source-0042' }], split_summary: '192 train / 24 val / 24 test groups; derivatives inherit the source split' },
    approved_steps: ['Scope'], versions: { ontology: 'v1', guide: 'v1', references: 'v1', splits: 'v3', recipes: 'full-letterbox-640-v1, full-letterbox-320-v1', policy: 'P-2' },
  },
  release: {
    id: 'r0008', revision: 2, previous_id: 'r0007', state: 'Draft', images: 1326, objects: 10790, excluded: 23, complete: 1326, deltas: [86, 731, -4],
    provenance: { reviewed: 5020, auto_accepted: 3412, policy: 'P-2' },
    classes: [{ name: 'Oat milk 1 L', train: 2140, val: 312, test: 288, delta: 120, minimum: 100 }, { name: 'Soy milk 1 L', train: 1860, val: 266, test: 251, delta: 98, minimum: 100 }, { name: 'Almond milk 1 L', train: 512, val: 71, test: 69, delta: -14, minimum: 100 }, { name: 'Price tag', train: 3920, val: 560, test: 541, delta: 402, minimum: 100 }],
    changes: [{ image_id: 'source-0041', description: 'A person changed 2 labels; canonical revision 8 replaces revision 7' }],
    exclusions: [{ reason: 'Uncertain object waiting for adjudication', count: 11, image_ids: ['source-0042'] }, { reason: 'Target under 4 px at 320', count: 7, image_ids: ['source-0412'] }, { reason: 'Quarantined: new near-duplicate relation links training and test', count: 5, image_ids: ['source-0500'] }],
    failed_checks: [], identical: false, quarantine: null, manifest: 'fixture-release-r0008', manifest_sha256: digest('e'), frozen_at: null, frozen_by: null, progress: { done: 812, total: 1326 },
  }, recipes: previews,
  runs: [
    { id: 'run-0031', recipe_id: recipe640, dataset_release_id: 'r0007', state: 'Running', backend: 'local_ultralytics', epoch: 41, epochs: 100, reason: null, initialization_sha256: digest('f'), updated_at: fixtureTime, images: 1114,
      best_validation: { value: 0.612, epoch: 38, set: 'development-v3', n: 266 }, checkpoints: [{ epoch: 38, sha256: digest('a') }, { epoch: 41, sha256: digest('b') }], retention: { before: 12418, after: 12410, dropped: ['target-07', 'target-08'] }, mismatch: [], arguments: { imgsz: 640, rect: false, tile: false, nms: true, scale: 0, translate: 0, fliplr: 0 } },
    { id: 'run-0028', recipe_id: recipe320, dataset_release_id: 'r0007', state: 'Failed', backend: 'local_ultralytics', epoch: 0, epochs: 100, reason: 'Settings did not match; nothing will retry automatically.', initialization_sha256: digest('f'), updated_at: fixtureTime, images: 1114,
      best_validation: null, checkpoints: [], retention: { before: 0, after: 0, dropped: [] }, mismatch: [{ key: 'fliplr', requested: '0', resolved: '0.5' }, { key: 'scale', requested: '0', resolved: 'missing' }], arguments: { imgsz: 320, rect: false, tile: false, nms: true, scale: 0, translate: 0, fliplr: 0 } },
  ], evaluations: [evaluation(640), evaluation(320)],
  registry: [640, 320].map((size) => {
    const side = size as 320 | 640;
    return { project_id: 'shelf-fixture', recipe_id: side === 640 ? recipe640 : recipe320, revision: 4,
      champion: model(side, side === 640 ? 'm-0007' : 'm-0006'), candidate: model(side, side === 640 ? 'm-0009' : 'm-0008'), rollback: model(side, side === 640 ? 'm-0005' : 'm-0004'),
      qualification: 'unqualified' as const, reason: evaluation(side).verdict,
      evidence: { set: 't-2 (locked)', frames: 1114, objects: 1207 },
      history: [{ time: '2026-10-30T14:02:00Z', change: 'Promoted previous qualifying release', principal: 'Fixture admin' }],
    };
  }),
};
export function createFixturePort(snapshot: ManagementSnapshot = fixtureSnapshot): ManagementPort {
  return { read: (signal) => signal.aborted ? Promise.reject(new DOMException('Aborted', 'AbortError')) : Promise.resolve(structuredClone(snapshot)) };
}
