import { auditClient, type AuditFilters, type RecordedActorKind, type RecordedAuditKind } from './audit-client.ts';
import type { AuditPort, AuditQuery } from './contracts.ts';

const unavailable = () => new Error('Audit records unavailable');

function day(value: string, end: boolean): string | undefined {
  if (value === '') return undefined;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw unavailable();
  const start = `${value}T00:00:00Z`;
  if (!Number.isFinite(Date.parse(start)) || new Date(start).toISOString().slice(0, 10) !== value) throw unavailable();
  return end ? `${value}T23:59:59.999999Z` : start;
}

function filters(query: AuditQuery): AuditFilters {
  if (!Array.isArray(query.kinds) || query.kinds.length < 1 || query.kinds.length > 4
    || query.kinds.some((kind) => !['Review', 'Promotion', 'Connection', 'Budget'].includes(kind))
    || !Array.isArray(query.principals) || query.principals.length < 1 || query.principals.length > 2
    || query.principals.some((kind) => kind !== 'person' && kind !== 'service')) throw unavailable();
  // No connection journal exists in this reader. Do not manufacture empty history.
  const kinds = [...new Set(query.kinds.filter((kind): kind is RecordedAuditKind => kind !== 'Connection'))];
  if (kinds.length === 0) throw unavailable();
  const people = query.principals.includes('person');
  const services = query.principals.includes('service');
  // Both categories mean no actor restriction, including unclassified history.
  // The display category does not replace each event's exact recorded actor kind.
  const actorKinds: readonly RecordedActorKind[] | undefined = people && services ? undefined : people ? ['human'] : ['model', 'system', 'service'];
  return { kinds, actorKinds, search: query.search === '' ? undefined : query.search, since: day(query.from, false), through: day(query.through, true) };
}

// Keep this port stable for one project/session lifetime. The authenticated shell
// must replace it on scope changes; role props never authorize reads or exports.
export function sessionAuditPort(project: string): AuditPort {
  const client = auditClient(project);
  return {
    async query(query, cursor, signal) {
      const page = await client.query({ ...filters(query), after: cursor ?? undefined, limit: 50 }, signal);
      return {
        events: page.events.map((event) => ({
          id: `${event.kind}/${event.id}`, at: event.at, kind: event.kind,
          principal: {
            id: event.actor_id, name: event.actor_name ?? event.actor_id,
            type: event.actor_kind === 'unknown' ? 'unknown' as const : event.actor_kind === 'human' ? 'person' as const : 'service' as const,
            recorded_kind: event.actor_kind,
          },
          sentence: event.sentence, refused: event.refused, record: null,
        })),
        total: page.total, next_cursor: page.next_after,
      };
    },
    exportJsonLines(query, signal) {
      return client.exportJsonLines(filters(query), signal);
    },
  };
}
