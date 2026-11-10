import { afterEach, describe, expect, it, vi } from 'vitest';
import { auditClient, type AuditMetadata, type AuditReadQuery } from './audit-client.ts';

const id = '00000000-0000-0000-0000-000000000001';
const entry: AuditMetadata = {
  id, at: '2026-11-09T08:00:00Z', kind: 'Promotion', actor_id: 'historical-actor',
  actor_kind: 'unknown', actor_name: null, record_id: id, revision: 0,
  sentence: 'Recorded a registry transition.', refused: null,
};
const signal = () => new AbortController().signal;
const page = (events: unknown[] = [entry], extra = {}) => ({ project_id: 'project-a', events, total: events.length, next_after: null, ...extra });
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json; charset=utf-8' } });
const lines = (body: string) => new Response(body, { headers: { 'Content-Type': 'application/x-ndjson' } });
const stub = (response: Response) => {
  const request = vi.fn().mockResolvedValue(response);
  vi.stubGlobal('fetch', request);
  return request;
};

afterEach(() => vi.unstubAllGlobals());

describe('session-backed audit transport', () => {
  it('preserves unknown provenance, null outcomes and kind-distinct UUIDs', async () => {
    const events = [entry, { ...entry, kind: 'Review', actor_kind: 'model', actor_name: 'Current service' }, { ...entry, kind: 'Budget', actor_kind: 'human', refused: true }];
    const request = stub(json(page(events)));
    const abort = signal();
    await expect(auditClient('project-a').query({}, abort)).resolves.toEqual(page(events));
    expect(request).toHaveBeenCalledWith('/api/projects/project-a/audit-log?limit=50', {
      method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: abort,
      headers: { Accept: 'application/json' },
    });
  });

  it('sends repeated server filters and opaque cursors without local page filtering', async () => {
    const request = stub(json(page([entry], { total: 3, next_after: `Promotion/${id}` })));
    await auditClient('project-a').query({ kinds: ['Promotion', 'Review'], actorKinds: ['unknown', 'human'], search: 'name%_ &', since: '2026-11-08T00:00:00Z', through: '2026-11-09T23:59:59Z', after: `Review/${id}`, limit: 1 }, signal());
    const url = new URL(request.mock.calls[0]?.[0] as string, 'https://example.test');
    expect(url.pathname).toBe('/api/projects/project-a/audit-log');
    expect(url.searchParams.getAll('kind')).toEqual(['Promotion', 'Review']);
    expect(url.searchParams.getAll('actor_kind')).toEqual(['unknown', 'human']);
    expect(url.searchParams.get('search')).toBe('name%_ &');
    expect(url.searchParams.get('after')).toBe(`Review/${id}`);
    expect(url.searchParams.get('limit')).toBe('1');
  });

  it('uses the same filters for the complete server export, never a paged read', async () => {
    const body = `${JSON.stringify(entry)}\n`;
    const request = stub(lines(body));
    const abort = signal();
    const blob = await auditClient('project-a').exportJsonLines({ kinds: ['Promotion'], actorKinds: ['unknown'], search: 'name' }, abort);
    expect(await blob.text()).toBe(body);
    expect(blob.type).toBe('application/x-ndjson');
    const [path, options] = request.mock.calls[0] as [string, RequestInit];
    const url = new URL(path, 'https://example.test');
    expect(url.pathname).toBe('/api/projects/project-a/audit-log/export');
    expect(url.searchParams.getAll('kind')).toEqual(['Promotion']);
    expect(url.searchParams.getAll('actor_kind')).toEqual(['unknown']);
    expect(url.searchParams.get('search')).toBe('name');
    expect(url.searchParams.has('after')).toBe(false);
    expect(url.searchParams.has('limit')).toBe(false);
    expect(options).toMatchObject({ signal: abort, credentials: 'same-origin', redirect: 'error', cache: 'no-store' });
  });

  it('accepts empty authorized pages and exports', async () => {
    stub(json(page([])));
    await expect(auditClient('project-a').query({}, signal())).resolves.toEqual(page([]));
    stub(lines(''));
    expect(await (await auditClient('project-a').exportJsonLines({}, signal())).text()).toBe('');
  });

  it.each([
    { kinds: [] }, { actorKinds: [] }, { kinds: ['Connection'] }, { actorKinds: ['person'] },
    { kinds: ['Review', 'Review', 'Review', 'Review'] }, { search: '' }, { search: 'x'.repeat(129) },
    { since: '2026-11-08' }, { through: 'not a date' },
    { since: '2026-11-09T00:00:00Z', through: '2026-11-08T00:00:00Z' },
    { limit: 0 }, { limit: 101 }, { limit: 1.5 }, { after: 'https://example.test' },
    { after: `Connection/${id}` }, { after: `Review/${id}/extra` }, { endpoint: '/elsewhere' },
  ])('rejects invalid or empty filters before accessing the network: %j', async (query) => {
    const request = stub(json(page()));
    await expect(auditClient('project-a').query(query as AuditReadQuery, signal())).rejects.toThrow('Audit records unavailable');
    expect(request).not.toHaveBeenCalled();
  });

  it.each(['', '.', '..', 'two projects', 'x'.repeat(129)])('rejects an invalid project key: %s', async (project) => {
    const request = stub(json(page()));
    await expect(auditClient(project).query({}, signal())).rejects.toThrow('Audit records unavailable');
    expect(request).not.toHaveBeenCalled();
  });

  it('encodes a project key as one path argument, never as URL authority or query', async () => {
    const project = 'project/other?x=1#fragment';
    const request = stub(json({ ...page(), project_id: project }));
    await auditClient(project).query({}, signal());
    expect(request.mock.calls[0]?.[0]).toBe('/api/projects/project%2Fother%3Fx%3D1%23fragment/audit-log?limit=50');
  });

  it.each([401, 403, 404, 409, 503])('redacts HTTP %s and never retries', async (status) => {
    const response = new Response('private provider failure', { status });
    const cancel = vi.spyOn(response.body!, 'cancel');
    const request = stub(response);
    await expect(auditClient('project-a').query({}, signal())).rejects.toThrow(/^Audit records unavailable$/);
    expect(request).toHaveBeenCalledTimes(1);
    expect(cancel).toHaveBeenCalledOnce();
  });

  it.each([
    page([entry], { project_id: 'foreign' }), page([entry], { private_payload: 'excluded' }),
    page([{ ...entry, actor_kind: 'person' }]), page([{ ...entry, actor_name: '' }]),
    page([{ ...entry, actor_id: 'x'.repeat(257) }]), page([{ ...entry, refused: 'false' }]),
    page([{ ...entry, revision: -1 }]), page([{ ...entry, revision: Number.MAX_SAFE_INTEGER + 1 }]),
    page([{ ...entry, at: 'yesterday' }]), page([{ ...entry, id: 'not-a-uuid' }]),
    page([{ ...entry, record_id: 'https://example.test' }]), page([{ ...entry, payload: 'excluded' }]),
    page([{ ...entry, kind: 'Connection' }]), page([{ ...entry, sentence: '' }]),
    page([entry, entry]), page([entry], { total: 0 }), page([], { total: -1 }),
    page([entry], { next_after: `Review/${id}`, total: 3 }),
    page([], { next_after: `Promotion/${id}`, total: 3 }),
    page([entry], { next_after: `Promotion/${id}`, total: 1 }),
  ])('rejects substituted or malformed metadata: %j', async (value) => {
    stub(json(value));
    await expect(auditClient('project-a').query({}, signal())).rejects.toThrow(/^Audit records unavailable$/);
  });

  it('rejects pages larger than the requested limit', async () => {
    stub(json(page([entry, { ...entry, kind: 'Review' }])));
    await expect(auditClient('project-a').query({ limit: 1 }, signal())).rejects.toThrow('Audit records unavailable');
  });

  it.each(['text/html', 'application/x-ndjson'])('rejects non-JSON page content: %s', async (type) => {
    stub(new Response(JSON.stringify(page()), { headers: { 'Content-Type': type } }));
    await expect(auditClient('project-a').query({}, signal())).rejects.toThrow('Audit records unavailable');
  });

  it('rejects malformed JSON without returning a parser snippet', async () => {
    stub(new Response('{private', { headers: { 'Content-Type': 'application/json' } }));
    await expect(auditClient('project-a').query({}, signal())).rejects.toThrow(/^Audit records unavailable$/);
  });

  it.each([
    JSON.stringify(entry), `${JSON.stringify(entry)}\n\n`, '{private\n',
    `${JSON.stringify({ ...entry, payload: 'excluded' })}\n`, `${JSON.stringify(entry)}\n${JSON.stringify(entry)}\n`,
  ])('rejects malformed, partial or duplicate exports: %s', async (body) => {
    stub(lines(body));
    await expect(auditClient('project-a').exportJsonLines({}, signal())).rejects.toThrow(/^Audit records unavailable$/);
  });

  it('rejects more than 1000 exported events without returning a partial file', async () => {
    const body = Array.from({ length: 1001 }, (_, index) => JSON.stringify({ ...entry, id: `00000000-0000-0000-0000-${String(index).padStart(12, '0')}` })).join('\n') + '\n';
    stub(lines(body));
    await expect(auditClient('project-a').exportJsonLines({}, signal())).rejects.toThrow('Audit records unavailable');
  });

  it('rejects cursor/limit injection into a complete export', async () => {
    const request = stub(lines(''));
    await expect(auditClient('project-a').exportJsonLines({ after: `Review/${id}` } as AuditReadQuery, signal())).rejects.toThrow('Audit records unavailable');
    expect(request).not.toHaveBeenCalled();
  });

  it('cancels an oversized stream before parsing or exposing its data', async () => {
    const cancel = vi.fn();
    stub(new Response(new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(256 * 1024 + 1)); }, cancel }), { headers: { 'Content-Type': 'application/json' } }));
    await expect(auditClient('project-a').query({}, signal())).rejects.toThrow(/^Audit records unavailable$/);
    expect(cancel).toHaveBeenCalledOnce();
  });

  it('rejects invalid UTF-8 rather than displaying replacement characters', async () => {
    stub(new Response(new Uint8Array([0xc3, 0x28]), { headers: { 'Content-Type': 'application/json' } }));
    await expect(auditClient('project-a').query({}, signal())).rejects.toThrow('Audit records unavailable');
  });

  it('does not issue an already-cancelled read', async () => {
    const request = stub(json(page()));
    const controller = new AbortController();
    controller.abort();
    await expect(auditClient('project-a').query({}, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(request).not.toHaveBeenCalled();
  });

  it('redacts transport failures and never retries', async () => {
    const request = vi.fn().mockRejectedValue(new Error('private transport diagnostic'));
    vi.stubGlobal('fetch', request);
    await expect(auditClient('project-a').query({}, signal())).rejects.toThrow(/^Audit records unavailable$/);
    expect(request).toHaveBeenCalledOnce();
  });

  it('discards a response cancelled after the request starts', async () => {
    const controller = new AbortController();
    const request = vi.fn().mockImplementation(() => {
      const response = json(page());
      controller.abort();
      return Promise.resolve(response);
    });
    vi.stubGlobal('fetch', request);
    await expect(auditClient('project-a').query({}, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(request).toHaveBeenCalledOnce();
  });
});
