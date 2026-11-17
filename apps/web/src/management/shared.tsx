import { useId } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import type { Actions, Fact, Metric } from './contracts.ts';

export function Notice({ children, tone = 'warn' }: { children: ReactNode; tone?: 'warn' | 'danger' | 'ok' | 'info' }) {
  return <div className={`notice ${tone}`}><span aria-hidden="true">{tone === 'ok' ? '✓' : tone === 'info' ? 'ⓘ' : '△'}</span><div>{children}</div></div>;
}
export function Facts({ items }: { items: readonly Fact[] }) {
  return <dl className="facts">{items.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>;
}
export function Table({ caption, columns, children }: { caption: string; columns: readonly string[]; children: ReactNode }) {
  return <div className="tablewrap" tabIndex={0} role="region" aria-label={`${caption}, scrolls sideways`}><table><caption className="sr">{caption}</caption><thead><tr>{columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr></thead><tbody>{children}</tbody></table></div>;
}
export function PageHeading({ title, gloss, lede, meta, children }: { title: string; gloss: string; lede?: string; meta?: ReactNode; children?: ReactNode }) {
  return <header className="phead"><div>{meta && <p className="label">{meta}</p>}<h1>{title} <span>{gloss}</span></h1>{lede && <p className="lede">{lede}</p>}</div>{children && <div className="acts">{children}</div>}</header>;
}
export function Button({ primary = false, children, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { primary?: boolean }) {
  return <button type="button" {...props} className={`pill ${primary && !props.disabled ? 'primary' : 'ghost'} ${className}`}>{children}</button>;
}
export function CommandButton({ actions, reason, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { actions: Actions; reason?: string; primary?: boolean }) {
  const id = useId();
  if (actions.role !== 'admin') return null;
  const unavailable = reason ?? (!actions.connected ? 'Commands are not connected in this fixture preview.' : null);
  return <span className="command"><Button {...props} disabled={Boolean(unavailable) || actions.busy || props.disabled} aria-describedby={unavailable ? id : props['aria-describedby']}>{children}</Button>{unavailable && <span className="help" id={id}>{unavailable}</span>}</span>;
}
export function ReadOnly({ role }: { role: Actions['role'] }) {
  return role === 'reviewer' ? <p className="readonly">Read-only. Only an admin can change this page. Server authorization remains authoritative.</p> : null;
}
export function Tile({ value, label, children }: { value: ReactNode; label: string; children?: ReactNode }) {
  return <div className="tile"><strong className="value">{value}</strong><span>{label}</span>{children}</div>;
}
export function Delta({ current, candidate }: Metric) {
  if (current === null) return <span className="small">First model</span>;
  const change = Math.round((candidate - current) * 1000) / 1000;
  return <span className="mono small">{change > 0 ? '+' : ''}{change} against current {current}</span>;
}
export function Utc({ value }: { value: string }) {
  return <time dateTime={value}>{value.replace('T', ' ').replace('Z', ' UTC')}</time>;
}
export function Download({ name, value, children }: { name: string; value: unknown; children: ReactNode }) {
  return <a className="pill ghost" download={name} href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(value, null, 2))}`}>{children}</a>;
}
export function Field({ name, label, value, readOnly, type = 'text', onChange }: { name: string; label: string; value: string | number; readOnly: boolean; type?: 'text' | 'number'; onChange: (value: string) => void }) {
  const id = useId();
  return <div className="field"><label htmlFor={id}>{label}</label><input id={id} name={name} type={type} value={value} readOnly={readOnly} onChange={(event) => onChange(event.currentTarget.value)} /></div>;
}
