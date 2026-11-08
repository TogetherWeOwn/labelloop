import { useId, useState } from 'react';
import type { BudgetPolicy, CostCommands, CostScope, CostsRecord, OperationKind, Role, ViewState } from './contracts.ts';
import { actualCostLabel, download, ledgerCsv, parseLimit, policyComplete, scopedEntries, totals, usd } from './records.ts';
import { Facts, Notice, PaidPause, TableRegion, Utc, ViewBoundary } from './shared.tsx';

const kinds: readonly OperationKind[] = ['Training', 'LLM review', 'Auto-annotation', 'Predict'];

export function CostsView({ state, readId, role, scope, commands, onRefresh }: {
  // Advance only after a successful authoritative read, even if revision is unchanged.
  state: ViewState<CostsRecord>; readId: string | number; role: Role; scope: CostScope; commands?: CostCommands; onRefresh: () => void;
}) {
  return <section className="operations"><header className="phead"><div><h1>Costs. <span>Nothing paid runs without an estimate.</span></h1>
    {role === 'reviewer' && <p>Read-only. Only an admin can manage spending policy.</p>}</div></header>
    <ViewBoundary state={state}>{(record, stale) => <CostsContent key={JSON.stringify([record.project_id, record.revision, readId])} record={record} role={role} scope={scope} stale={stale} commands={commands} onRefresh={onRefresh} />}</ViewBoundary>
  </section>;
}

function BudgetMeter({ label, used, limit }: { label: string; used: number; limit: number | null }) {
  const exhausted = limit !== null && used >= limit;
  const percent = limit === null ? null : limit === 0 ? 100 : used / limit * 100;
  const tone = exhausted ? 'danger' : percent !== null && percent > 80 ? 'warn' : '';
  return <div className="tile"><span className="label">{label}</span><strong className="value">{usd(used)}{limit !== null && <> / {usd(limit)}</>}</strong>
    {percent !== null ? <><div className={`meter ${tone}`} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(percent, 100)} aria-valuetext={`${percent}% of limit; ${usd(used)} against ${usd(limit ?? 0)}`}><span style={{ width: `${Math.min(percent, 100)}%` }} /></div>
      {percent > 80 && <p>{exhausted ? 'At or above the limit.' : `You've used ${percent}% of this limit.`}</p>}</> : <p>Limit not set · paid steps unavailable.</p>}</div>;
}

