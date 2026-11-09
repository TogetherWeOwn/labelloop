import { useId, useState } from 'react';
import type { BudgetPolicy, CostCommands, CostScope, CostsRecord, OperationKind, Role, ViewState } from './contracts.ts';
import { actualCostLabel, approvalLabel, download, ledgerCsv, parseLimit, policyComplete, scopedEntries, totals, usd } from './records.ts';
import { Facts, Notice, PaidPause, TableRegion, Utc, ViewBoundary } from './shared.tsx';

const kinds: readonly OperationKind[] = ['Training', 'LLM review', 'Auto-annotation', 'Predict'];

type CostPaging = { pending: boolean; failed: boolean; onMore: () => void; onRetry: () => void };

export function CostsView({ state, readId, role, scope, commands, onRefresh, paging }: {
  // Advance only after a successful authoritative read, even if revision is unchanged.
  state: ViewState<CostsRecord>; readId: string | number; role: Role; scope: CostScope; commands?: CostCommands; onRefresh: () => void; paging?: CostPaging;
}) {
  return <section className="operations"><header className="phead"><div><h1>Costs. <span>Nothing paid runs without an estimate.</span></h1>
    {role === 'reviewer' && <p>Read-only. Only an admin can manage spending policy.</p>}</div></header>
    <ViewBoundary state={state}>{(record, stale) => <CostsContent key={JSON.stringify([record.project_id, record.revision, role, record.source])} record={record} readId={readId} role={role} scope={scope} stale={stale} commands={record.source === 'session' ? undefined : commands} onRefresh={onRefresh} paging={paging} />}</ViewBoundary>
  </section>;
}

function BudgetMeter({ label, used, limit }: { label: string; used: number; limit: number | null }) {
  const exhausted = limit !== null && used >= limit;
  const percent = limit === null ? null : limit === 0 ? 100 : used / limit * 100;
  // Truncate exact integer tenths before conversion; floating division can turn 29 into 28.9.
  const displayPercent = limit === null ? null : limit === 0 ? 100 : Number(BigInt(used) * 1000n / BigInt(limit)) / 10;
  const percentLabel = displayPercent === null ? '' : new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 }).format(displayPercent);
  const tone = exhausted ? 'danger' : percent !== null && percent > 80 ? 'warn' : '';
  return <div className="tile"><span className="label">{label}</span><strong className="value">{usd(used)}{limit !== null && <> / {usd(limit)}</>}</strong>
    {percent !== null ? <><div className={`meter ${tone}`} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(percent, 100)} aria-valuetext={`${percentLabel}% of limit; ${usd(used)} against ${usd(limit ?? 0)}`}><span style={{ width: `${Math.min(percent, 100)}%` }} /></div>
      {percent > 80 && <p>{exhausted ? 'At or above the limit.' : `You've used ${percentLabel}% of this limit.`}</p>}</> : <p>Limit not set · paid steps unavailable.</p>}</div>;
}

