import type { ModelRelease, RecipeOperationsRecord, Role, ViewState } from './contracts.ts';
import { Facts, Notice, Utc, ViewBoundary } from './shared.tsx';

export function RecipeOperations({ state, role }: { state: ViewState<readonly RecipeOperationsRecord[]>; role: Role }) {
  return <section className="operations"><header className="phead"><div><h1>Models. <span>One champion per recipe.</span></h1><p>Canary, drift and rollback evidence. Each recipe is independent.</p>
    <p>{role === 'reviewer' ? 'Read-only. Only an admin can promote or roll back.' : 'Operational evidence only. Promotion and rollback commands belong to the model registry.'}</p></div></header>
    <ViewBoundary state={state}>{(records) => records.length === 0 ? <section className="card"><h2>No models yet</h2><p>Train one from Runs.</p></section> : <div className="stack">{records.map((record) => <RecipeCard key={`${record.project_id}:${record.recipe_id}`} record={record} />)}</div>}</ViewBoundary>
  </section>;
}

function RecipeCard({ record }: { record: RecipeOperationsRecord }) {
  const candidate = record.candidate;
  const canary = record.canary;
  const canaryValid = canary !== null && canary.planned_fresh_groups.length > 0
    && new Set(canary.planned_fresh_groups).size === canary.planned_fresh_groups.length
    && new Set(canary.completed_fresh_groups).size === canary.completed_fresh_groups.length
    && canary.completed_fresh_groups.every((group) => canary.planned_fresh_groups.includes(group))
    && canary.planned_fresh_groups.every((group) => !canary.excluded_training_groups.includes(group));
  return <section className="card"><header><h2>{record.recipe_id}</h2><span className="label">Independent of the other recipe</span></header>
    <div className="recipe-tiles"><div className="tile"><span className="label">In use</span><strong className="value mono">{record.champion?.release_id ?? 'No model yet'}</strong><p>{record.champion ? 'Proposes labels for new images' : 'Train one from Runs.'}</p></div>
      <div className="tile"><span className="label">Candidate</span><strong className="value mono">{candidate?.release_id ?? 'No candidate'}</strong>{candidate && <><span className={`tag ${record.qualification.state === 'qualified' ? 'ok' : record.qualification.state === 'unqualified' ? 'danger' : ''}`}>{record.qualification.state === 'qualified' ? 'Qualifies' : record.qualification.state === 'evaluating' ? 'Evaluating' : 'Not qualified'}</span><p>{record.qualification.reason}</p></>}</div></div>
    {canary && (canaryValid ? <><span className="tag">Canary · {canary.completed_fresh_groups.length} of {canary.planned_fresh_groups.length} groups</span><Facts items={[
      ['Fresh source groups', canary.planned_fresh_groups.join(', ')], ['Completed groups', canary.completed_fresh_groups.join(', ') || 'None yet'],
      ['Training groups excluded', canary.excluded_training_groups.join(', ')], ['Canary evidence', canary.evidence_id],
    ]} /></> : <Notice>Fresh source-group evidence is inconsistent. Canary progress is unavailable; no enablement is implied.</Notice>)}
    {record.drift.map((pause) => <Notice key={`${pause.scope}:${pause.id}`}><p>Auto-accept is paused for {pause.label} ({pause.scope}: {pause.id}): {pause.reason}</p><p>Continue collecting and reviewing evidence. Resume only after a new qualified release.</p><a href={`/audit-samples/${encodeURIComponent(pause.audit_sample_id)}`}>Audit sample {pause.audit_sample_id}</a></Notice>)}
    {candidate && <ReleaseContract release={candidate} label="Candidate deployment contract" open />}
    {record.champion && <ReleaseContract release={record.champion} label="In-use deployment contract" />}
    <section className="stack"><h3>Qualification evidence</h3>{record.qualification.evidence.length === 0 ? <p>Evidence is pending. No qualification is established.</p> : record.qualification.evidence.map((evidence) => <section className="stack" key={evidence.id}><h4>{evidence.id}</h4><Facts items={[
      ['Set', `${evidence.set_version} · ${evidence.set_role}`], ['Sample size', `${evidence.source_groups} source groups · ${evidence.images} images · ${evidence.objects} objects`],
      ['Protocol', evidence.protocol], ['Pipeline', evidence.pipeline_id],
    ]} /><ul>{evidence.classes.map((entry) => <li key={entry.id}><strong>{entry.id}</strong> · {entry.support} ground-truth objects · {entry.supported ? <>Supported · {entry.result}</> : <>Unsupported · {entry.reason}</>}</li>)}</ul></section>)}</section>
    <section className="stack"><h3>Rollback</h3>{record.rollback ? <><Facts items={[
      ['Known-good release', record.rollback.release.release_id], ['Replaced release', record.rollback.previous_release_id],
      ['Promotion time', <Utc value={record.rollback.promoted_at} />], ['Authenticated principal', record.rollback.principal],
      ['Reason', record.rollback.reason], ['Gate results', record.rollback.gate_results.join('; ')],
      ['Recipe revision', record.revision],
    ]} /><ReleaseContract release={record.rollback.release} label="Complete rollback contract" /></> : <p>No known-good rollback release is recorded.</p>}</section>
  </section>;
}

function ReleaseContract({ release, label, open = false }: { release: ModelRelease; label: string; open?: boolean }) {
  return <details open={open}><summary>{label} · {release.release_id}</summary><Facts items={[
    ['Release', release.release_id], ['Project', release.project_id], ['Recipe', release.recipe_id],
    ['Weights SHA-256', release.weights_sha256], ['Preprocessing SHA-256', release.preprocessing_sha256],
    ['Class-map SHA-256', release.class_map_sha256], ['Classes', release.class_ids.join(', ')],
    ['Input', `${release.policy.imgsz} × ${release.policy.imgsz} · static [${release.static_input_signature.join(', ')}]`],
    ['NMS', `${release.policy.nms} · IoU ${release.policy.iou} · max ${release.policy.max_det} · class-agnostic ${String(release.policy.agnostic_nms)}`],
    ['Confidence threshold', release.policy.conf], ['Tiling', release.tiling_mode],
    ['Export', release.export_format], ['Export SHA-256', release.export_sha256],
    ['Ultralytics', release.ultralytics_version], ['Torch', release.torch_version],
    ['Evaluations', release.evaluation_run_ids.join(', ')],
  ]} /></details>;
}
