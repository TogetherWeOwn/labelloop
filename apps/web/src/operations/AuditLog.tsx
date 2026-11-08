import { useEffect, useId, useRef, useState } from 'react';
import type { AuditKind, AuditPage, AuditPort, AuditQuery, Role, ViewState } from './contracts.ts';
import { download } from './records.ts';
import { Notice, TableRegion, Utc, ViewBoundary } from './shared.tsx';

const kinds: readonly AuditKind[] = ['Review', 'Promotion', 'Connection', 'Budget'];
const initialQuery: AuditQuery = { kinds, principals: ['person', 'service'], search: '', from: '', through: '' };

type AuditLogProps = { port: AuditPort; role: Role; updatedAt: string; stale?: boolean };

export function AuditLog({ port, role, updatedAt, stale = false }: AuditLogProps) {
  const [query, setQuery] = useState<AuditQuery>(initialQuery);
  const [lifetime, setLifetime] = useState({ port, role, updatedAt, stale, generation: 0 });
  // Invalidate before rendering: old rows must never acquire a new scope or timestamp.
  if (lifetime.port !== port || lifetime.role !== role || lifetime.updatedAt !== updatedAt || lifetime.stale !== stale) {
    setLifetime({ port, role, updatedAt, stale, generation: lifetime.generation + 1 });
  }
  return <AuditSession key={lifetime.generation} port={port} role={role} updatedAt={updatedAt} stale={stale} query={query} onQuery={setQuery} />;
}

function AuditSession({ port, role, updatedAt, stale = false, query, onQuery }: AuditLogProps & { query: AuditQuery; onQuery: (query: AuditQuery) => void }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; page: AuditPage } | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [exporting, setExporting] = useState(false);
  const exportRequest = useRef<AbortController | null>(null);
  const key = JSON.stringify([query, cursor, attempt]);
  useEffect(() => () => exportRequest.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    void port.query(query, cursor, controller.signal).then((page) => {
      if (controller.signal.aborted) return;
      setResult((previous) => ({ key, page: cursor === null ? page : { ...page, events: [...(previous?.page.events ?? []), ...page.events] } }));
      setFailed(null);
    }).catch(() => { if (!controller.signal.aborted) setFailed(key); });
    return () => controller.abort();
  }, [port, query, cursor, key]);
  const state: ViewState<AuditPage> = failed === key ? { status: 'error', retry: () => setAttempt(attempt + 1) } : result?.key === key ? { status: 'ready', data: result.page, updatedAt, stale } : { status: 'loading' };
  return <AuditView state={state} query={query} role={role} message={message}
    onQuery={(next) => { setCursor(null); setResult(null); onQuery(next); }}
    onMore={() => { if (result?.page.next_cursor) setCursor(result.page.next_cursor); }}
    exporting={exporting}
    onExport={() => {
      if (exporting || stale || state.status !== 'ready') return;
      const exportController = new AbortController();
      exportRequest.current = exportController;
      setExporting(true); setMessage('Preparing the authorized export…');
      void port.exportJsonLines(query, exportController.signal).then((blob) => {
        if (exportController.signal.aborted) return;
        download(blob, 'audit.jsonl'); setMessage('Export downloaded.');
      }).catch(() => { if (!exportController.signal.aborted) setMessage('Export could not be loaded. Try again.'); })
        .finally(() => { if (!exportController.signal.aborted) setExporting(false); });
    }} />;
}

export function AuditView({ state, role, query, onQuery, onMore, onExport, exporting = false, message = '' }: {
  state: ViewState<AuditPage>; role: Role; query: AuditQuery;
  onQuery: (query: AuditQuery) => void; onMore: () => void; onExport: () => void; exporting?: boolean; message?: string;
}) {
  const id = useId();
  const toggleKind = (kind: AuditKind) => onQuery({ ...query, kinds: query.kinds.includes(kind) ? query.kinds.filter((k) => k !== kind) : [...query.kinds, kind] });
  const disabled = state.status !== 'ready' || state.stale;
  return <section className="operations"><header className="phead"><div><h1>Audit log. <span>Who did what, and as whom.</span></h1><p>Entries can’t be edited. Every event names the authenticated person or service.</p>
    {role === 'reviewer' && <p>Read-only · review events only. Admins see every kind.</p>}</div></header>
    <div className="audit-filters"><div className="chips" role="group" aria-label="Event kind">{(role === 'reviewer' ? ['Review'] as const : kinds).map((kind) => <button className="chip" key={kind} aria-pressed={query.kinds.includes(kind)} onClick={() => toggleKind(kind)}>{kind}</button>)}</div>
      <div className="chips" role="group" aria-label="Who">{(['person', 'service'] as const).map((type) => <button className="chip" key={type} aria-pressed={query.principals.includes(type)} onClick={() => onQuery({ ...query, principals: query.principals.includes(type) ? query.principals.filter((p) => p !== type) : [...query.principals, type] })}>{type === 'person' ? 'People' : 'Services'}</button>)}</div>
      <div className="field"><label htmlFor={`${id}-search`}>Image, run, model or person</label><input id={`${id}-search`} className="input" type="search" value={query.search} onChange={(event) => onQuery({ ...query, search: event.target.value })} /></div>
      <div className="field"><label htmlFor={`${id}-from`}>From (UTC)</label><input className="input" id={`${id}-from`} type="date" value={query.from} max={query.through || undefined} onChange={(event) => onQuery({ ...query, from: event.target.value })} /></div>
      <div className="field"><label htmlFor={`${id}-through`}>Through (UTC)</label><input className="input" id={`${id}-through`} type="date" value={query.through} min={query.from || undefined} onChange={(event) => onQuery({ ...query, through: event.target.value })} /></div>
    </div>
    <ViewBoundary state={state}>{(page) => <>
      {page.events.length === 0 ? <Notice><p>No {query.kinds.length === 1 ? query.kinds[0]?.toLowerCase() : 'matching'} events in this period.</p><button className="pill ghost" onClick={() => onQuery(initialQuery)}>Clear filters</button></Notice> : <TableRegion label="Audit events"><table className="audit-table"><caption>Audit events · newest first</caption><thead><tr><th scope="col">Time (UTC)</th><th scope="col">Who</th><th scope="col" className="audit-extra">Kind</th><th scope="col">What happened</th><th scope="col" className="audit-extra">Record</th></tr></thead><tbody>{page.events.map((event) => <tr key={event.id}>
        <td><Utc value={event.at} /></td><td><span className={event.principal.type === 'service' ? 'mono' : ''}>{event.principal.name}</span>{event.principal.type === 'service' && <> <span className="tag">Service</span></>}</td>
        <td className="audit-extra">{event.kind}</td><td><span className={event.refused ? 'danger-text' : ''}>{event.sentence}</span><span className="audit-folded secondary">{event.kind} · <RecordLink record={event.record} /></span></td><td className="audit-extra"><RecordLink record={event.record} /></td>
      </tr>)}</tbody></table></TableRegion>}
      <p className="label">{page.events.length} of {page.total} events</p>
      {page.next_cursor !== null && <button className="pill ghost audit-more" disabled={disabled} onClick={onMore}>Show 50 more</button>}
    </>}</ViewBoundary>
    <button className="linkbtn" disabled={disabled || exporting} onClick={onExport}>Export JSON lines</button><p role="status">{message}</p>
  </section>;
}

function RecordLink({ record }: { record: { href: string; label: string } | null }) {
  return record && /^\/(?!\/)[a-zA-Z0-9/?#%=&._-]*$/.test(record.href) ? <a href={record.href}>{record.label}</a> : <span>{record?.label ?? 'No linked record'}</span>;
}