function CostsContent({ record, readId, role, scope, stale, commands, onRefresh, paging }: {
  record: CostsRecord; readId: string | number; role: Role; scope: CostScope; stale: boolean; commands?: CostCommands; onRefresh: () => void; paging?: CostPaging;
}) {
  const [kind, setKind] = useState<OperationKind | null>(null);
  const command = useCostCommand(readId, stale, commands, onRefresh);
  const monthly = record.entries.filter((entry) => entry.project_id === record.project_id && entry.month === record.month);
  const month = record.source === 'session' ? record.summaries.monthly : totals(monthly);
  const iteration = record.source === 'session' ? record.summaries.iteration : totals(record.entries.filter((entry) => entry.project_id === record.project_id && entry.iteration_id === record.iteration_id));
  const entries = scopedEntries(record, scope).filter((entry) => kind === null || entry.kind === kind);
  return <div className="stack"><p className="label">{record.month} · USD · prices {record.price_version ?? 'per operation; version unavailable in this view'} · {scope.kind}: {scope.id}</p>
    {record.source === 'session' && <p>Complete month and iteration summaries. Ledger rows are independently read pages, not a shared snapshot. Refused-attempt history is not included here.</p>}
    <PaidPause reason={record.pause_reason}>{role === 'admin' && <button type="button" className="pill ghost" disabled={command.disabled} onClick={() => { if (commands) void command.execute(() => commands.resumePaid(record.revision)); }}>Resume paid steps</button>}</PaidPause>
    <div className="tiles">
      <BudgetMeter label="Spent and committed this month" used={month.exposure} limit={record.policy.monthly_limit} />
      <div className="tile"><span className="label">Reserved for queued work</span><strong className="value">{usd(month.reserved)}</strong><span className="tag warn">Estimate</span><p>{usd(month.actual)} actual · {usd(month.unconfirmed)} unconfirmed, still counted at estimate.</p></div>
      <BudgetMeter label={`This iteration · ${record.iteration_id}`} used={iteration.exposure} limit={record.policy.iteration_limit} />
      <div className="tile"><span className="label">Actual against estimate</span><strong className="value">{usd(month.delta)}</strong><p>This month · confirmed charges only.</p></div>
    </div>
    <div className="cost-layout">
      <aside className="card policy" aria-label="Budget and policy"><h2>Spending policy</h2>
        <p>Limits are checked against estimates, not caps on actual billing. Actual billing can come in above an estimate.</p>
        {role === 'admin' ? <PolicyEditor key={command.editorGeneration} record={record} command={command} commands={commands} onRefresh={onRefresh} /> : <Facts items={[
          ['Spend without asking', record.policy.autonomous_spend ? 'On' : 'Off'],
          ['Monthly limit', record.policy.monthly_limit === null ? 'Not set' : usd(record.policy.monthly_limit)],
          ['Per iteration', record.policy.iteration_limit === null ? 'Not set' : usd(record.policy.iteration_limit)],
          ['Per operation', record.policy.operation_limit === null ? 'Not set' : usd(record.policy.operation_limit)],
        ]} />}
      </aside>
      <section className="card ledger" aria-labelledby="ledger-heading"><header><h2 id="ledger-heading">Ledger</h2><button className="linkbtn" onClick={() => download(new Blob([ledgerCsv(entries)], { type: 'text/csv;charset=utf-8' }), 'costs.csv')}>{record.source === 'session' ? 'Download shown rows CSV' : 'Download CSV'}</button></header>
        <div className="chips" role="group" aria-label="Operation kind"><button className="chip" aria-pressed={kind === null} onClick={() => setKind(null)}>All</button>{kinds.map((item) => <button className="chip" key={item} aria-pressed={kind === item} onClick={() => setKind(item)}>{item}</button>)}</div>
        {entries.length === 0 ? <p>{record.source === 'session' ? 'No operations on this page match this scope and filter.' : record.entries.length === 0 ? 'Nothing paid has run yet. Local training and review are free.' : 'No operations match this scope and filter.'}</p> : <TableRegion label="Paid operation ledger"><table><caption>{record.source === 'session' ? 'Current ledger page · known reservation times first' : 'Every paid operation, including refused ones · newest first'}</caption><thead><tr>{['When (UTC)', 'Operation', 'Approved by', 'Estimate', 'Actual', 'Result'].map((label) => <th key={label} scope="col">{label === 'Estimate' ? <span className="tag warn">Estimate</span> : label}</th>)}</tr></thead><tbody>{entries.map((entry) => <tr key={entry.operation_id}>
          <td>{entry.at === null ? 'Reservation time unavailable' : <Utc value={entry.at} />}</td><th scope="row">{entry.kind ?? 'Operation kind unavailable'} · {entry.object_id ?? 'Object unavailable'}{entry.recipe_id && <> · {entry.recipe_id}</>}<span className="secondary">{entry.operation_id} · {entry.iteration_id}</span></th>
          <td>{entry.state === 'refused' ? <span className="tag danger">{approvalLabel(entry)}</span> : approvalLabel(entry)}</td>
          <td>{usd(entry.estimate)}</td><td>{actualCostLabel(entry)}</td><td>{entry.result}</td>
        </tr>)}</tbody></table></TableRegion>}
        {record.source === 'session' && paging && <>
          <p className="label">{record.entries.length} loaded operations · filters apply to loaded rows only</p>
          {record.next_after !== null && <button className="pill ghost audit-more" aria-disabled={stale || paging.pending || paging.failed} onClick={() => { if (!stale && !paging.pending && !paging.failed) paging.onMore(); }}>Show 50 more operations</button>}
          {paging.pending && <p role="status">Loading more operations…</p>}
          {paging.failed && <Notice tone="danger"><p role="alert">More operations could not be loaded. Existing operations are retained. Refresh records if policy changed.</p><button className="pill ghost" disabled={stale} onClick={paging.onRetry}>Retry loading more operations</button><button className="pill ghost" disabled={stale} onClick={onRefresh}>Refresh records</button></Notice>}
        </>}
      </section>
    </div>
  </div>;
}

