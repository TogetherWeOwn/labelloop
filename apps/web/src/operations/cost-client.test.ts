import { afterEach, describe, expect, it, vi } from 'vitest';
import { costClient, type CostOperation, type CostReadQuery } from './cost-client.ts';

const entry: CostOperation = {
  operation_id: 'operation-1', iteration_id: 'iteration-1', month: '2026-11', price_version: 'prices-1',
  estimate: 100, actual: null, exposure: 150, state: 'unconfirmed', approved_by: null, reserved_autonomously: true,
};
const page = (operations: unknown[] = [entry], extra = {}) => ({
  project_id: 'project-a', currency: 'USD', unit: 'micro_usd', policy_revision: 1,
  policy: { monthly_limit: null, iteration_limit: 0, operation_limit: 0, autonomous_spend: false },
  pause_reason: null, operations, next_after: null, ...extra,
});
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json; charset=utf-8' } });
const signal = () => new AbortController().signal;
const stub = (response: Response) => {
  const request = vi.fn().mockResolvedValue(response);
  vi.stubGlobal('fetch', request);
  return request;
};
afterEach(() => vi.unstubAllGlobals());

describe('session-backed cost transport', () => {
  it('preserves actual null versus confirmed zero, child exposure, policy zero/null and pause metadata', async () => {
    const value = page([entry, { ...entry, operation_id: 'operation-2', state: 'settled', actual: 0, exposure: 0, approved_by: 'admin', reserved_autonomously: false }], { pause_reason: 'estimate_overrun' });
    const request = stub(json(value));
    const abort = signal();
    await expect(costClient('project-a').query({}, abort)).resolves.toEqual(value);
    expect(request).toHaveBeenCalledWith('/api/projects/project-a/costs?limit=50', {
      method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: abort, headers: { Accept: 'application/json' },
    });
  });

  it('encodes opaque operation cursors and project keys without choosing another endpoint', async () => {
    const project = 'scope/path?x=1#fragment';
    const after = 'operation/?&=日本語';
    const value = page([entry], { project_id: project, next_after: entry.operation_id });
    const request = stub(json(value));
    await expect(costClient(project).query({ after, limit: 1 }, signal())).resolves.toEqual(value);
    const url = new URL(request.mock.calls[0]?.[0] as string, 'https://example.test');
    expect(url.pathname).toBe('/api/projects/scope%2Fpath%3Fx%3D1%23fragment/costs');
    expect(url.searchParams.get('after')).toBe(after);
    expect(url.searchParams.get('limit')).toBe('1');
    expect(request).toHaveBeenCalledOnce();
  });

  it('returns one page only, without summing, filtering or claiming a policy revision is a snapshot', async () => {
    const request = vi.fn().mockResolvedValueOnce(json(page([entry], { next_after: entry.operation_id })))
      .mockResolvedValueOnce(json(page([], { policy_revision: 2 })));
    vi.stubGlobal('fetch', request);
    const client = costClient('project-a');
    const first = await client.query({ limit: 1 }, signal());
    expect(request).toHaveBeenCalledOnce();
    await expect(client.query({ after: first.next_after!, limit: 1 }, signal())).resolves.toEqual(page([], { policy_revision: 2 }));
    expect(request).toHaveBeenCalledTimes(2);
  });

  it.each(['reserved', 'dispatched', 'unconfirmed', 'insufficient_credits'] as const)('retains unconfirmed exposure for %s', async (state) => {
    const value = page([{ ...entry, state }]);
    stub(json(value));
    await expect(costClient('project-a').query({}, signal())).resolves.toEqual(value);
  });

  it('accepts cancelled rows without manufacturing billed cost', async () => {
    const value = page([{ ...entry, state: 'cancelled', exposure: 0 }]);
    stub(json(value));
    await expect(costClient('project-a').query({}, signal())).resolves.toEqual(value);
  });

  it.each([null, [], { limit: 0 }, { limit: 101 }, { limit: 1.5 }, { limit: '1' }, { after: '' }, { after: ' ' }, { after: 'x'.repeat(257) }, { role: 'admin' }, { endpoint: 'https://example.test' }])('rejects invalid queries before fetch: %j', async (query) => {
    const request = stub(json(page()));
    await expect(costClient('project-a').query(query as CostReadQuery, signal())).rejects.toThrow(/^Cost records unavailable$/);
    expect(request).not.toHaveBeenCalled();
  });

  it.each(['', '.', '..', 'two scopes', 'x'.repeat(129)])('rejects invalid scope %s before fetch', async (project) => {
    const request = stub(json(page()));
    await expect(costClient(project).query({}, signal())).rejects.toThrow(/^Cost records unavailable$/);
    expect(request).not.toHaveBeenCalled();
  });

  it.each([
    page([], { project_id: 'foreign' }), page([], { currency: 'EUR' }), page([], { unit: 'usd' }),
    page([], { policy_revision: 0 }), page([], { policy_revision: Number.MAX_SAFE_INTEGER + 1 }), page([], { pause_reason: 'refused' }),
    page([], { policy: { monthly_limit: -1, iteration_limit: null, operation_limit: null, autonomous_spend: false } }),
    page([], { policy: { monthly_limit: null, iteration_limit: null, operation_limit: null, autonomous_spend: 1 } }),
    page([], { policy: { monthly_limit: null, iteration_limit: null, operation_limit: null, autonomous_spend: false, private: 'excluded' } }),
    page([], { policy: null }), page([], { private_payload: 'excluded' }), page([entry, entry]), page([null]),
    ...[
      { operation_id: '' }, { iteration_id: ' ' }, { price_version: 'x'.repeat(257) }, { month: '2026-13' }, { month: '0000-01' },
      { estimate: -1 }, { estimate: Number.MAX_SAFE_INTEGER + 1 }, { actual: 1.5 }, { exposure: -1 }, { state: 'refused' },
      { approved_by: '' }, { reserved_autonomously: 'false' }, { reserved_autonomously: false }, { private_payload: 'excluded' },
      { actual: 0 }, { state: 'settled' }, { state: 'settled', actual: 0, exposure: 1 }, { exposure: 99 }, { state: 'cancelled', exposure: 1 },
    ].map((change) => page([{ ...entry, ...change }])),
    page([], { next_after: 'operation-1' }), page([entry], { next_after: 'different' }),
  ])('rejects malformed/substituted wire data: %j', async (value) => {
    stub(json(value));
    await expect(costClient('project-a').query({}, signal())).rejects.toThrow(/^Cost records unavailable$/);
  });

  it('rejects oversized pages, short nonterminal pages and cursor replay', async () => {
    for (const [value, query] of [
      [page([entry, { ...entry, operation_id: 'operation-2' }]), { limit: 1 }],
      [page([entry], { next_after: entry.operation_id }), { limit: 2 }],
      [page(), { after: entry.operation_id }],
    ] as const) {
      stub(json(value));
      await expect(costClient('project-a').query(query, signal())).rejects.toThrow(/^Cost records unavailable$/);
    }
  });

  it.each([401, 403, 404, 409, 503])('redacts HTTP %s, cancels the body and never retries', async (status) => {
    const response = new Response('private server diagnostic', { status });
    const cancel = vi.spyOn(response.body!, 'cancel');
    const request = stub(response);
    await expect(costClient('project-a').query({}, signal())).rejects.toThrow(/^Cost records unavailable$/);
    expect(cancel).toHaveBeenCalledOnce();
    expect(request).toHaveBeenCalledOnce();
  });

  it.each([
    () => new Response('{private', { headers: { 'Content-Type': 'application/json' } }),
    () => new Response(JSON.stringify(page()), { headers: { 'Content-Type': 'text/html' } }),
    () => new Response(new Uint8Array([0xc3, 0x28]), { headers: { 'Content-Type': 'application/json' } }),
    () => new Response(null, { headers: { 'Content-Type': 'application/json' } }),
  ])('redacts parser, content and UTF-8 failures', async (response) => {
    stub(response());
    await expect(costClient('project-a').query({}, signal())).rejects.toThrow(/^Cost records unavailable$/);
  });

  it('cancels oversized bodies before parsing or returning partial records', async () => {
    const cancel = vi.fn();
    stub(new Response(new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(256 * 1024 + 1)); }, cancel }), { headers: { 'Content-Type': 'application/json' } }));
    await expect(costClient('project-a').query({}, signal())).rejects.toThrow(/^Cost records unavailable$/);
    expect(cancel).toHaveBeenCalledOnce();
  });

  it('redacts transport errors without retries', async () => {
    const request = vi.fn().mockRejectedValue(new Error('private transport diagnostic'));
    vi.stubGlobal('fetch', request);
    await expect(costClient('project-a').query({}, signal())).rejects.toThrow(/^Cost records unavailable$/);
    expect(request).toHaveBeenCalledOnce();
  });

  it('does not fetch when cancelled and discards a cancelled response', async () => {
    const controller = new AbortController();
    const request = stub(json(page()));
    controller.abort();
    await expect(costClient('project-a').query({}, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(request).not.toHaveBeenCalled();
    const later = new AbortController();
    request.mockImplementation(() => { later.abort(); return Promise.resolve(json(page())); });
    await expect(costClient('project-a').query({}, later.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(request).toHaveBeenCalledOnce();
  });
});
