import { useState } from 'react';
import type { ModelExtensions, ModelRelease, RegistryRecord, ScreenProps } from './contracts.ts';
import { Button, CommandButton, Facts, Notice, PageHeading, ReadOnly, Table, Tile, Utc } from './shared.tsx';

export function ModelsView({ data, actions, extensions }: ScreenProps & { extensions?: ModelExtensions }) {
  const [pending, setPending] = useState<{ kind: 'promote' | 'rollback'; record: RegistryRecord } | null>(null);
  const [reason, setReason] = useState('');
  const firstQualified = data.registry.find((record) => record.candidate && record.qualification === 'qualified')?.recipe_id;
  const live = pending && data.registry.find((record) => record.recipe_id === pending.record.recipe_id);
  const changed = pending && (!live || live.revision !== pending.record.revision);
  const target = pending && (pending.kind === 'promote' ? pending.record.candidate : pending.record.rollback);
  async function confirm() {
    if (!pending || !target || changed) return;
    const common = { project_id: pending.record.project_id, recipe_id: pending.record.recipe_id, expected_revision: pending.record.revision, release_id: target.release_id };
    const ok = await actions.send(pending.kind === 'promote' ? { kind: 'promote', ...common } : { kind: 'rollback', ...common, reason: reason.trim() });
    if (ok) { setPending(null); setReason(''); }
  }
  return <>
    <PageHeading title="Models." gloss="One champion per recipe." lede="Promoting swaps the weights, preprocessing, classes and thresholds together. The previous model stays ready for rollback." /><ReadOnly role={actions.role} />
    <div className="stack">{data.registry.length === 0 && <Notice tone="info">No models yet. Train one from Runs.</Notice>}
      {data.registry.map((record) => <section className="card" key={record.recipe_id}><header><h2>{record.recipe_id}</h2><span className="label">Independent of the other recipe</span></header>
        <div className="grid2 even"><Tile value={record.champion?.release_id ?? 'No model yet'} label="In use"><p>{record.champion ? 'Proposes labels for new images' : <a href="?screen=runs">Train one from Runs.</a>}</p></Tile><Tile value={record.candidate?.release_id ?? 'No candidate'} label="Candidate"><span className={`tag ${record.qualification === 'qualified' ? 'ok' : record.qualification === 'unqualified' ? 'danger' : ''}`}>{record.qualification === 'qualified' ? 'Qualifies' : record.qualification === 'evaluating' ? 'Evaluating' : 'Not qualified'}</span><p>{record.reason}</p></Tile></div>
        <p className="small">Qualification evidence: {record.evidence.set} · n={record.evidence.frames} frames · {record.evidence.objects} objects. The server owns every gate.</p>
        {record.candidate && <ReleaseContract release={record.candidate} label="Candidate deployment contract" open={record.recipe_id === firstQualified} />}
        {record.champion && <ReleaseContract release={record.champion} label="In-use deployment contract" />}
        {record.candidate && !pending && <CommandButton actions={actions} primary={record.recipe_id === firstQualified} reason={record.qualification !== 'qualified' ? record.reason : undefined} onClick={() => setPending({ kind: 'promote', record: structuredClone(record) })}>Promote {record.candidate.release_id} for {record.recipe_id.includes('640') ? '640' : '320'}</CommandButton>}
        {record.qualification === 'unqualified' && <a href={`?screen=evaluation&recipe=${record.recipe_id}`}>See evaluation</a>}
        {extensions?.render?.(record.recipe_id)}
        <h3>History</h3><Table caption={`Promotions and rollbacks · ${record.recipe_id}`} columns={['When (UTC)', 'Recipe', 'Change', 'By', 'Actions']}>
          {record.history.map((event, index) => <tr key={`${event.time}:${index}`}><td><Utc value={event.time} /></td><td>{record.recipe_id}</td><td>{event.change}</td><td>{event.principal}</td><td>{index === 0 && record.rollback && <CommandButton actions={actions} onClick={() => { setPending({ kind: 'rollback', record: structuredClone(record) }); setReason(''); }}>Roll back {record.recipe_id.includes('640') ? '640' : '320'} to {record.rollback.release_id}</CommandButton>}</td></tr>)}
        </Table>
        {pending?.record.recipe_id === record.recipe_id && target && <section className="confirm"><h3>{pending.kind === 'promote' ? 'Promote' : 'Roll back to'} {target.release_id} for {target.policy.imgsz}?</h3><p>It replaces {pending.record.champion?.release_id ?? 'no current model'} for new proposals. The current release stays in history. The other recipe does not change.</p><ReleaseContract release={target} label="Exact contract to activate" open />
          {pending.kind === 'rollback' && <div className="field"><label htmlFor="rollback-reason">Reason for rollback</label><input id="rollback-reason" value={reason} onChange={(event) => setReason(event.currentTarget.value)} /></div>}
          {changed && <Notice tone="danger">The registry changed while confirmation was open. Cancel and inspect the new record before trying again.</Notice>}
          <div className="acts"><CommandButton actions={actions} primary reason={changed ? 'Registry revision changed. Reload the confirmation.' : pending.kind === 'rollback' && !reason.trim() ? 'Enter a reason for rollback.' : undefined} onClick={() => { void confirm(); }}>{pending.kind === 'promote' ? 'Confirm promotion' : 'Confirm rollback'}</CommandButton><Button onClick={() => setPending(null)}>Cancel</Button></div>
        </section>}
      </section>)}
    </div>
  </>;
}

function ReleaseContract({ release, label, open = false }: { release: ModelRelease; label: string; open?: boolean }) {
  return <details open={open}><summary>{label} · {release.release_id}</summary><Facts items={[
    ['Release', release.release_id], ['Project / recipe', `${release.project_id} / ${release.recipe_id}`],
    ['Weights SHA-256', release.weights_sha256], ['Preprocessing SHA-256', release.preprocessing_sha256],
    ['Class-map SHA-256', release.class_map_sha256], ['Classes', release.class_ids.join(', ')],
    ['Input', `${release.policy.imgsz} × ${release.policy.imgsz} · letterbox · static [${release.static_input_signature.join(', ')}]`],
    ['NMS', `${release.policy.nms} · IoU ${release.policy.iou} · max ${release.policy.max_det} · class-agnostic ${String(release.policy.agnostic_nms)}`],
    ['Confidence threshold', release.policy.conf], ['Tiling', release.tiling_mode], ['Export', release.export_format],
    ['Export SHA-256', release.export_sha256], ['Ultralytics / Torch', `${release.ultralytics_version} / ${release.torch_version}`],
    ['Evaluations', release.evaluation_run_ids.join(', ')],
  ]} /></details>;
}
