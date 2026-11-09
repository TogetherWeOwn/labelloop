// Session-backed transport for audit_reads.py. Keep wire provenance intact;
// the display adapter must not infer people, successful outcomes or record URLs.
export type RecordedAuditKind = 'Review' | 'Promotion' | 'Budget';
export type RecordedActorKind = 'human' | 'model' | 'system' | 'service' | 'unknown';
export interface AuditMetadata {
  id: string;
  at: string;
  kind: RecordedAuditKind;
  actor_id: string;
  actor_kind: RecordedActorKind;
  actor_name: string | null;
  record_id: string;
  revision: number;
  sentence: string;
  refused: boolean | null;
}
export interface AuditMetadataPage {
  project_id: string;
  events: AuditMetadata[];
  total: number;
  next_after: string | null;
}
export interface AuditFilters {
  kinds?: readonly RecordedAuditKind[];
  actorKinds?: readonly RecordedActorKind[];
  search?: string;
  since?: string;
  through?: string;
}
export interface AuditReadQuery extends AuditFilters {
  after?: string;
  limit?: number;
}

const kinds: readonly string[] = ['Review', 'Promotion', 'Budget'];
const actorKinds: readonly string[] = ['human', 'model', 'system', 'service', 'unknown'];
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const eventKeys = ['id', 'at', 'kind', 'actor_id', 'actor_kind', 'actor_name', 'record_id', 'revision', 'sentence', 'refused'];
const pageKeys = ['project_id', 'events', 'total', 'next_after'];
const unavailable = () => new Error('Audit records unavailable');
const text = (value: unknown, max: number): value is string => typeof value === 'string' && [...value].length >= 1 && [...value].length <= max;
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const timestamp = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));

function object(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function cursor(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  const [kind, id, extra] = value.split('/');
  return kinds.includes(kind ?? '') && uuid.test(id ?? '') && extra === undefined;
}

function event(value: unknown): value is AuditMetadata {
  return object(value, eventKeys) && typeof value.id === 'string' && uuid.test(value.id)
    && timestamp(value.at) && kinds.includes(value.kind as string) && text(value.actor_id, 256)
    && actorKinds.includes(value.actor_kind as string) && (value.actor_name === null || text(value.actor_name, 128))
    && typeof value.record_id === 'string' && uuid.test(value.record_id) && integer(value.revision)
    && text(value.sentence, 512) && (value.refused === null || typeof value.refused === 'boolean');
}

function distinct(events: readonly AuditMetadata[]): boolean {
  return new Set(events.map((entry) => `${entry.kind}/${entry.id.toLowerCase()}`)).size === events.length;
}

function parameters(query: AuditReadQuery, exporting: boolean): URLSearchParams {
  const allowed = ['kinds', 'actorKinds', 'search', 'since', 'through', ...(exporting ? [] : ['after', 'limit'])];
  if (Object.keys(query).some((key) => !allowed.includes(key))) throw unavailable();
  const result = new URLSearchParams();
  for (const [name, selected, choices] of [['kind', query.kinds, kinds], ['actor_kind', query.actorKinds, actorKinds]] as const) {
    if (selected === undefined) continue;
    // Empty selection is not omission: omission would silently select all events.
    if (!Array.isArray(selected) || selected.length < 1 || selected.length > choices.length || selected.some((value: unknown) => typeof value !== 'string' || !choices.includes(value))) throw unavailable();
    for (const value of new Set<string>(selected)) result.append(name, value);
  }
  if (query.search !== undefined) {
    if (!text(query.search, 128)) throw unavailable();
    result.set('search', query.search);
  }
  for (const [name, value] of [['since', query.since], ['through', query.through]] as const) {
    if (value === undefined) continue;
    if (!timestamp(value)) throw unavailable();
    result.set(name, value);
  }
  if (query.since !== undefined && query.through !== undefined && Date.parse(query.since) > Date.parse(query.through)) throw unavailable();
  if (!exporting) {
    if (query.after !== undefined) {
      if (!cursor(query.after)) throw unavailable();
      result.set('after', query.after);
    }
    if (query.limit !== undefined && (!integer(query.limit) || query.limit < 1 || query.limit > 100)) throw unavailable();
    result.set('limit', String(query.limit ?? 50));
  }
  return result;
}

async function read(project: string, query: AuditReadQuery, signal: AbortSignal, exporting: boolean): Promise<string> {
  if (!text(project, 128) || /\s/.test(project) || project === '.' || project === '..') throw unavailable();
  const params = parameters(query, exporting);
  const path = `/api/projects/${encodeURIComponent(project)}/audit-log${exporting ? '/export' : ''}`;
  signal.throwIfAborted();
  let response: Response;
  try {
    response = await fetch(`${path}?${params}`, {
      method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal,
      headers: { Accept: exporting ? 'application/x-ndjson' : 'application/json' },
    });
  } catch {
    signal.throwIfAborted();
    throw unavailable();
  }
  if (!response.ok) {
    await response.body?.cancel().catch(() => undefined);
    throw unavailable();
  }
  const mediaType = response.headers.get('Content-Type')?.split(';')[0]?.trim().toLowerCase();
  if (mediaType !== (exporting ? 'application/x-ndjson' : 'application/json') || response.body === null) {
    await response.body?.cancel().catch(() => undefined);
    throw unavailable();
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  const max = exporting ? 2 * 1024 * 1024 : 256 * 1024;
  let bytes = 0;
  let body = '';
  try {
    while (true) {
      signal.throwIfAborted();
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > max) throw unavailable();
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

export function auditClient(project: string) {
  return {
    async query(query: AuditReadQuery, signal: AbortSignal): Promise<AuditMetadataPage> {
      const body = await read(project, query, signal, false);
      let value: unknown;
      try { value = JSON.parse(body); } catch { throw unavailable(); }
      if (!object(value, pageKeys) || value.project_id !== project || !Array.isArray(value.events)
        || value.events.length > (query.limit ?? 50) || !value.events.every(event) || !distinct(value.events)
        || !integer(value.total) || value.total < value.events.length
        || !(value.next_after === null || cursor(value.next_after))) throw unavailable();
      if (value.next_after !== null) {
        const last = value.events.at(-1);
        if (!last || value.next_after !== `${last.kind}/${last.id}` || value.total <= value.events.length) throw unavailable();
      }
      return value as unknown as AuditMetadataPage;
    },
    async exportJsonLines(query: AuditFilters, signal: AbortSignal): Promise<Blob> {
      const body = await read(project, query, signal, true);
      if (body !== '' && !body.endsWith('\n')) throw unavailable();
      let values: unknown[];
      try { values = body === '' ? [] : body.slice(0, -1).split('\n').map((line) => JSON.parse(line) as unknown); }
      catch { throw unavailable(); }
      if (values.length > 1000 || !values.every(event) || !distinct(values)) throw unavailable();
      return new Blob([body], { type: 'application/x-ndjson' });
    },
  };
}
