import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { relative, resolve } from 'node:path';
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
  it('exports the same actual-cost labels as the ledger for every billing state', () => {
    for (const state of ['refused', 'cancelled', 'reserved', 'dispatched', 'unconfirmed', 'insufficient_credits', 'settled'] as const) {
      const entry = { ...costsFixture.entries[0], state, actual: state === 'settled' ? 1 : null };
      const expected = state === 'settled' ? '$0.000001' : state === 'refused' || state === 'cancelled' ? 'Not billed' : 'Unconfirmed';
      expect(ledgerCsv([entry])).toContain(`"${expected}"`);
      const html = renderToStaticMarkup(<CostsView readId={0} state={ready({ ...costsFixture, entries: [entry] })} role="reviewer" scope={{ kind: 'project', id: 'shelf-demo' }} onRefresh={() => undefined} />);
      expect(html).toContain(expected);
    }
  });
  it('exports the ledger approval label independently of approver presence', () => {
    for (const state of ['refused', 'cancelled', 'reserved', 'dispatched', 'unconfirmed', 'insufficient_credits', 'settled'] as const) for (const approved_by of [null, 'Named approver']) {
      const entry = { ...costsFixture.entries[0], state, approved_by, actual: state === 'settled' ? 1 : null };
      const expected = state === 'refused' ? 'Refused' : approved_by ?? 'Approval unavailable';
      expect(ledgerCsv([entry]).split('\r\n')[1].split(',')[5]).toBe(`"${expected}"`);
      const html = renderToStaticMarkup(<CostsView readId={0} state={ready({ ...costsFixture, entries: [entry] })} role="reviewer" scope={{ kind: 'project', id: 'shelf-demo' }} onRefresh={() => undefined} />);
      expect(html).toContain(expected);
    }
  });
  it('keeps complete source ground truth identical across recipes of the same evaluation set', () => {
    const evidence = recipesFixture.map((record) => record.qualification.evidence[0]);
    const sourceFacts = (item: typeof evidence[number]) => ({ set: item.set_version, role: item.set_role, groups: item.source_groups, images: item.images, objects: item.objects, support: item.classes.map(({ id, support }) => ({ id, support })) });
    expect(sourceFacts(evidence[0])).toEqual(sourceFacts(evidence[1]));
    for (const item of evidence) expect(item.classes.reduce((sum, row) => sum + row.support, 0)).toBe(item.objects);
    expect(evidence.every((item) => item.classes.some((row) => row.support === 0 && !row.supported))).toBe(true);
  });
  it('qualifies web changes for every consumed design-token import, even within docs', () => {
    const root = fileURLToPath(new URL('../../../../', import.meta.url));
    const cssPath = fileURLToPath(new URL('./operations.css', import.meta.url));
    const css = readFileSync(cssPath, 'utf8');
    const workflow = readFileSync(resolve(root, '.github/workflows/ci.yml'), 'utf8');
    const areas = JSON.parse(workflow.match(/areas-json: '([^']+)'/)![1]) as { web: string[] };
    const dependencies = [...css.matchAll(/@import ['"]([^'"]+)['"]/g)].map((match) => relative(root, fileURLToPath(new URL(match[1], new URL('./operations.css', import.meta.url)))));
    expect(dependencies.length).toBeGreaterThan(0);
    for (const dependency of dependencies) expect(areas.web).toContain(dependency);
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
    const html = renderToStaticMarkup(<CostsView readId={0} state={ready(costsFixture)} role="reviewer" scope={{ kind: 'project', id: 'shelf-demo' }} onRefresh={() => undefined} />);
    expect(html).not.toContain('<input');
    expect(html).not.toContain('role="switch"');
    expect(html).toContain('Unconfirmed');
    expect(html).toContain('Refused');
    expect(html).toContain('not caps on actual billing');
    expect(html).toContain("won&#x27;t retry on its own");
  });
  it('reports exhaustion using integer exposure, not rounded percentages', () => {
    for (const [used, limit, exhausted] of [[99_010_000, 100_000_000, false], [100_000_000, 100_000_000, true], [100_000_001, 100_000_000, true], [0, 0, true], [1, 0, true]] as const) {
      const record = { ...costsFixture, policy: { ...costsFixture.policy, monthly_limit: limit, iteration_limit: limit }, entries: [{ ...costsFixture.entries[0], state: 'reserved' as const, estimate: used }] };
      const html = renderToStaticMarkup(<CostsView readId={0} state={ready(record)} role="reviewer" scope={{ kind: 'project', id: 'shelf-demo' }} onRefresh={() => undefined} />);
      expect(html.includes('At or above the limit.')).toBe(exhausted);
      if (!exhausted) expect(html).not.toContain('100%');
    }
  });
  it('formats budget percentages without floating-point noise or premature exhaustion', () => {
    for (const [used, expected] of [[3_240_000, '8.1'], [33_640_000, '84.1'], [39_999_999, '99.9'], [40_000_000, '100']] as const) {
      const record = { ...costsFixture, entries: [{ ...costsFixture.entries[0], state: 'reserved' as const, estimate: used }] };
      const html = renderToStaticMarkup(<CostsView readId={0} state={ready(record)} role="reviewer" scope={{ kind: 'project', id: 'shelf-demo' }} onRefresh={() => undefined} />);
      expect(html).toContain(`aria-valuetext="${expected}% of limit;`);
      if (used > 32_000_000 && used < 40_000_000) expect(html).toContain(`used ${expected}% of this limit.`);
    }
  });
  it('has loading, error, empty and stale states with no enabled spending command', () => {
    const states: ViewState<typeof costsFixture>[] = [{ status: 'loading' }, { status: 'error', retry: () => undefined }, ready({ ...costsFixture, entries: [] }), ready(costsFixture, true)];
    for (const [index, state] of states.entries()) {
      const html = renderToStaticMarkup(<CostsView readId={0} state={state} role="admin" scope={{ kind: 'project', id: 'shelf-demo' }} onRefresh={() => undefined} />);
      expect(html).toContain(['Loading the latest', 'could not be loaded', 'Nothing paid has run', 'Stale data'][index]);
    }
  });
  it('renders evidence scope, unsupported reasons and complete rollback contracts without enablement', () => {
    const html = renderToStaticMarkup(<RecipeOperations state={ready(recipesFixture)} role="admin" />);
    expect(html).toContain('Canary · 3 of 10 groups');
    expect(html).toContain('store-9');
    expect(html).toContain('price-tag');
    expect(html).toContain('locked_final_test');
    expect(html).toContain('60 source groups · 120 images · 780 objects');
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
