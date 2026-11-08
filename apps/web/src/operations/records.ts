import type { BudgetPolicy, CostEntry, CostScope, CostsRecord } from './contracts.ts';

export function usd(microdollars: number): string {
  if (!Number.isSafeInteger(microdollars)) throw new Error('Invalid money projection');
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 6 }).format(microdollars / 1_000_000);
}

export function parseLimit(value: string): number | null {
  if (!value.trim()) return null;
  if (!/^\d+(\.\d{1,6})?$/.test(value)) throw new Error('Use a nonnegative USD amount with up to six decimal places.');
  const [whole, fraction = ''] = value.split('.');
  const amount = Number(whole) * 1_000_000 + Number(fraction.padEnd(6, '0'));
  if (!Number.isSafeInteger(amount)) throw new Error('That amount is too large.');
  return amount;
}

export function policyComplete(policy: BudgetPolicy): boolean {
  return policy.monthly_limit !== null && policy.iteration_limit !== null && policy.operation_limit !== null;
}

export function scopedEntries(record: CostsRecord, scope: CostScope): readonly CostEntry[] {
  return record.entries.filter((entry) => entry.project_id === record.project_id && (
    scope.kind === 'project' ? scope.id === entry.project_id :
    scope.kind === 'iteration' ? scope.id === entry.iteration_id : scope.id === entry.object_id
  )).sort((a, b) => b.at.localeCompare(a.at) || a.operation_id.localeCompare(b.operation_id));
}

export function totals(entries: readonly CostEntry[]) {
  let actual = 0, reserved = 0, unconfirmed = 0, delta = 0;
  for (const entry of entries) {
    if (entry.state === 'settled') {
      if (entry.actual === null) throw new Error('Settled entry requires confirmed billing');
      actual += entry.actual;
      delta += entry.actual - entry.estimate;
    } else if (entry.state !== 'cancelled' && entry.state !== 'refused') {
      if (entry.state === 'unconfirmed') unconfirmed += entry.estimate;
      else reserved += entry.estimate;
    }
  }
  return { actual, reserved, unconfirmed, delta, exposure: actual + reserved + unconfirmed };
}

// Neutralize spreadsheet formulas as well as CSV separators, quotes and newlines.
export function ledgerCsv(entries: readonly CostEntry[]): string {
  const cell = (value: string) => `"${(/^\s*[=+@-]/.test(value) ? "'" : '') + value.replaceAll('"', '""')}"`;
  return [['UTC', 'Operation', 'Kind', 'Object', 'Recipe', 'Approved by', 'Estimate USD', 'Actual USD', 'State', 'Result'],
    ...entries.map((e) => [e.at, e.operation_id, e.kind, e.object_id, e.recipe_id ?? '', e.approved_by ?? 'Refused',
      usd(e.estimate), e.state === 'settled' && e.actual !== null ? usd(e.actual) : 'Unconfirmed', e.state, e.result]),
  ].map((row) => row.map(cell).join(',')).join('\r\n');
}

export function download(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  // A later task lets the browser consume the URL before it is revoked.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