function CostsContent({ record, role, scope, stale, commands, onRefresh }: {
  record: CostsRecord; role: Role; scope: CostScope; stale: boolean; commands?: CostCommands; onRefresh: () => void;
}) {
  const [kind, setKind] = useState<OperationKind | null>(null);
  const monthly = record.entries.filter((entry) => entry.project_id === record.project_id && entry.month === record.month);
  const month = totals(monthly);
  const iteration = totals(record.entries.filter((entry) => entry.project_id === record.project_id && entry.iteration_id === record.iteration_id));
  const entries = scopedEntries(record, scope).filter((entry) => kind === null || entry.kind === kind);
  return <div className="stack"><p className="label">{record.month} · USD · prices {record.price_version} · {scope.kind}: {scope.id}</p>
    <PaidPause reason={record.pause_reason} />
    <div className="tiles">
      <BudgetMeter label="Spent and committed this month" used={month.exposure} limit={record.policy.monthly_limit} />
      <div className="tile"><span className="label">Reserved for queued work</span><strong className="value">{usd(month.reserved)}</strong><span className="tag warn">Estimate</span><p>{usd(month.actual)} actual · {usd(month.unconfirmed)} unconfirmed, still counted at estimate.</p></div>
      <BudgetMeter label={`This iteration · ${record.iteration_id}`} used={iteration.exposure} limit={record.policy.iteration_limit} />
      <div className="tile"><span className="label">Actual against estimate</span><strong className="value">{usd(month.delta)}</strong><p>This month · confirmed charges only.</p></div>
    </div>
    <div className="cost-layout">
      <aside className="card policy" aria-label="Budget and policy"><h2>Spending policy</h2>
        <p>Limits are checked against estimates, not caps on actual billing. Actual billing can come in above an estimate.</p>
        {role === 'admin' ? <PolicyEditor record={record} stale={stale} commands={commands} onRefresh={onRefresh} /> : <Facts items={[
          ['Spend without asking', record.policy.autonomous_spend ? 'On' : 'Off'],
          ['Monthly limit', record.policy.monthly_limit === null ? 'Not set' : usd(record.policy.monthly_limit)],
          ['Per iteration', record.policy.iteration_limit === null ? 'Not set' : usd(record.policy.iteration_limit)],
          ['Per operation', record.policy.operation_limit === null ? 'Not set' : usd(record.policy.operation_limit)],
        ]} />}
      </aside>
      <section className="card ledger" aria-labelledby="ledger-heading"><header><h2 id="ledger-heading">Ledger</h2><button className="linkbtn" onClick={() => download(new Blob([ledgerCsv(entries)], { type: 'text/csv;charset=utf-8' }), 'costs.csv')}>Download CSV</button></header>
        <div className="chips" role="group" aria-label="Operation kind"><button className="chip" aria-pressed={kind === null} onClick={() => setKind(null)}>All</button>{kinds.map((item) => <button className="chip" key={item} aria-pressed={kind === item} onClick={() => setKind(item)}>{item}</button>)}</div>
        {entries.length === 0 ? <p>{record.entries.length === 0 ? 'Nothing paid has run yet. Local training and review are free.' : 'No operations match this scope and filter.'}</p> : <TableRegion label="Paid operation ledger"><table><caption>Every paid operation, including refused ones · newest first</caption><thead><tr>{['When (UTC)', 'Operation', 'Approved by', 'Estimate', 'Actual', 'Result'].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead><tbody>{entries.map((entry) => <tr key={entry.operation_id}>
          <td><Utc value={entry.at} /></td><th scope="row">{entry.kind} · {entry.object_id}{entry.recipe_id && <> · {entry.recipe_id}</>}<span className="secondary">{entry.operation_id} · {entry.iteration_id}</span></th>
          <td>{entry.state === 'refused' ? <span className="tag danger">Refused</span> : entry.approved_by ?? 'Approval unavailable'}</td>
          <td>{usd(entry.estimate)} <span className="tag warn">Estimate</span></td><td>{actualCostLabel(entry)}</td><td>{entry.result}</td>
        </tr>)}</tbody></table></TableRegion>}
      </section>
    </div>
  </div>;
}

function PolicyEditor({ record, stale, commands, onRefresh }: { record: CostsRecord; stale: boolean; commands?: CostCommands; onRefresh: () => void }) {
  const id = useId();
  const initial = (amount: number | null) => amount === null ? '' : String(amount / 1_000_000);
  const [monthly, setMonthly] = useState(initial(record.policy.monthly_limit));
  const [iteration, setIteration] = useState(initial(record.policy.iteration_limit));
  const [operation, setOperation] = useState(initial(record.policy.operation_limit));
  const [autonomous, setAutonomous] = useState(record.policy.autonomous_spend);
  const [confirm, setConfirm] = useState<BudgetPolicy | null>(null);
  const [pending, setPending] = useState(false);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [message, setMessage] = useState('');
  const disabled = stale || pending || needsRefresh || commands === undefined;
  const execute = async (action: () => Promise<void>) => {
    if (disabled) return;
    setPending(true);
    try { await action(); setMessage('Saved. Refreshing the authoritative record.'); setNeedsRefresh(true); setConfirm(null); onRefresh(); }
    catch { setMessage('The change was not confirmed. Refresh records before trying again.'); setNeedsRefresh(true); }
    finally { setPending(false); }
  };
  const draft = (): BudgetPolicy => ({ monthly_limit: parseLimit(monthly), iteration_limit: parseLimit(iteration), operation_limit: parseLimit(operation), autonomous_spend: autonomous });
  return <form className="stack" onSubmit={(event) => {
    event.preventDefault();
    if (disabled || !commands) return;
    try {
      const policy = draft();
      if (!policyComplete(policy)) { setMessage('Set monthly, per-iteration and per-operation limits first.'); return; }
      if (policy.autonomous_spend) setConfirm(policy);
      else void execute(() => commands.savePolicy(policy, record.revision));
    } catch { setMessage('Enter nonnegative USD limits with up to six decimal places.'); }
  }}>
    {!commands && <p>Read-only fixture. Authenticated spending commands are not connected.</p>}
    <div className="toggle"><span>Spend without asking</span><button type="button" className="switch" role="switch" aria-label="Spend without asking" aria-checked={autonomous} disabled={disabled} onClick={() => { setConfirm(null); setAutonomous(!autonomous); }} aria-describedby={`${id}-why`} /><p id={`${id}-why`}>Off: every paid step waits for a person to approve its estimate. On: steps run inside the limits below, and each one still writes its estimate first.</p></div>
    {([['Monthly limit (USD)', monthly, setMonthly], ['Per iteration (USD)', iteration, setIteration], ['Per operation (USD)', operation, setOperation]] as const).map(([label, value, change], index) => <div className="field" key={label}><label htmlFor={`${id}-${index}`}>{label}</label><input id={`${id}-${index}`} className="input" inputMode="decimal" value={value} disabled={disabled} onChange={(event) => { setConfirm(null); change(event.target.value); }} /></div>)}
    {!policyComplete(record.policy) && <p>Set a monthly limit first. Paid steps are unavailable until all three limits are set.</p>}
    <p>An operation over any limit is refused, never queued.</p>
    <button className="pill" disabled={disabled}>Save limits</button>
    {confirm && <section className="confirm" aria-label="Confirm autonomous spending"><p>labelloop starts paid steps without asking while each estimate is {usd(confirm.operation_limit ?? 0)} or less, each iteration stays within {usd(confirm.iteration_limit ?? 0)}, and this month’s spend, plus estimates reserved for queued work, plus the new estimate stays within {usd(confirm.monthly_limit ?? 0)}. Actual billing can come in above an estimate.</p><button type="button" className="pill ghost" disabled={disabled} onClick={() => { if (commands) void execute(() => commands.savePolicy(confirm, record.revision)); }}>Turn on</button><button type="button" className="pill ghost" onClick={() => setConfirm(null)}>Cancel</button></section>}
    {record.pause_reason !== null && <button type="button" className="pill ghost" disabled={disabled} onClick={() => { if (commands) void execute(() => commands.resumePaid(record.revision)); }}>Resume paid steps</button>}
    <p role="status">{message}</p>{needsRefresh && <Notice><button type="button" className="pill ghost" onClick={onRefresh}>Refresh records</button></Notice>}
  </form>;
}
