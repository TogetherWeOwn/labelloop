import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AuditView } from './AuditLog.tsx';
import type { AuditMetadata } from './audit-client.ts';
import type { AuditQuery } from './contracts.ts';
import { sessionAuditPort } from './session-audit.ts';

const id = '00000000-0000-0000-0000-000000000001';
const entry: AuditMetadata = { id, at: '2026-11-09T08:00:00Z', kind: 'Promotion', actor_id: 'historical-actor', actor_kind: 'unknown', actor_name: null, record_id: id, revision: 0, sentence: 'Recorded a registry transition.', refused: null };
const query: AuditQuery = { kinds: ['Review', 'Promotion', 'Connection', 'Budget'], principals: ['person', 'service'], search: '', from: '', through: '' };
const signal = () => new AbortController().signal;
const stub = (events: AuditMetadata[], extra = {}) => {
  const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({ project_id: 'project-a', events, total: events.length, next_after: null, ...extra }), { headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', request);
  return request;
};
const path = (request: ReturnType<typeof stub>) => new URL(request.mock.calls[0][0] as string, 'https://example.test');

afterEach(() => vi.unstubAllGlobals());

describe('session-backed AuditLog projection', () => {
  it('preserves unknown provenance, nullable refusal and server totals/cursors without inventing links', async () => {
    const request = stub([entry], { total: 9, next_after: `Promotion/${id}` });
    const page = await sessionAuditPort('project-a').query(query, null, signal());
    expect(page).toEqual({ events: [{ id: `Promotion/${id}`, at: entry.at, kind: 'Promotion', principal: { id: entry.actor_id, name: entry.actor_id, type: 'unknown', recorded_kind: 'unknown' }, sentence: entry.sentence, refused: null, record: null }], total: 9, next_cursor: `Promotion/${id}` });
    expect(path(request).searchParams.getAll('actor_kind')).toEqual([]);
    expect(path(request).searchParams.getAll('kind')).toEqual(['Review', 'Promotion', 'Budget']);
    const html = renderToStaticMarkup(<AuditView state={{ status: 'ready', data: page, updatedAt: entry.at, stale: false }} role="admin" query={query} onQuery={() => undefined} onMore={() => undefined} onExport={() => undefined} />);
    expect(html).toContain('Actor kind unavailable');
    expect(html).not.toContain('>Service</span>');
    expect(html).toContain('No linked record');
    expect(html).not.toContain('class="danger-text"');
    expect(html).not.toContain('Every event names the authenticated person or service');
  });

  it.each(['human', 'model', 'system', 'service', 'unknown'] as const)('retains exact %s provenance and current names, even with shared actor identifiers', async (actor_kind) => {
    stub([{ ...entry, actor_kind, actor_name: 'Current name', refused: true }]);
    const page = await sessionAuditPort('project-a').query(query, null, signal());
    expect(page.events[0].principal).toEqual({ id: entry.actor_id, name: 'Current name', type: actor_kind === 'unknown' ? 'unknown' : actor_kind === 'human' ? 'person' : 'service', recorded_kind: actor_kind });
    expect(page.events[0].refused).toBe(true);
  });

  it.each([true, false, null])('styles only explicitly refused events: %s', async (refused) => {
    stub([{ ...entry, refused, actor_name: '<script>Current name</script>' }]);
    const page = await sessionAuditPort('project-a').query(query, null, signal());
    const html = renderToStaticMarkup(<AuditView state={{ status: 'ready', data: page, updatedAt: entry.at, stale: false }} role="admin" query={query} onQuery={() => undefined} onMore={() => undefined} onExport={() => undefined} />);
    expect(html.includes('class="danger-text"')).toBe(refused === true);
    expect(html).toContain('Actor kind unavailable');
    expect(html).toContain('&lt;script&gt;Current name&lt;/script&gt;');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('>Service</span>');
  });

  it.each([
    [['person'], ['human']],
    [['service'], ['model', 'system', 'service']],
    [['service', 'person'], []],
  ] as const)('translates the existing Who categories on the server: %j', async (principals, expected) => {
    const request = stub([]);
    await sessionAuditPort('project-a').query({ ...query, principals }, null, signal());
    expect(path(request).searchParams.getAll('actor_kind')).toEqual(expected);
  });

  it('uses exact UTC calendar-day bounds, literal search, and the server cursor', async () => {
    const request = stub([]);
    await sessionAuditPort('project-a').query({ ...query, kinds: ['Review'], principals: ['person'], from: '2024-02-29', through: '2024-03-01', search: 'name%_ &' }, `Review/${id}`, signal());
    const params = path(request).searchParams;
    expect(params.get('since')).toBe('2024-02-29T00:00:00Z');
    expect(params.get('through')).toBe('2024-03-01T23:59:59.999999Z');
    expect(params.get('after')).toBe(`Review/${id}`);
    expect(params.get('limit')).toBe('50');
    expect(params.get('search')).toBe('name%_ &');
  });

  it.each([
    { kinds: [] }, { kinds: ['Connection'] }, { kinds: ['Other'] }, { principals: [] }, { principals: ['unknown'] },
    { from: '2025-02-29' }, { from: '2026-04-31' }, { through: '2026-13-01' }, { from: '2026-1-01' },
    { from: '2026-11-10', through: '2026-11-09' }, { search: 'x'.repeat(129) },
  ])('fails closed before network access for unsupported filters: %j', async (change) => {
    const request = stub([]);
    const port = sessionAuditPort('project-a');
    const invalid = { ...query, ...change } as AuditQuery;
    await expect(port.query(invalid, null, signal())).rejects.toThrow('Audit records unavailable');
    await expect(port.exportJsonLines(invalid, signal())).rejects.toThrow('Audit records unavailable');
    expect(request).not.toHaveBeenCalled();
  });

  it('keeps kind-distinct IDs distinct without dropping or reordering authorized rows', async () => {
    stub([entry, { ...entry, kind: 'Review', actor_kind: 'model' }, { ...entry, kind: 'Budget', actor_kind: 'service', refused: false }]);
    const page = await sessionAuditPort('project-a').query(query, null, signal());
    expect(page.events.map((event) => event.id)).toEqual([`Promotion/${id}`, `Review/${id}`, `Budget/${id}`]);
    expect(page.events.map((event) => event.refused)).toEqual([null, null, false]);
  });

  it('exports complete original metadata with identical filters, not the displayed page or projected types', async () => {
    const body = `${JSON.stringify(entry)}\n`;
    const request = vi.fn().mockResolvedValue(new Response(body, { headers: { 'Content-Type': 'application/x-ndjson' } }));
    vi.stubGlobal('fetch', request);
    const controller = new AbortController();
    const blob = await sessionAuditPort('project-a').exportJsonLines({ ...query, from: '2026-11-09', through: '2026-11-09', search: 'name' }, controller.signal);
    expect(await blob.text()).toBe(body);
    const url = path(request);
    expect(url.pathname).toBe('/api/projects/project-a/audit-log/export');
    expect(url.searchParams.getAll('actor_kind')).toEqual([]);
    expect(url.searchParams.get('since')).toBe('2026-11-09T00:00:00Z');
    expect(url.searchParams.get('through')).toBe('2026-11-09T23:59:59.999999Z');
    expect(url.searchParams.get('search')).toBe('name');
    expect(url.searchParams.has('after')).toBe(false);
    expect(url.searchParams.has('limit')).toBe(false);
    expect(request.mock.calls[0][1]).toMatchObject({ credentials: 'same-origin', signal: controller.signal });
  });

  it('forwards cancellation and never retries a rejected session read', async () => {
    const request = stub([]);
    const controller = new AbortController(); controller.abort();
    await expect(sessionAuditPort('project-a').query(query, null, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(request).not.toHaveBeenCalled();
    request.mockResolvedValue(new Response('private server failure', { status: 403 }));
    await expect(sessionAuditPort('project-a').query(query, null, signal())).rejects.toThrow(/^Audit records unavailable$/);
    expect(request).toHaveBeenCalledTimes(1);
  });
});