function useCostCommand(readId: string | number, stale: boolean, commands: CostCommands | undefined, onRefresh: () => void) {
  const [pending, setPending] = useState(false);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [message, setMessage] = useState('');
  const [lastReadId, setLastReadId] = useState(readId);
  const [editorGeneration, setEditorGeneration] = useState(0);
  if (lastReadId !== readId) {
    setLastReadId(readId);
    // Ordinary unchanged-record reads retain the editor, filter and focused DOM.
    if (needsRefresh) {
      setNeedsRefresh(false);
      setMessage('');
      setEditorGeneration(editorGeneration + 1);
    }
  }
  const disabled = stale || pending || needsRefresh || commands === undefined;
  const execute = async (action: () => Promise<void>) => {
    if (disabled) return;
    setPending(true);
    try { await action(); setMessage('Saved. Refreshing the authoritative record.'); setNeedsRefresh(true); onRefresh(); }
    catch { setMessage('The change was not confirmed. Refresh records before trying again.'); setNeedsRefresh(true); }
    finally { setPending(false); }
  };
  return { disabled, execute, message, setMessage, needsRefresh, editorGeneration };
}

function PolicyEditor({ record, command, commands, onRefresh }: { record: CostsRecord; command: ReturnType<typeof useCostCommand>; commands?: CostCommands; onRefresh: () => void }) {
  const id = useId();
  const initial = (amount: number | null) => amount === null ? '' : String(amount / 1_000_000);
  const [monthly, setMonthly] = useState(initial(record.policy.monthly_limit));
  const [iteration, setIteration] = useState(initial(record.policy.iteration_limit));
  const [operation, setOperation] = useState(initial(record.policy.operation_limit));
  const [autonomous, setAutonomous] = useState(record.policy.autonomous_spend);
  const [confirm, setConfirm] = useState<BudgetPolicy | null>(null);
  const { disabled, execute, message, setMessage, needsRefresh } = command;
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
    {!commands && <p>{record.source === 'session' ? 'Read-only session records. Spending commands are not connected.' : 'Read-only fixture. Authenticated spending commands are not connected.'}</p>}
    <div className="toggle"><span>Spend without asking</span><button type="button" className="switch" role="switch" aria-label="Spend without asking" aria-checked={autonomous} disabled={disabled} onClick={() => { setConfirm(null); setAutonomous(!autonomous); }} aria-describedby={`${id}-why`} /><p id={`${id}-why`}>Off: every paid step waits for a person to approve its estimate. On: steps run inside the limits below, and each one still writes its estimate first.</p></div>
    {([['Monthly limit (USD)', monthly, setMonthly], ['Per iteration (USD)', iteration, setIteration], ['Per operation (USD)', operation, setOperation]] as const).map(([label, value, change], index) => <div className="field" key={label}><label htmlFor={`${id}-${index}`}>{label}</label><input id={`${id}-${index}`} className="input" inputMode="decimal" value={value} disabled={disabled} onChange={(event) => { setConfirm(null); change(event.target.value); }} /></div>)}
    {!policyComplete(record.policy) && <p>Set a monthly limit first. Paid steps are unavailable until all three limits are set.</p>}
    <p>An operation over any limit is refused, never queued.</p>
    <button className="pill" disabled={disabled}>Save limits</button>
    {confirm && <section className="confirm" aria-label="Confirm autonomous spending"><p>labelloop starts paid steps without asking while each estimate is {usd(confirm.operation_limit ?? 0)} or less, each iteration stays within {usd(confirm.iteration_limit ?? 0)}, and this month’s spend, plus estimates reserved for queued work, plus the new estimate stays within {usd(confirm.monthly_limit ?? 0)}. Actual billing can come in above an estimate.</p><button type="button" className="pill ghost" disabled={disabled} onClick={() => { if (commands) void execute(async () => { await commands.savePolicy(confirm, record.revision); setConfirm(null); }); }}>Turn on</button><button type="button" className="pill ghost" onClick={() => setConfirm(null)}>Cancel</button></section>}
    <p role="status">{message}</p>{needsRefresh && <Notice><button type="button" className="pill ghost" onClick={onRefresh}>Refresh records</button></Notice>}
  </form>;
}
