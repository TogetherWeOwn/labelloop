import { afterEach, describe, expect, it, vi } from 'vitest';
import { costClient, type BudgetLimitUpdate } from './cost-client.ts';

const csrf = 'fixture-csrf-marker';
const update = (): BudgetLimitUpdate => ({
  expected_revision: 7,
  policy: { monthly_limit: null, iteration_limit: 0, operation_limit: 1_000_000, autonomous_spend: false },
});
const receipt = { project_id: 'project-a', policy_revision: 8 };
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json; charset=utf-8' } });
const signal = () => new AbortController().signal;
const unconfirmed = /^Cost change not confirmed\. Refresh records before trying again\.$/;
const stub = (response = json(receipt)) => {
  const request = vi.fn().mockResolvedValue(response);
  vi.stubGlobal('fetch', request);
  return request;
};
afterEach(() => vi.unstubAllGlobals());

describe('session-backed cost limit command', () => {
  it.each([false, true])('posts exact limits and revision with CSRF, autonomous=%s, without granting resume or dispatch', async (autonomous) => {
    const value = update();
    value.policy.autonomous_spend = autonomous;
    const request = stub();
    const abort = signal();
    await expect(costClient('project-a').setLimits(value, csrf, abort)).resolves.toEqual(receipt);
    expect(request).toHaveBeenCalledExactlyOnceWith('/api/projects/project-a/costs/policy', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: abort,
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(value),
    });
  });

  it('encodes project identity on the fixed policy route', async () => {
    const project = 'scope/?日本語';
    const request = stub(json({ ...receipt, project_id: project }));
    await costClient(project).setLimits(update(), csrf, signal());
    expect(request.mock.calls[0]?.[0]).toBe(`/api/projects/${encodeURIComponent(project)}/costs/policy`);
  });

  it('binds the receipt to the revision actually sent, not a subsequently changed caller object', async () => {
    const value = update();
    const request = stub();
    const result = costClient('project-a').setLimits(value, csrf, signal());
    value.expected_revision = 99;
    await expect(result).resolves.toEqual(receipt);
    expect((request.mock.calls[0]?.[1] as RequestInit).body).toBe(JSON.stringify(update()));
  });

  it.each(['', '.', '..', 'two scopes', 'x'.repeat(129)])('rejects invalid project %s before posting', async (project) => {
    const request = stub();
    await expect(costClient(project).setLimits(update(), csrf, signal())).rejects.toThrow(unconfirmed);
    expect(request).not.toHaveBeenCalled();
  });

  it.each([null, [], {}, { policy: update().policy },
    ...[0, -1, true, '7', 1.5, Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER + 1].map((expected_revision) => ({ ...update(), expected_revision })),
    ...['actor', 'endpoint', 'role', 'resume', 'prices', 'allowed_providers', 'qualification'].map((key) => ({ ...update(), [key]: 'not-authority' })),
    ...[null, [], {}, { ...update().policy, autonomous_spend: 1 }, { ...update().policy, private: 'excluded' },
      ...[-1, 1.5, true, '0', Number.MAX_SAFE_INTEGER + 1].map((monthly_limit) => ({ ...update().policy, monthly_limit })),
    ].map((policy) => ({ ...update(), policy })),
  ])('rejects malformed or authority-bearing request before posting: %j', async (value) => {
    const request = stub();
    await expect(costClient('project-a').setLimits(value as BudgetLimitUpdate, csrf, signal())).rejects.toThrow(unconfirmed);
    expect(request).not.toHaveBeenCalled();
  });

  it.each(['', 'two tokens', 'bad\r\nheader', '日本語', 'x'.repeat(257), null])('rejects missing or unsafe CSRF before posting', async (token) => {
    const request = stub();
    await expect(costClient('project-a').setLimits(update(), token as string, signal())).rejects.toThrow(unconfirmed);
    expect(request).not.toHaveBeenCalled();
  });

  it.each([401, 403, 404, 409, 422, 503])('does not retry or expose HTTP %s diagnostics', async (status) => {
    const response = new Response('private diagnostic', { status });
    const cancel = vi.spyOn(response.body!, 'cancel');
    const request = stub(response);
    await expect(costClient('project-a').setLimits(update(), csrf, signal())).rejects.toThrow(unconfirmed);
    expect(cancel).toHaveBeenCalledOnce();
    expect(request).toHaveBeenCalledOnce();
  });

  it.each([null, [], {}, { ...receipt, project_id: 'foreign' }, { ...receipt, policy_revision: 7 },
    { ...receipt, policy_revision: 9 }, { ...receipt, policy_revision: true }, { ...receipt, policy_revision: '8' },
    { ...receipt, private: 'excluded' }, { ...receipt, policy: update().policy },
  ])('treats malformed or substituted receipts as unconfirmed, never retrying: %j', async (value) => {
    const request = stub(json(value));
    await expect(costClient('project-a').setLimits(update(), csrf, signal())).rejects.toThrow(unconfirmed);
    expect(request).toHaveBeenCalledOnce();
  });

  it.each([
    () => new Response('{private', { headers: { 'Content-Type': 'application/json' } }),
    () => new Response(JSON.stringify(receipt), { headers: { 'Content-Type': 'text/html' } }),
    () => new Response(new Uint8Array([0xc3, 0x28]), { headers: { 'Content-Type': 'application/json' } }),
    () => new Response(null, { headers: { 'Content-Type': 'application/json' } }),
    () => new Response(' '.repeat(4097) + JSON.stringify(receipt), { headers: { 'Content-Type': 'application/json' } }),
    () => new Response(new ReadableStream({ start(controller) { controller.error(new Error('private stream diagnostic')); } }), { headers: { 'Content-Type': 'application/json' } }),
  ])('fails closed on content type, byte bound, encoding or stream failure', async (response) => {
    const request = stub(response());
    await expect(costClient('project-a').setLimits(update(), csrf, signal())).rejects.toThrow(unconfirmed);
    expect(request).toHaveBeenCalledOnce();
  });

  it('does not retry an uncertain network outcome, including a refused redirect', async () => {
    const request = vi.fn().mockRejectedValue(new Error('private transport diagnostic'));
    vi.stubGlobal('fetch', request);
    await expect(costClient('project-a').setLimits(update(), csrf, signal())).rejects.toThrow(unconfirmed);
    expect(request).toHaveBeenCalledOnce();
    expect((request.mock.calls[0]?.[1] as RequestInit).redirect).toBe('error');
  });

  it('does not post after cancellation and does not treat cancellation after dispatch as confirmation', async () => {
    const before = new AbortController();
    before.abort();
    const request = stub();
    await expect(costClient('project-a').setLimits(update(), csrf, before.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(request).not.toHaveBeenCalled();
    const after = new AbortController();
    request.mockImplementation(() => { after.abort(); return Promise.resolve(json(receipt)); });
    await expect(costClient('project-a').setLimits(update(), csrf, after.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(request).toHaveBeenCalledOnce();
  });
});
