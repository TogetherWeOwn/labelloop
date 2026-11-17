import { useState } from 'react';
import type { RecipeId, ScreenProps } from './contracts.ts';
import { Button, CommandButton, Delta, Download, Notice, PageHeading, ReadOnly, Table, Tile } from './shared.tsx';

export function EvaluationView({ data, actions, initialRecipe }: ScreenProps & { initialRecipe?: RecipeId }) {
  const [recipeId, setRecipeId] = useState(initialRecipe ?? data.evaluations[0]?.recipe_id);
  const [reopen, setReopen] = useState(false);
  const record = data.evaluations.find((item) => item.recipe_id === recipeId) ?? data.evaluations[0];
  if (!record) return <><PageHeading title="Evaluation." gloss="Original-frame evidence." /><ReadOnly role={actions.role} /><Notice tone="info">No evaluation yet. Complete a local run, then evaluate its checkpoint on a frozen set.</Notice></>;
  const report = { ...record, classes: record.classes.map((entry) => entry.supported && entry.support >= entry.minimum ? entry : { name: entry.name, support: entry.support, minimum: entry.minimum, supported: false, reason: entry.supported ? `${entry.support} objects, gate needs ${entry.minimum}` : entry.reason }) };
  const setLabel = `${record.set_id} (${record.set_role === 'locked_final' ? 'locked final test' : 'development'}) · n=${record.frames} frames · ${record.objects} objects`;
  return <>
    <PageHeading title="Evaluation." gloss={`Candidate against current, ${record.recipe_id.includes('640') ? '640' : '320'}.`} meta={<>{record.id} · {setLabel}</>} lede="Scored on the original frames: predictions are inverse-mapped through the recorded recipe and merged before scoring. Provider val is diagnostic only.">
      <Download name={`${record.id}-report.json`} value={report}>Download {data.provenance === 'fixture' ? 'fixture ' : ''}report</Download>
      {actions.role === 'admin' && <a className="pill primary" href={`?screen=models&recipe=${record.recipe_id}&candidate=${record.candidate_id}`}>Go to promotion</a>}
    </PageHeading><ReadOnly role={actions.role} />
    <div className="seg" role="group" aria-label="Evaluation recipe">{data.evaluations.map((item) => <Button key={item.id} aria-pressed={record.recipe_id === item.recipe_id} onClick={() => { setRecipeId(item.recipe_id); setReopen(false); }}>{item.recipe_id.includes('640') ? '640' : '320'}</Button>)}</div>
    {record.state === 'Running' ? <Notice tone="info">Running · {record.progress} of {record.frames} frames. Metrics are not ready.</Notice> : record.state !== 'Completed' ? <Notice tone="danger">{record.state}. No qualified evaluation is available.</Notice> : <>
      <p className="label">Every metric below: {setLabel} · confidence {record.conf}</p>
      <div className="tiles">{Object.entries(record.overall).map(([name, metric]) => <Tile key={name} value={metric.candidate} label={name === 'map50_95' ? 'mAP50–95' : name === 'precision' ? `Precision at conf ${record.conf}` : 'Recall'}><Delta {...metric} /></Tile>)}<Tile value={`${record.gates.filter((gate) => gate.passed).length} of ${record.gates.length}`} label="gates passed" /></div>
      <div className="grid2"><div className="stack"><section className="card"><h2>mAP50–95 per class</h2><p className="small">{setLabel}</p><p className="keyrow">Current {record.current_id ?? 'none (first model)'} · Candidate {record.candidate_id}</p>
        <div className="bars" aria-hidden="true">{record.classes.map((entry) => <div className="bar-row" key={entry.name}><span>{entry.name}</span>{entry.supported && entry.support >= entry.minimum ? <><span className="bar-pair" title={`Current ${entry.metric.current ?? 'first model'}, candidate ${entry.metric.candidate}`}><span className="inc" style={{ width: `${(entry.metric.current ?? 0) * 100}%` }} /><span className="cand" style={{ width: `${entry.metric.candidate * 100}%` }} /></span><span className="mono">{entry.metric.candidate.toFixed(2)}</span></> : <span className="unsupported">Unsupported: {entry.supported ? `${entry.support} test objects, gate needs ${entry.minimum}` : entry.reason}</span>}</div>)}</div>
        <ul className="sr">{record.classes.map((entry) => <li key={entry.name}>{entry.name}: {entry.supported && entry.support >= entry.minimum ? `current ${entry.metric.current ?? 'first model'}, candidate ${entry.metric.candidate}, ${entry.support} objects` : `unsupported, ${entry.support} objects; gate needs ${entry.minimum}`} · {setLabel}</li>)}</ul>
        <details><summary>Show as a table</summary><Table caption={`mAP50–95 per class · ${setLabel}`} columns={['Class', 'Support', 'Current', 'Candidate']}>
          {record.classes.map((entry) => <tr key={entry.name}><th scope="row">{entry.name}</th><td>{entry.support} objects</td>{entry.supported && entry.support >= entry.minimum ? <><td>{entry.metric.current ?? 'First model'}</td><td>{entry.metric.candidate}</td></> : <td colSpan={2}>Unsupported: {entry.supported ? `${entry.support} objects, gate needs ${entry.minimum}` : entry.reason}</td>}</tr>)}
        </Table></details>
      </section><section className="card"><h2>Hard conditions</h2><Table caption={`Recall and negative-scene counts · ${setLabel}`} columns={['Slice', 'Support', 'Current', 'Candidate']}>
        {record.slices.map((slice) => <tr key={slice.name}><th scope="row">{slice.name}</th><td>{slice.n} {slice.unit}</td><td>{slice.current}</td><td>{slice.candidate}</td></tr>)}
      </Table><p>Coverage lost to cropping or exclusion: {record.lost_frames} of {record.frames} frames.</p></section></div>
      <section className="card"><h2>Gates for {record.recipe_id}</h2><ul className="checks">{record.gates.map((gate) => <li key={gate.name}><span className={`tag ${gate.passed ? 'ok' : 'danger'}`}>{gate.passed ? '✓ Pass' : '× Fail'}</span> {gate.name}</li>)}</ul><Notice tone={record.qualified ? 'ok' : 'warn'}>{record.verdict}</Notice><p>Each recipe qualifies independently. A 640 pass says nothing about 320.</p>
        <p className="label">{record.set_role === 'locked_final' ? `Locked test set ${record.set_id} was opened ${record.opened} time(s) for this candidate.` : 'Development evaluation; not untouched final evidence.'}</p>
        {record.set_role === 'locked_final' && actions.role === 'admin' && <Button onClick={() => setReopen(true)}>Open {record.set_id} again</Button>}
        {reopen && <section className="confirm"><h3>Opening {record.set_id} again turns it into development data for later runs.</h3><p>The server records this for the entire lineage. No examples can be removed to make it untouched again.</p><CommandButton actions={actions} onClick={() => { void actions.send({ kind: 'open_locked_test', evaluation_id: record.id, acknowledge_development_reuse: true }).then((ok) => { if (ok) setReopen(false); }); }}>Confirm open as development data</CommandButton><Button onClick={() => setReopen(false)}>Cancel</Button></section>}
      </section></div>
      <details className="card"><summary>Show provider diagnostics</summary><p>Diagnostic only · {setLabel}. {record.diagnostics}</p></details>
    </>}
  </>;
}
