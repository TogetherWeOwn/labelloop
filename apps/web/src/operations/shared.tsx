import type { ReactNode } from 'react';
import type { ViewState } from './contracts.ts';

export function Notice({ children, tone = 'warn' }: { children: ReactNode; tone?: 'warn' | 'danger' }) {
  return <div className={`notice ${tone}`}><span aria-hidden="true">!</span><div>{children}</div></div>;
}

export function ViewBoundary<T>({ state, children }: { state: ViewState<T>; children: (data: T, stale: boolean) => ReactNode }) {
  if (state.status === 'loading') return <section className="card" aria-busy="true"><h2>Loading records</h2><p role="status">Loading the latest records…</p></section>;
  if (state.status === 'error') return <Notice tone="danger"><p>Records could not be loaded. Try again.</p><button className="pill ghost" onClick={state.retry}>Try again</button></Notice>;
  return <><p className={`freshness ${state.stale ? 'warning' : ''}`}>{state.stale ? 'Stale data · actions unavailable · last updated' : 'Last updated'} <Utc value={state.updatedAt} /></p>{children(state.data, state.stale)}</>;
}

export function Utc({ value }: { value: string }) {
  return <time className="mono" dateTime={value}>{value.replace('T', ' ').replace('.000Z', '').replace('Z', '')} UTC</time>;
}

export function TableRegion({ label, children }: { label: string; children: ReactNode }) {
  return <div className="tablewrap" tabIndex={0} role="region" aria-label={label}>{children}</div>;
}

export function Facts({ items }: { items: readonly (readonly [string, ReactNode])[] }) {
  return <dl className="facts">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

export function PaidPause({ reason, children }: { reason: 'insufficient_credits' | 'estimate_overrun' | null; children?: ReactNode }) {
  if (reason === null) return null;
  return <Notice tone="danger"><p>{reason === 'insufficient_credits'
    ? "Paid steps are paused: Ultralytics Platform said the account is out of credits (402). Top up the account, then resume. labelloop won't retry on its own."
    : 'Paid steps are paused: actual billing exceeded the estimate. Reconcile billing and review limits before resuming.'}</p>{children && <div className="acts">{children}</div>}</Notice>;
}
