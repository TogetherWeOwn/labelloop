import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from './App.tsx';
import { fetchHealth } from './health.ts';

afterEach(() => vi.unstubAllGlobals());

describe('web bootstrap', () => {
  it('exports the application component', () => {
    expect(App.name).toBe('App');
  });

  it('passes the abort signal to the health request', async () => {
    const signal = new AbortController().signal;
    const request = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: 'ok' }),
    });
    vi.stubGlobal('fetch', request);
    await expect(fetchHealth(signal)).resolves.toBe('ok');
    expect(request).toHaveBeenCalledWith('/api/healthz', { signal });
  });

  it('distinguishes an HTTP failure from cancellation', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));
    const controller = new AbortController();
    controller.abort();
    await expect(fetchHealth(controller.signal)).rejects.toThrow('API unavailable');
  });

  it('propagates request cancellation unchanged', async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new DOMException('Cancelled', 'AbortError')),
    );
    controller.abort();
    await expect(fetchHealth(controller.signal)).rejects.toMatchObject({
      name: 'AbortError',
    });
  });
});
