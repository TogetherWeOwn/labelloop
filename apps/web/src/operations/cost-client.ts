import type { BudgetPolicy, CostTotals } from './contracts.ts';
import { readSessionText } from './session-read.ts';
export type { CostTotals } from './contracts.ts';

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
  reserved_at: string | null;
  kind: 'LLM review' | null;
  annotation_job_id: string | null;
  review_pause: 'approval_exhausted' | null;
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
export interface BudgetExport extends BudgetPage {
  next_after: null;
}
export interface CostReadQuery {
  after?: string;
  limit?: number;
}
export interface CostSummaryQuery {
  month: string;
  iteration_id: string;
}
export interface BudgetSummary extends Omit<BudgetPage, 'operations' | 'next_after'>, CostSummaryQuery {
  monthly: CostTotals;
  iteration: CostTotals;
}

const unavailable = () => new Error('Cost records unavailable');
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const identity = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0 && [...value].length <= 256;
const month = (value: unknown): value is string => typeof value === 'string' && /^(?!0000)\d{4}-(?:0[1-9]|1[0-2])$/.test(value);
const operationKeys = ['operation_id', 'iteration_id', 'month', 'price_version', 'estimate', 'actual', 'exposure', 'state', 'approved_by', 'reserved_autonomously', 'reserved_at', 'kind', 'annotation_job_id', 'review_pause'];
const pageKeys = ['project_id', 'currency', 'unit', 'policy_revision', 'policy', 'pause_reason', 'operations', 'next_after'];
const policyKeys = ['monthly_limit', 'iteration_limit', 'operation_limit', 'autonomous_spend'];
const summaryKeys = ['project_id', 'currency', 'unit', 'policy_revision', 'policy', 'pause_reason', 'month', 'iteration_id', 'monthly', 'iteration'];
const totalKeys = ['operation_count', 'actual', 'confirmed_estimate', 'reserved', 'unconfirmed', 'exposure', 'delta'];
const projectKey = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && [...value].length <= 128 && !/\s/.test(value) && value !== '.' && value !== '..';

function object(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function policy(value: unknown): value is BudgetPolicy {
  return object(value, policyKeys) && typeof value.autonomous_spend === 'boolean'
    && [value.monthly_limit, value.iteration_limit, value.operation_limit].every((amount) => amount === null || integer(amount));
}

function reservationTime(value: unknown): boolean {
  if (value === null) return true;
  if (typeof value !== 'string' || !/^(?!0000)\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value)) return false;
  const instant = Date.parse(value);
  return Number.isFinite(instant) && new Date(instant).toISOString().slice(0, 19) === value.slice(0, 19);
}

