import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { AuditQuery, ViewState } from './contracts.ts';
import { CostsView } from './CostsView.tsx';
import { RecipeOperations } from './RecipeOperations.tsx';
import { auditFixture, costsFixture, fixtureTime, recipesFixture, recordedAuditPort } from './fixtures.ts';
import { ledgerCsv, parseLimit, policyComplete, scopedEntries, totals, usd } from './records.ts';

const ready = <T,>(data: T, stale = false): ViewState<T> => ({ status: 'ready', data, stale, updatedAt: fixtureTime });
const query: AuditQuery = { kinds: ['Review', 'Budget', 'Promotion', 'Connection'], principals: ['person', 'service'], search: '', from: '', through: '' };

describe('recorded operational projections', () => {
  it('preserves micro-USD precision and rejects malformed limit inputs', () => {
    expect(parseLimit('0.000001')).toBe(1);
    expect(parseLimit('1.38')).toBe(1_380_000);
    expect(parseLimit('')).toBeNull();
    expect(usd(1)).toBe('$0.000001');
    for (const invalid of ['-1', '1e6', 'Infinity', '1.0000001', '9007199254740991', '$4', ' 4']) expect(() => parseLimit(invalid)).toThrow();
    expect(policyComplete({ ...costsFixture.policy, operation_limit: null })).toBe(false);
  });
  it('counts unconfirmed completed billing at estimate, but not refusals or cancellation', () => {
    const entries = costsFixture.entries.filter((entry) => entry.month === '2026-11');
    expect(totals(entries)).toEqual({ actual: 540_000, reserved: 1_380_000, unconfirmed: 1_320_000, delta: -1_190_000, exposure: 3_240_000 });
    expect(totals([{ ...entries[0], state: 'cancelled' }]).exposure).toBe(0);
    expect(() => totals([{ ...entries[0], state: 'settled', actual: null }])).toThrow();
  });
  it('isolates project, iteration, run and review batch without losing refused attempts', () => {
    expect(scopedEntries(costsFixture, { kind: 'project', id: 'other' })).toHaveLength(0);
    expect(scopedEntries(costsFixture, { kind: 'iteration', id: 'iteration-4' })).toHaveLength(3);
    expect(scopedEntries(costsFixture, { kind: 'run', id: 'run-0030' }).map((e) => e.operation_id)).toEqual(['op-118']);
    expect(scopedEntries(costsFixture, { kind: 'review_batch', id: 'batch-49' }).map((e) => e.operation_id)).toEqual(['op-117']);
    expect(scopedEntries(costsFixture, { kind: 'project', id: 'shelf-demo' }).some((e) => e.state === 'refused')).toBe(true);
  });
  it('escapes CSV syntax and spreadsheet formulas without presenting unconfirmed costs as actuals', () => {
    const csv = ledgerCsv([{ ...costsFixture.entries[0], object_id: '=HYPERLINK("fixture")', result: 'line\nbreak, "quoted"' }]);
    expect(csv).toContain('"\'=HYPERLINK(""fixture"")"');
    expect(csv).toContain('"Unconfirmed"');
    expect(csv).toContain('line\nbreak, ""quoted""');
  });
  it('simulates authorization before query, pagination, counts and exports, not client hiding', async () => {
    const port = recordedAuditPort(auditFixture.filter((event) => event.kind === 'Review'));
    const controller = new AbortController();
    const page = await port.query(query, null, controller.signal);
    expect(page.total).toBe(3);
    expect(page.events.every((event) => event.kind === 'Review')).toBe(true);
    const exportText = await (await port.exportJsonLines(query, controller.signal)).text();
    expect(exportText).not.toContain('Promotion');
    expect(exportText).not.toContain('Connection');
    expect(exportText).toContain('refused');
    expect(await port.query({ ...query, search: 'missing' }, null, controller.signal)).toEqual({ events: [], total: 0, next_cursor: null });
    const dates = await port.query({ ...query, from: '2026-11-08' }, null, controller.signal);
    expect(dates.total).toBe(0);
    controller.abort();
    expect(() => port.query(query, null, controller.signal)).toThrow();
  });
});

describe('operational screen states', () => {
  it('shows read-only policy without fields and distinguishes actual from estimate', () => {
    const html = renderToStaticMarkup(<CostsView state={ready(costsFixture)} role="reviewer" scope={{ kind: 'project', id: 'shelf-demo' }} onRefresh={() => undefined} />);
    expect(html).not.toContain('<input');
    expect(html).not.toContain('role="switch"');
    expect(html).toContain('Unconfirmed');
    expect(html).toContain('Refused');
    expect(html).toContain('not caps on actual billing');
    expect(html).toContain("won&#x27;t retry on its own");
  });
  it('has loading, error, empty and stale states with no enabled spending command', () => {
    const states: ViewState<typeof costsFixture>[] = [{ status: 'loading' }, { status: 'error', retry: () => undefined }, ready({ ...costsFixture, entries: [] }), ready(costsFixture, true)];
    for (const [index, state] of states.entries()) {
      const html = renderToStaticMarkup(<CostsView state={state} role="admin" scope={{ kind: 'project', id: 'shelf-demo' }} onRefresh={() => undefined} />);
      expect(html).toContain(['Loading the latest', 'could not be loaded', 'Nothing paid has run', 'Stale data'][index]);
    }
  });
  it('renders evidence scope, unsupported reasons and complete rollback contracts without enablement', () => {
    const html = renderToStaticMarkup(<RecipeOperations state={ready(recipesFixture)} role="admin" />);
    expect(html).toContain('Canary · 3 of 10 groups');
    expect(html).toContain('store-9');
    expect(html).toContain('price-tag');
    expect(html).toContain('locked_final_test');
    expect(html).toContain('60 source groups · 120 images · 820 objects');
    expect(html).toContain('Unsupported · Absent from this evaluation set');
    expect(html).toContain('Complete rollback contract');
    expect(html).toContain('Confidence threshold');
    expect(html).toContain('Preprocessing SHA-256');
    expect(html).not.toContain('<button');
  });
  it('does not label training groups as fresh canary evidence', () => {
    const record = recipesFixture[0];
    const html = renderToStaticMarkup(<RecipeOperations state={ready([{ ...record, canary: { ...record.canary!, excluded_training_groups: ['fresh-1'] } }])} role="admin" />);
    expect(html).toContain('Fresh source-group evidence is inconsistent');
    expect(html).not.toContain('Canary · 3 of 10');
  });
});
