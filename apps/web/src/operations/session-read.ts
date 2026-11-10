// Callers own fixed same-origin paths, media types and byte bounds. This helper
// never retries failures or returns server/transport diagnostics to the view.
export async function readSessionText(path: string, signal: AbortSignal, mediaType: string, maxBytes: number, unavailable: () => Error): Promise<string> {
  signal.throwIfAborted();
  let response: Response;
  try {
    response = await fetch(path, {
      method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal,
      headers: { Accept: mediaType },
    });
  } catch {
    signal.throwIfAborted();
    throw unavailable();
  }
  return readSessionResponse(response, signal, mediaType, maxBytes, unavailable);
}

// Writes share bounded response decoding, not the GET/retry contract. A cancelled
// or unconfirmed mutation may already have committed; callers must reconcile it.
export async function readSessionResponse(response: Response, signal: AbortSignal, mediaType: string, maxBytes: number, unavailable: () => Error): Promise<string> {
  if (!response.ok || response.headers.get('Content-Type')?.split(';')[0]?.trim().toLowerCase() !== mediaType || response.body === null) {
    await response.body?.cancel().catch(() => undefined);
    signal.throwIfAborted();
    throw unavailable();
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let bytes = 0;
  let body = '';
  try {
    while (true) {
      signal.throwIfAborted();
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > maxBytes) throw unavailable();
      body += decoder.decode(value, { stream: true });
    }
    body += decoder.decode();
    signal.throwIfAborted();
    return body;
  } catch {
    await reader.cancel().catch(() => undefined);
    signal.throwIfAborted();
    throw unavailable();
  } finally {
    reader.releaseLock();
  }
}
