import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { BudgetPage, BudgetSummary, CostOperation } from './cost-client.ts';
import type { CostCommands, CostsRecord } from './contracts.ts';
import { CostsView } from './CostsView.tsx';
import { ledgerCsv, scopedEntries, totals } from './records.ts';
import { sessionCostsRecord } from './session-costs.ts';

const project = 'project-a';
const query = { month: '2026-11', iteration_id: 'iteration-a' };
const policy = { monthly_limit: 1_000_000, iteration_limit: 2_000_000, operation_limit: 0, autonomous_spend: false };
const total = { operation_count: 8, actual: 300_000, confirmed_estimate: 350_000, reserved: 500_000, unconfirmed: 200_000, exposure: 1_000_000, delta: -50_000 };
const summary: BudgetSummary = {
  project_id: project, currency: 'USD', unit: 'micro_usd', policy_revision: 1,
  policy, pause_reason: null, ...query, monthly: total, iteration: { ...total, reserved: 600_000, exposure: 1_100_000 },
};
const operation: CostOperation = {
  operation_id: 'operation-a', iteration_id: query.iteration_id, month: query.month, price_version: 'price-a',
  estimate: 100, actual: null, exposure: 900, state: 'unconfirmed', approved_by: null,
  reserved_autonomously: true, reserved_at: null, kind: null, annotation_job_id: null, review_pause: null,
};
const page: BudgetPage = {
  project_id: project, currency: 'USD', unit: 'micro_usd', policy_revision: 1,
  policy, pause_reason: null, operations: [operation], next_after: operation.operation_id,
};
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
function responses(first: unknown = summary, second: unknown = page) {
  const fetch = vi.fn().mockResolvedValueOnce(json(first)).mockResolvedValueOnce(json(second));
  vi.stubGlobal('fetch', fetch);
  return fetch;
}
const read = () => sessionCostsRecord(project, query, { limit: 1 }, new AbortController().signal);
function html(record: CostsRecord, commands?: CostCommands) {
  return renderToStaticMarkup(<CostsView readId={1} state={{ status: 'ready', data: record, updatedAt: '2026-11-09T12:00:00Z', stale: false }} role="admin" scope={{ kind: 'project', id: project }} commands={commands} onRefresh={() => undefined} />);
}
afterEach(() => vi.unstubAllGlobals());