function operation(value: unknown): value is CostOperation {
  if (!object(value, operationKeys) || (value.kind !== null && value.kind !== 'LLM review')
    || !(value.annotation_job_id === null || (value.kind === 'LLM review' && identity(value.annotation_job_id)))
    || !(value.review_pause === null || (value.review_pause === 'approval_exhausted' && value.kind === 'LLM review' && identity(value.annotation_job_id)))
    || !reservationTime(value.reserved_at) || !identity(value.operation_id) || !identity(value.iteration_id)
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

function totals(value: unknown): value is CostTotals {
  if (!object(value, totalKeys) || !integer(value.operation_count) || value.operation_count > 10_000
    || ![value.actual, value.confirmed_estimate, value.reserved, value.unconfirmed, value.exposure].every(integer)
    || typeof value.delta !== 'number' || !Number.isSafeInteger(value.delta)) return false;
  const actual = value.actual as number, estimate = value.confirmed_estimate as number;
  const reserved = value.reserved as number, unconfirmed = value.unconfirmed as number, exposure = value.exposure as number;
  if (BigInt(exposure) !== BigInt(actual) + BigInt(reserved) + BigInt(unconfirmed)
    || BigInt(value.delta) !== BigInt(actual) - BigInt(estimate)) return false;
  return value.operation_count !== 0 || [actual, estimate, reserved, unconfirmed, exposure, value.delta].every((amount) => amount === 0);
}

function budgetPage(value: unknown, project: string, limit: number): value is BudgetPage {
  return object(value, pageKeys) && value.project_id === project && value.currency === 'USD' && value.unit === 'micro_usd'
    && integer(value.policy_revision) && value.policy_revision >= 1 && policy(value.policy)
    && (value.pause_reason === null || value.pause_reason === 'insufficient_credits' || value.pause_reason === 'estimate_overrun')
    && Array.isArray(value.operations) && value.operations.length <= limit && value.operations.every(operation)
    && new Set(value.operations.map((entry: CostOperation) => entry.operation_id)).size === value.operations.length
    && (value.next_after === null || identity(value.next_after));
}

export function costClient(project: string) {
  return {
    async exportJson(signal: AbortSignal): Promise<Blob> {
      if (!projectKey(project)) throw unavailable();
      // One complete server read, not an export assembled from mutable pages.
      // Buffer and validate the entire response before creating a download.
      const body = await readSessionText(`/api/projects/${encodeURIComponent(project)}/costs/export`, signal, 'application/json', 16 * 1024 * 1024, unavailable);
      let value: unknown;
      try { value = JSON.parse(body); } catch { throw unavailable(); }
      if (!budgetPage(value, project, 10_000) || value.next_after !== null) throw unavailable();
      return new Blob([JSON.stringify(value)], { type: 'application/json;charset=utf-8' });
    },
    async summary(query: CostSummaryQuery, signal: AbortSignal): Promise<BudgetSummary> {
      if (!projectKey(project) || !object(query, ['month', 'iteration_id']) || !month(query.month) || !identity(query.iteration_id)) throw unavailable();
      const params = new URLSearchParams({ month: query.month, iteration_id: query.iteration_id });
      const body = await readSessionText(`/api/projects/${encodeURIComponent(project)}/costs/summary?${params}`, signal, 'application/json', 64 * 1024, unavailable);
      let value: unknown;
      try { value = JSON.parse(body); } catch { throw unavailable(); }
      if (!object(value, summaryKeys) || value.project_id !== project || value.currency !== 'USD' || value.unit !== 'micro_usd'
        || !integer(value.policy_revision) || value.policy_revision < 1 || !policy(value.policy)
        || (value.pause_reason !== null && value.pause_reason !== 'insufficient_credits' && value.pause_reason !== 'estimate_overrun')
        || value.month !== query.month || value.iteration_id !== query.iteration_id || !totals(value.monthly) || !totals(value.iteration)) throw unavailable();
      return value as unknown as BudgetSummary;
    },
    async query(query: CostReadQuery, signal: AbortSignal): Promise<BudgetPage> {
      if (!projectKey(project)
        || typeof query !== 'object' || query === null || Array.isArray(query)
        || Object.keys(query).some((key) => key !== 'after' && key !== 'limit')
        || (query.after !== undefined && !identity(query.after))
        || (query.limit !== undefined && (!integer(query.limit) || query.limit < 1 || query.limit > 100))) throw unavailable();
      const params = new URLSearchParams({ limit: String(query.limit ?? 50) });
      if (query.after !== undefined) params.set('after', query.after);
      const body = await readSessionText(`/api/projects/${encodeURIComponent(project)}/costs?${params}`, signal, 'application/json', 256 * 1024, unavailable);
      let value: unknown;
      try { value = JSON.parse(body); } catch { throw unavailable(); }
      if (!budgetPage(value, project, query.limit ?? 50)
        || value.operations.some((entry) => entry.operation_id === query.after)) throw unavailable();
      if (value.next_after !== null && (value.operations.length !== (query.limit ?? 50) || value.next_after !== value.operations.at(-1)?.operation_id)) throw unavailable();
      return value;
    },
  };
}
