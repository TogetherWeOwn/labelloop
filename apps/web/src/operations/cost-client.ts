import type { BudgetPolicy } from './contracts.ts';
import { readSessionText } from './session-read.ts';

// Wire projection of budget_reads.BudgetPage, not the richer CostsRecord. Policy
// revisions are not ledger freshness; pages are independent consistent reads.
export interface CostOperation {
  operation_id: string;
  iteration_id: string;
  month: string;
  price_version: string;
  estimate: number;
  actual: number | null;
  exposure: number;
  state: 'reserved' | 'dispatched' | 'unconfirmed' | 'settled' | 'cancelled' | 'insufficient_credits';
  approved_by: string | null;
  reserved_autonomously: boolean;
}
export interface BudgetPage {
  project_id: string;
  currency: 'USD';
  unit: 'micro_usd';
  policy_revision: number;
  policy: BudgetPolicy;
  pause_reason: 'insufficient_credits' | 'estimate_overrun' | null;
  operations: CostOperation[];
  next_after: string | null;
}
export interface CostReadQuery {
  after?: string;
  limit?: number;
}

const unavailable = () => new Error('Cost records unavailable');
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const identity = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0 && [...value].length <= 256;
const month = (value: unknown): value is string => typeof value === 'string' && /^(?!0000)\d{4}-(?:0[1-9]|1[0-2])$/.test(value);
const operationKeys = ['operation_id', 'iteration_id', 'month', 'price_version', 'estimate', 'actual', 'exposure', 'state', 'approved_by', 'reserved_autonomously'];
const pageKeys = ['project_id', 'currency', 'unit', 'policy_revision', 'policy', 'pause_reason', 'operations', 'next_after'];
const policyKeys = ['monthly_limit', 'iteration_limit', 'operation_limit', 'autonomous_spend'];

function object(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function policy(value: unknown): value is BudgetPolicy {
  return object(value, policyKeys) && typeof value.autonomous_spend === 'boolean'
    && [value.monthly_limit, value.iteration_limit, value.operation_limit].every((amount) => amount === null || integer(amount));
}

function operation(value: unknown): value is CostOperation {
  if (!object(value, operationKeys) || !identity(value.operation_id) || !identity(value.iteration_id)
    || !month(value.month) || !identity(value.price_version) || !integer(value.estimate) || !integer(value.exposure)
    || !(value.actual === null || integer(value.actual)) || !(value.approved_by === null || identity(value.approved_by))
    || typeof value.reserved_autonomously !== 'boolean' || (!value.reserved_autonomously && value.approved_by === null)
    || !['reserved', 'dispatched', 'unconfirmed', 'settled', 'cancelled', 'insufficient_credits'].includes(value.state as string)
    || (value.state === 'settled') !== (value.actual !== null)) return false;
  // Check wire consistency without reconstructing hidden child exposure. An
  // unconfirmed entry can legitimately retain exposure greater than its estimate.
  return value.state === 'cancelled' ? value.exposure === 0
    : value.actual !== null ? value.exposure === value.actual : value.exposure >= value.estimate;
}

export function costClient(project: string) {
  return {
    async query(query: CostReadQuery, signal: AbortSignal): Promise<BudgetPage> {
      if (typeof project !== 'string' || project.length < 1 || [...project].length > 128 || /\s/.test(project) || project === '.' || project === '..'
        || typeof query !== 'object' || query === null || Array.isArray(query)
        || Object.keys(query).some((key) => key !== 'after' && key !== 'limit')
        || (query.after !== undefined && !identity(query.after))
        || (query.limit !== undefined && (!integer(query.limit) || query.limit < 1 || query.limit > 100))) throw unavailable();
      const params = new URLSearchParams({ limit: String(query.limit ?? 50) });
      if (query.after !== undefined) params.set('after', query.after);
      const body = await readSessionText(`/api/projects/${encodeURIComponent(project)}/costs?${params}`, signal, 'application/json', 256 * 1024, unavailable);
      let value: unknown;
      try { value = JSON.parse(body); } catch { throw unavailable(); }
      if (!object(value, pageKeys) || value.project_id !== project || value.currency !== 'USD' || value.unit !== 'micro_usd'
        || !integer(value.policy_revision) || value.policy_revision < 1 || !policy(value.policy)
        || (value.pause_reason !== null && value.pause_reason !== 'insufficient_credits' && value.pause_reason !== 'estimate_overrun')
        || !Array.isArray(value.operations) || value.operations.length > (query.limit ?? 50) || !value.operations.every(operation)
        || new Set(value.operations.map((entry: CostOperation) => entry.operation_id)).size !== value.operations.length
        || value.operations.some((entry: CostOperation) => entry.operation_id === query.after)
        || !(value.next_after === null || identity(value.next_after))) throw unavailable();
      if (value.next_after !== null && (value.operations.length !== (query.limit ?? 50) || value.next_after !== value.operations.at(-1)?.operation_id)) throw unavailable();
      return value as unknown as BudgetPage;
    },
  };
}