describe('session cost display contract', () => {
  it('uses complete server totals, never the visible page or retained-child estimate', async () => {
    const fetch = responses();
    const record = await read();
    expect(record).toMatchObject({ source: 'session', summaries: { monthly: total, iteration: summary.iteration }, next_after: 'operation-a', revision: 1 });
    expect(record.entries[0]).toMatchObject({ estimate: 100, exposure: 900, actual: null });
    expect(totals(record.entries).exposure).toBe(900);
    const rendered = html(record);
    expect(rendered).toContain('100% of limit; $1.00 against $1.00');
    expect(rendered).toContain('$1.10 against $2.00');
    expect(rendered).toContain('$0.50');
    expect(rendered).toContain('-$0.05');
    expect(rendered).toContain('Current ledger page');
    expect(rendered).toContain('Download shown rows CSV');
    expect(rendered).not.toContain('Every paid operation, including refused ones');
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls[0]?.[0]).toContain('/costs/summary?');
    expect(fetch.mock.calls[1]?.[0]).toBe('/api/projects/project-a/costs?limit=1');
    expect(fetch.mock.calls.every((call) => (call[1] as RequestInit).credentials === 'same-origin')).toBe(true);
  });

  it('preserves unknown metadata without fabricated labels, dates, objects or price versions', async () => {
    responses();
    const record = await read();
    expect(record).toMatchObject({ price_version: null, entries: [{ at: null, kind: null, object_id: null, recipe_id: null }] });
    const rendered = html(record);
    for (const label of ['Operation kind unavailable', 'Object unavailable', 'Reservation time unavailable', 'Policy (autonomous)']) expect(rendered).toContain(label);
    expect(rendered).not.toContain('<time');
    const cells = ledgerCsv(record.entries).split('\r\n')[1].split(',');
    expect(cells.slice(0, 5)).toEqual(['""', '"operation-a"', '""', '""', '""']);
    expect(scopedEntries(record, { kind: 'run', id: 'operation-a' })).toEqual([]);
    expect(scopedEntries(record, { kind: 'review_batch', id: 'operation-a' })).toEqual([]);
  });

  it('retains review-job identity and pause without converting it to a refused reservation', async () => {
    responses(summary, { ...page, operations: [{ ...operation, kind: 'LLM review', annotation_job_id: '<batch>', review_pause: 'approval_exhausted', reserved_at: '2026-11-08T12:00:00.123456Z' }] });
    const record = await read();
    expect(record.entries[0]).toMatchObject({ state: 'unconfirmed', object_id: '<batch>', kind: 'LLM review', at: '2026-11-08T12:00:00.123456Z' });
    expect(scopedEntries(record, { kind: 'review_batch', id: '<batch>' })).toHaveLength(1);
    expect(html(record)).toContain('&lt;batch&gt;');
    expect(html(record)).toContain('review paused: approval exhausted');
    expect(html(record)).not.toContain('>Refused<');
  });

  it('retains confirmed zero separately from null billing and empty page state', async () => {
    responses(summary, { ...page, operations: [{ ...operation, state: 'settled', actual: 0, exposure: 0 }], next_after: null });
    const record = await read();
    expect(record.entries[0].actual).toBe(0);
    expect(ledgerCsv(record.entries)).toContain('"$0.00"');
    expect(html(record)).toContain('Billing confirmed');
    const empty = { ...record, entries: [] };
    expect(html(empty)).toContain('No operations on this page');
    expect(html(empty)).not.toContain('Nothing paid has run yet');
    expect(html(empty)).toContain('$1.00 against $1.00');
  });

  it('keeps session policy/resume controls disabled even if fixture commands are supplied', async () => {
    responses({ ...summary, pause_reason: 'insufficient_credits' }, { ...page, pause_reason: 'insufficient_credits' });
    const commands = { savePolicy: vi.fn(), resumePaid: vi.fn() };
    const rendered = html(await read(), commands);
    expect(rendered).toContain('Read-only session records');
    expect(rendered).toMatch(/disabled=""[^>]*>Resume paid steps/);
    expect(rendered).toMatch(/disabled=""[^>]*>Save limits/);
    expect(commands.savePolicy).not.toHaveBeenCalled();
    expect(commands.resumePaid).not.toHaveBeenCalled();
  });

  it.each([
    { policy_revision: 2 }, { pause_reason: 'estimate_overrun' },
    ...[{ monthly_limit: 0 }, { iteration_limit: null }, { operation_limit: null }, { autonomous_spend: true }].map((change) => ({ policy: { ...policy, ...change } })),
  ])('rejects mixed policy reads without retry: %j', async (change) => {
    const fetch = responses(summary, { ...page, ...change });
    await expect(read()).rejects.toThrow(/^Cost records unavailable$/);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it.each(['summary', 'page'])('rejects foreign %s scope rather than displaying a partial success', async (where) => {
    const fetch = responses(where === 'summary' ? { ...summary, project_id: 'foreign' } : summary, where === 'page' ? { ...page, project_id: 'foreign' } : page);
    await expect(read()).rejects.toThrow(/^Cost records unavailable$/);
    expect(fetch).toHaveBeenCalledTimes(where === 'summary' ? 1 : 2);
  });

  it('forwards the same cancellation signal to both reads and never retries an expired session', async () => {
    const fetch = responses();
    const controller = new AbortController();
    await sessionCostsRecord(project, query, { limit: 1 }, controller.signal);
    expect(fetch.mock.calls.every((call) => (call[1] as RequestInit).signal === controller.signal)).toBe(true);
    fetch.mockReset().mockResolvedValueOnce(json(summary)).mockResolvedValueOnce(new Response('private', { status: 401 }));
    await expect(read()).rejects.toThrow(/^Cost records unavailable$/);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
