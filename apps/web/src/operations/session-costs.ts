import type { CostsRecord } from './contracts.ts';
import { costClient, type CostOperation, type CostReadQuery, type CostSummaryQuery } from './cost-client.ts';

// Loaded pages are independent reads, never a reconstructed ledger snapshot.
export function appendSessionCosts(previous: CostsRecord, next: CostsRecord): CostsRecord {
  if (previous.source !== 'session' || next.source !== 'session'
    || previous.project_id !== next.project_id || previous.month !== next.month || previous.iteration_id !== next.iteration_id
    || previous.revision !== next.revision || previous.pause_reason !== next.pause_reason
    || previous.policy.monthly_limit !== next.policy.monthly_limit || previous.policy.iteration_limit !== next.policy.iteration_limit
    || previous.policy.operation_limit !== next.policy.operation_limit || previous.policy.autonomous_spend !== next.policy.autonomous_spend
    || previous.next_after === null) throw new Error('Cost records unavailable');
  const entries = [...previous.entries, ...next.entries];
  if (entries.length > 10_000 || new Set(entries.map((entry) => entry.operation_id)).size !== entries.length) throw new Error('Cost records unavailable');
  return { ...next, entries };
}

const results: Record<CostOperation['state'], string> = {
  reserved: 'Reserved', dispatched: 'Dispatched', unconfirmed: 'Billing unconfirmed',
  settled: 'Billing confirmed', cancelled: 'Cancelled', insufficient_credits: 'Paid steps paused: insufficient credits',
};

// Both reads are authorized and validated by the existing fixed-route client.
// Matching policy is not a ledger snapshot: no cross-read/page total is inferred.
export async function sessionCostsRecord(project: string, summaryQuery: CostSummaryQuery, pageQuery: CostReadQuery, signal: AbortSignal): Promise<Extract<CostsRecord, { source: 'session' }>> {
  const client = costClient(project);
  const summary = await client.summary(summaryQuery, signal);
  const page = await client.query(pageQuery, signal);
  if (summary.policy_revision !== page.policy_revision || summary.pause_reason !== page.pause_reason
    || summary.policy.monthly_limit !== page.policy.monthly_limit
    || summary.policy.iteration_limit !== page.policy.iteration_limit
    || summary.policy.operation_limit !== page.policy.operation_limit
    || summary.policy.autonomous_spend !== page.policy.autonomous_spend) throw new Error('Cost records unavailable');
  return {
    source: 'session', project_id: project, revision: summary.policy_revision,
    month: summary.month, iteration_id: summary.iteration_id, price_version: null,
    policy: summary.policy, pause_reason: summary.pause_reason,
    summaries: { monthly: summary.monthly, iteration: summary.iteration }, next_after: page.next_after,
    entries: page.operations.map((entry) => ({
      operation_id: entry.operation_id, project_id: project, iteration_id: entry.iteration_id,
      object_id: entry.annotation_job_id, recipe_id: null, month: entry.month, at: entry.reserved_at,
      kind: entry.kind, approved_by: entry.approved_by, reserved_autonomously: entry.reserved_autonomously, price_version: entry.price_version,
      estimate: entry.estimate, actual: entry.actual, exposure: entry.exposure, state: entry.state,
      result: entry.review_pause === 'approval_exhausted' ? `${results[entry.state]} · review paused: approval exhausted` : results[entry.state],
    })),
  };
}
