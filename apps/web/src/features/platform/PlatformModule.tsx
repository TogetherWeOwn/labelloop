import { useEffect, useState } from 'react';
import type { PlatformReadPort, PlatformSnapshot, ViewerRole } from './contracts.ts';
import { statusCopy } from './contracts.ts';
import { ConnectionsView } from './ConnectionsView.tsx';
import { PlatformRunsView } from './PlatformRunsView.tsx';
import './platform.css';

type Page = 'connections' | 'runs';
type ReadState = { snapshot: PlatformSnapshot | null; failed: boolean; announcement: string };

// The caller keys this boundary by authenticated principal/project. No credentials or
// principal are inferred from URLs. No live adapter is shipped by this module.
export function PlatformModule({ role, page, port }: { role: ViewerRole; page: Page; port: PlatformReadPort }) {
  const allowed = page !== 'connections' || role === 'admin';
  return <div className="platform-module">
    {!allowed ? <><h1>Connections.</h1><p>Only an admin can manage connections.</p></> :
      <PlatformContent key={`${role}:${page}`} role={role} page={page} port={port} />}
  </div>;
}

function PlatformContent({ role, page, port }: { role: ViewerRole; page: Page; port: PlatformReadPort }) {
  const [state, setState] = useState<ReadState>({ snapshot: null, failed: false, announcement: '' });
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    let disposed = false;
    let pending = false;
    let current: AbortController | null = null;
    const refresh = async () => {
      if (document.visibilityState === 'hidden' || pending) return;
      pending = true;
      current = new AbortController();
      try {
        const snapshot = await port.read(current.signal);
        if (!disposed) {
          setState((previous) => {
            const priorStatuses = new Map(previous.snapshot?.runs.map((run) => [run.id, run.status]));
            const changes = snapshot.runs.filter((run) => priorStatuses.has(run.id) && priorStatuses.get(run.id) !== run.status)
              .map((run) => `${run.id}: ${statusCopy[run.status].label}${run.status === 'args_mismatch' ? ' (args mismatch)' : ''}.`);
            // Keep the live text stable on identical polls, including refreshed timestamps.
            return { snapshot, failed: false, announcement: changes.join(' ') || previous.announcement };
          });
          setNow(Date.now());
        }
      } catch {
        if (!disposed) {
          setState((previous) => ({ ...previous, failed: true }));
          setNow(Date.now());
        }
      } finally { pending = false; }
    };
    void refresh();
    const onVisibility = () => {
      // Freshness ages even if the next read hangs or the tab was hidden.
      setNow(Date.now());
      void refresh();
    };
    const interval = window.setInterval(onVisibility, 30_000);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      disposed = true;
      current?.abort();
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [port]);
  const { snapshot, failed } = state;
  const observedAt = snapshot ? Date.parse(snapshot.observedAt) : NaN;
  const stale = failed || !Number.isFinite(observedAt) || now - observedAt > 60_000;
  return <div className="stack">
    {!snapshot && <div className="phead"><h1>{page === 'connections' ? 'Connections.' : 'Training runs.'}</h1></div>}
    <p className="notice warn">Fixture preview · Not connected to a live service. Keys, workspace changes and paid operations are disabled until authenticated adapter and ledger integration.</p>
    <div role="status" aria-live="polite">
      {failed ? <p>Could not refresh. Check the connection and try again.</p> : !snapshot && <p>Loading Platform records…</p>}
      {snapshot && <p className={stale ? 'freshness warn' : 'freshness'}>{stale ? 'Stale data · ' : ''}Record observed {snapshot.observedAt} (UTC). Refreshes every 30 seconds while visible.</p>}
    </div>
    {page === 'runs' && <p className="sr" role="status" aria-label="Run status updates" aria-live="polite" aria-atomic="true">{state.announcement}</p>}
    {snapshot && <>
      {snapshot.paidStepsPaused && <p className="notice danger">Paid steps paused · Out of credits (402). labelloop won't retry on its own.</p>}
      {page === 'connections' ? <ConnectionsView role={role} snapshot={snapshot} now={now} /> : <PlatformRunsView role={role} snapshot={snapshot} />}
    </>}
  </div>;
}
