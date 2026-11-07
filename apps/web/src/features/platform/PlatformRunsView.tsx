import { useState } from 'react';
import { exceededLimits, statusCopy, usd } from './contracts.ts';
import type { DryRun, PlatformSnapshot, ViewerRole } from './contracts.ts';

function DryRunPanel({ draft, paused, close }: { draft: DryRun; paused: boolean; close: () => void }) {
  const limits = exceededLimits(draft);
  return <section id="platform-start" className="confirm" aria-labelledby="start-heading">
    <h2 id="start-heading">Start run on Ultralytics Platform</h2>
    <div className="grid2 even"><dl className="facts">
      <dt>Release</dt><dd>{draft.release}</dd><dt>Recipe</dt><dd>{draft.recipe}</dd>
      <dt>Backend</dt><dd>Platform</dd><dt>Base model</dt><dd>{draft.baseModel} · sha256 <code>{draft.baseSha256}</code></dd>
      <dt>GPU</dt><dd>{draft.gpu} · {draft.gpuAvailable ? 'Available in fixture' : 'Unavailable'}</dd>
      <dt>Epochs</dt><dd>{draft.epochs}</dd><dt>Images</dt><dd>{draft.images}</dd>
    </dl><div className="stack">
      <p><span className="tag warn">Estimate</span> {usd(draft.estimateUsd)}</p>
      <p className="small t2">Price basis: {usd(draft.hourlyUsd)}/h. Billed for time used, even if cancelled; at least 15 minutes.</p>
      <p className="small t2">Month total after this run: {usd(draft.monthlyUsedAndReservedUsd + draft.estimateUsd)} of {usd(draft.monthlyLimitUsd)}.</p>
      <p className="small t2">The run won't start if its estimate is over {usd(draft.operationLimitUsd)} per operation, or if this month's spend, plus estimates reserved for queued work, plus this estimate would pass the {usd(draft.monthlyLimitUsd)} monthly limit. The per-iteration limit is {usd(draft.iterationLimitUsd)}; used and reserved this iteration: {usd(draft.iterationUsedAndReservedUsd)}. Time-based billing can come in above the estimate; the actual shows as unconfirmed until the bill is matched to this run.</p>
    </div></div>
    {limits.length > 0 && <p className="notice danger">Cannot start: {limits.join(', ')}. Review limits in Costs.</p>}
    {paused && <p className="notice danger">Paid steps are paused. No start or retry is available.</p>}
    {!draft.gpuAvailable && <p className="notice warn">The selected GPU is unavailable. Nothing will start.</p>}
    <p className="help">Authenticated approval, a ledger estimate and all server-enforced limits are required. This preview cannot authorize spend.</p>
    <div className="acts">{!limits.length && !paused && draft.gpuAvailable && <button className="pill" disabled>Start · estimate {usd(draft.estimateUsd)}</button>}
      <button className="pill ghost" onClick={close}>Cancel</button></div>
  </section>;
}

export function PlatformRunsView({ role, snapshot }: { role: ViewerRole; snapshot: PlatformSnapshot }) {
  const [showDraft, setShowDraft] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = snapshot.runs.find((run) => run.id === selectedId) ?? snapshot.runs[0];
  return <>
    <div className="phead"><div><h1>Training runs. <span className="g">One per recipe.</span></h1>
      {role !== 'admin' && <p className="lede">Read only. Only an admin can start or cancel runs.</p>}</div>
      {role === 'admin' && <button className="pill ghost" disabled={!snapshot.dryRun} aria-expanded={showDraft} aria-controls="platform-start" onClick={() => setShowDraft(!showDraft)}>Start a run</button>}
    </div>
    {role === 'admin' && showDraft && snapshot.dryRun && <DryRunPanel draft={snapshot.dryRun} paused={snapshot.paidStepsPaused} close={() => setShowDraft(false)} />}
    {snapshot.runs.length === 0 ? <p className="notice">No Platform runs yet. An admin can inspect a dry-run summary before starting a run after integration.</p> : <>
      <div className="tablewrap" tabIndex={0} role="region" aria-label="Training runs, scrolls sideways"><table><caption className="sr">Platform training runs</caption>
        <thead><tr>{['Run', 'Recipe', 'Release', 'Backend', 'Status', 'Progress', 'Cost'].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
        <tbody>{snapshot.runs.map((run) => <tr key={run.id} className={selected?.id === run.id ? 'hl' : ''}>
          <th scope="row"><button className="linkbtn" aria-pressed={selected?.id === run.id} onClick={() => setSelectedId(run.id)}>{run.id}</button></th>
          <td className="mono">{run.recipe}</td><td>{run.release}</td><td>Platform</td>
          <td><span className={`tag ${statusCopy[run.status].tone}`}>{statusCopy[run.status].label}</span></td>
          <td>{run.epoch} of {run.epochs} epochs</td><td>{run.actualUsd === null ? <>Estimate {usd(run.estimateUsd)} · Actual unconfirmed</> : <>Actual {usd(run.actualUsd)} · Estimate {usd(run.estimateUsd)}</>}</td>
        </tr>)}</tbody></table></div>
      {selected && <div className="grid2"><section className="card" aria-labelledby="run-heading">
        <header><h2 id="run-heading">{selected.id}</h2><span className={`tag ${statusCopy[selected.status].tone}`}>{statusCopy[selected.status].label}</span></header>
        <p className={`notice ${statusCopy[selected.status].tone}`}>{statusCopy[selected.status].reason}</p>
        <p className="small t2">Status is read from the operation record, never inferred from a click. This is a deterministic fixture record, not a live server operation.</p>
        <dl className="facts"><dt>Operation</dt><dd>{selected.operationId}</dd><dt>Updated (UTC)</dt><dd><time dateTime={selected.updatedAt}>{selected.updatedAt}</time></dd>
          <dt>Checkpoint</dt><dd>{selected.checkpointSha256 ? <>best.pt · sha256 <code>{selected.checkpointSha256}</code></> : 'Not downloaded'}</dd>
          <dt>Selection</dt><dd>Selected under provider rect validation</dd><dt>Targets kept</dt><dd>{selected.targets ? `${selected.targets.kept} of ${selected.targets.total}` : 'Not reported; retention unconfirmed'}</dd>
        </dl>
        {selected.mismatches.length > 0 && <div className="tablewrap" tabIndex={0} role="region" aria-label="Settings mismatch, scrolls sideways"><table><caption>Settings check · mismatch fails the run</caption>
          <thead><tr><th scope="col">Setting</th><th scope="col">Asked for</th><th scope="col">Platform used</th></tr></thead><tbody>{selected.mismatches.map((mismatch) => <tr key={mismatch.key}>
            <th scope="row" className="mono">{mismatch.key}</th><td>{mismatch.requested}</td><td>{mismatch.resolved ?? 'Missing'}</td>
          </tr>)}</tbody></table></div>}
        {role === 'admin' && <div className="acts"><button className="pill danger" disabled>Cancel run</button><button className="pill ghost" disabled>Copy settings to a new run</button></div>}
        <p className="help">You're billed for the time used so far. Cancellation, downloads, evaluation and Platform links are unavailable in the fixture preview.</p>
      </section><aside className="card" aria-label="Running now"><h2>What's running now</h2>
        {snapshot.runs.filter((run) => run.status === 'running' || run.status === 'queued').map((run) => <p key={run.id}>{run.id} · {statusCopy[run.status].label} · {run.epoch} of {run.epochs}</p>)}
        {!snapshot.runs.some((run) => run.status === 'running' || run.status === 'queued') && <p>Nothing is running.</p>}
      </aside></div>}
    </>}
  </>;
}
