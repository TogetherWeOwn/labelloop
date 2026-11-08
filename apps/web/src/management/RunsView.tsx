import { useState } from 'react';
import type { ReactNode } from 'react';
import type { RecipeId, ScreenProps } from './contracts.ts';
import { Button, CommandButton, Download, Facts, Notice, PageHeading, ReadOnly, Table, Utc } from './shared.tsx';

export function RunsView({ data, actions, platform }: ScreenProps & { platform?: ReactNode }) {
  const [selectedId, setSelectedId] = useState(data.runs[0]?.id ?? '');
  const [starting, setStarting] = useState(false);
  const [recipe, setRecipe] = useState<RecipeId>(data.recipes[0]?.id ?? 'full-letterbox-640-v1');
  const [epochs, setEpochs] = useState(100);
  const [cancelling, setCancelling] = useState(false);
  const run = data.runs.find((item) => item.id === selectedId) ?? data.runs[0];
  const initialization = run?.initialization_sha256;
  const invalid = !data.release || data.release.state !== 'Frozen' ? 'Select a frozen release before starting a run.' : !initialization ? 'A verified base-checkpoint hash is required.' : !Number.isInteger(epochs) || epochs < 1 ? 'Epochs must be a positive whole number.' : undefined;
  return <>
    <PageHeading title="Training runs." gloss="One per recipe." lede="Local runs use the controlled backend. Settings, checkpoint hashes and target retention stay visible.">
      {actions.role === 'admin' && <Button primary={!starting} onClick={() => setStarting(!starting)} aria-expanded={starting} aria-controls="local-start">Start a run</Button>}
    </PageHeading><ReadOnly role={actions.role} />
    {starting && actions.role === 'admin' && <section id="local-start" className="confirm"><h2>Start run on this server</h2><div className="grid2 even"><div className="stack"><label htmlFor="local-recipe">Recipe</label><select id="local-recipe" value={recipe} onChange={(event) => setRecipe(event.currentTarget.value as RecipeId)}>{data.recipes.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}</select><label htmlFor="local-epochs">Epochs</label><input id="local-epochs" type="number" min="1" step="1" value={epochs} onChange={(event) => setEpochs(event.currentTarget.valueAsNumber)} /></div><Facts items={[[ 'Release', data.release?.id ?? 'None' ], ['Backend', 'local_ultralytics · CPU'], ['Base model', 'yolo26n · pinned checkpoint'], ['Initialization SHA-256', initialization ?? 'Unavailable'], ['Images', data.release?.images ?? 0], ['Account spend', 'None; host resource use is not an account charge']]} /></div>
      <p>One run per recipe. Input size comes from the recipe, not a second size field. Fixed-shape validation selects checkpoints; locked test data never selects an epoch.</p>
      <div className="acts"><CommandButton actions={actions} primary reason={invalid} onClick={() => { if (data.release && initialization) void actions.send({ kind: 'start_local_run', release_id: data.release.id, recipe_id: recipe, initialization_sha256: initialization, epochs }).then((ok) => { if (ok) setStarting(false); }); }}>Start run on this server</CommandButton><Button onClick={() => setStarting(false)}>Cancel</Button></div>
    </section>}
    {data.runs.length === 0 ? <Notice tone="info">No local runs yet. Freeze a release, then start a run for one recipe.</Notice> : <Table caption="Local training runs, newest first" columns={['Run', 'Recipe', 'Release', 'Backend', 'Status', 'Progress', 'Cost']}>
      {data.runs.map((item) => <tr key={item.id} aria-selected={item.id === run?.id}><th scope="row"><Button onClick={() => { setSelectedId(item.id); setCancelling(false); }}>{item.id}</Button></th><td className="mono">{item.recipe_id}</td><td>{item.dataset_release_id}</td><td>Local</td><td><span className={`tag ${item.state === 'Failed' ? 'danger' : item.state === 'Completed' ? 'ok' : ''}`}>{item.state}</span></td><td>{item.epoch} of {item.epochs} epochs</td><td>No account charge</td></tr>)}
    </Table>}
    {run && <div className="grid2"><section className="card"><h2>{run.id} · {run.state.toLowerCase()}</h2>{run.reason && <Notice tone={run.state === 'Failed' ? 'danger' : 'info'}>{run.reason}</Notice>}
      {run.mismatch.length > 0 && <><h3>Settings did not match</h3><Table caption="Requested and resolved arguments that differ" columns={['Setting', 'Asked for', 'Backend used']}>{run.mismatch.map((entry) => <tr key={entry.key}><th scope="row" className="mono">{entry.key}</th><td>{entry.requested}</td><td>{entry.resolved}</td></tr>)}</Table></>}
      <Facts items={[[ 'Epoch', `${run.epoch} of ${run.epochs}` ], ['Best fixed-shape validation', run.best_validation ? `${run.best_validation.value} · epoch ${run.best_validation.epoch} · ${run.best_validation.set} · n=${run.best_validation.n} frames` : 'Not available'], ['Checkpoints', `${run.checkpoints.length} recorded, hashed; local backend saves each epoch`], ['Targets kept by dataloader', `${run.retention.after} of ${run.retention.before}`], ['Updated', <Utc value={run.updated_at} />]]} />
      <details><summary>Checkpoint hashes and resolved settings</summary><Facts items={Object.entries(run.arguments)} /><Table caption="Recorded checkpoint hashes" columns={['Epoch', 'SHA-256']}>{run.checkpoints.map((checkpoint) => <tr key={checkpoint.epoch}><th scope="row">{checkpoint.epoch}</th><td className="mono">{checkpoint.sha256}</td></tr>)}</Table></details>
      {run.retention.before !== run.retention.after && <Notice tone="danger">Targets were dropped. This run cannot claim retention qualification until the backend has resolved the audit.</Notice>}
      <details><summary>See dropped targets</summary><p>{run.retention.dropped.join(', ') || 'None recorded'}</p></details>
      {actions.role === 'admin' && <div className="acts"><Button onClick={() => { setRecipe(run.recipe_id); setEpochs(run.epochs); setStarting(true); }}>Copy settings to a new run</Button>{(run.state === 'Running' || run.state === 'Queued') && <Button onClick={() => setCancelling(true)}>Cancel run</Button>}</div>}
      {cancelling && <section className="confirm"><h3>Cancel {run.id}?</h3><p>Completed checkpoints remain in history. This never retries automatically.</p><CommandButton actions={actions} onClick={() => { void actions.send({ kind: 'cancel_local_run', run_id: run.id }).then((ok) => { if (ok) setCancelling(false); }); }}>Confirm cancel run</CommandButton><Button onClick={() => setCancelling(false)}>Keep running</Button></section>}
      {run.state === 'Completed' && <div className="acts"><Download name={`${run.id}-record.json`} value={run}>Download run record</Download><a href={`?screen=evaluation&recipe=${run.recipe_id}`}>Evaluate</a></div>}
    </section><aside className="card"><h2>Running now</h2>{data.runs.filter((item) => item.state === 'Running').map((item) => <p key={item.id}>{item.id} · {item.epoch} of {item.epochs} epochs</p>)}<p className="label">Visible data refreshes every 30 s; no animation</p></aside></div>}
    {platform}
  </>;
}
