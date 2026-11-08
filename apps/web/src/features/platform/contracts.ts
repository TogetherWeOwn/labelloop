// Read models only. Authentication and command/ledger integration belong to the shell.
export type ViewerRole = 'admin' | 'reviewer';
export type Fingerprint = Readonly<{ prefix: 'ul_' | ''; lastFour: string; sha256Prefix: string }>;
export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' |
  'uncertain' | 'paused_402' | 'busy_503' | 'args_mismatch';
export type Provider = Readonly<{
  id: string;
  name: string;
  model: string;
  fingerprint: Fingerprint | null;
  probe: 'passed' | 'failed';
  imagesAllowed: boolean;
  local: boolean;
}>;
export type Connection = Readonly<{
  state: 'connected' | 'not_connected' | 'check_failed';
  workspace: Readonly<{ id: string; slug: string; owner: string }> | null;
  fingerprint: Fingerprint | null;
  checkedAt: string;
}>;
export type PlatformRun = Readonly<{
  id: string;
  recipe: string;
  release: string;
  status: RunStatus;
  epoch: number;
  epochs: number;
  durationSeconds: number | null;
  failureReason: string | null;
  estimateUsd: number;
  actualUsd: number | null;
  updatedAt: string;
  operationId: string;
  mismatches: readonly Readonly<{ key: string; requested: string; resolved: string | null }>[];
  checkpointSha256: string | null;
  targets: Readonly<{ kept: number; total: number }> | null;
}>;
export type DryRun = Readonly<{
  release: string;
  recipe: string;
  baseModel: string;
  baseSha256: string;
  gpu: string;
  gpuAvailable: boolean;
  epochs: number;
  images: number;
  // Exact ledger units, not independently rounded display dollars.
  estimateMicrousd: number;
  hourlyUsd: number;
  monthlyUsedAndReservedMicrousd: number;
  iterationUsedAndReservedMicrousd: number;
  monthlyLimitMicrousd: number;
  iterationLimitMicrousd: number;
  operationLimitMicrousd: number;
}>;
export type PlatformSnapshot = Readonly<{
  source: 'fixture';
  observedAt: string;
  paidStepsPaused: boolean;
  connection: Connection;
  providers: readonly Provider[];
  permittedUse: string;
  runs: readonly PlatformRun[];
  dryRun: DryRun | null;
}>;

// Deliberately no saveKey, selectWorkspace, start, retry, resume or cancel command.
// A future authenticated adapter must validate its transport at this boundary.
export interface PlatformReadPort {
  read(signal: AbortSignal): Promise<PlatformSnapshot>;
}

export const statusCopy: Record<RunStatus, { label: string; tone: string; reason: string }> = {
  queued: { label: 'Queued', tone: '', reason: 'Waiting for GPU.' },
  running: { label: 'Running', tone: '', reason: 'Training is active.' },
  completed: { label: 'Completed', tone: 'ok', reason: 'Training completed. Evaluation is separate.' },
  failed: { label: 'Failed', tone: 'danger', reason: 'The run failed. It will not be retried automatically.' },
  cancelled: { label: 'Cancelled', tone: '', reason: 'Cancelled. Time used so far is billed.' },
  uncertain: { label: 'Checking start', tone: 'warn', reason: "We're checking whether the platform started this run before trying again." },
  paused_402: { label: 'Paused', tone: 'danger', reason: 'Out of credits. Paid steps are paused; nothing is retried automatically.' },
  busy_503: { label: 'Platform busy', tone: 'warn', reason: 'Ultralytics Platform is busy. Try again later; nothing was started.' },
  args_mismatch: { label: 'Failed', tone: 'danger', reason: "Settings didn't match. This run cannot be trusted." },
};

export function fingerprintText(value: Fingerprint): string {
  if (!/^[a-zA-Z0-9]{4}$/.test(value.lastFour) || !/^[a-f0-9]{6,8}$/.test(value.sha256Prefix) ||
      (value.prefix !== 'ul_' && value.prefix !== '')) return 'Fingerprint unavailable';
  return `${value.prefix}••••${value.lastFour} · sha256 ${value.sha256Prefix}`;
}

export function checkedTime(value: string, now: number): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp) || !Number.isFinite(now) || timestamp > now) return 'Check time unavailable';
  const minutes = Math.floor((now - timestamp) / 60_000);
  const age = minutes < 1 ? 'less than 1 min' : minutes < 60 ? `${minutes} min`
    : minutes < 1440 ? `${Math.floor(minutes / 60)} h` : `${Math.floor(minutes / 1440)} d`;
  return `${new Date(timestamp).toISOString().slice(11, 16)} UTC · ${age} ago`;
}

export const shortHash = (value: string) => /^[a-f0-9]{64}$/.test(value) ? `${value.slice(0, 4)}…` : 'Hash unavailable';
export const usd = (value: number) => Number.isFinite(value) && value >= 0 ? `$${value.toFixed(2)}` : 'Unconfirmed';
export const microUsd = (value: number) => Number.isSafeInteger(value) && value >= 0 ? usd(value / 1_000_000) : 'Unconfirmed';
export function exceededLimits(draft: DryRun): string[] {
  const amounts = [draft.estimateMicrousd, draft.monthlyUsedAndReservedMicrousd, draft.iterationUsedAndReservedMicrousd,
    draft.monthlyLimitMicrousd, draft.iterationLimitMicrousd, draft.operationLimitMicrousd];
  if (amounts.some((amount) => !Number.isSafeInteger(amount) || amount < 0)) return ['Budget information unavailable'];
  const [estimate, monthlyUsed, iterationUsed, monthlyLimit, iterationLimit, operationLimit] = amounts;
  // Subtract to keep sums beyond the safe integer range out of comparisons.
  // This mirrors the ledger's exact units, but never authorizes server spend.
  return [
    estimate > operationLimit ? 'Per-operation limit' : '',
    estimate > monthlyLimit - monthlyUsed ? 'Monthly limit' : '',
    estimate > iterationLimit - iterationUsed ? 'Per-iteration limit' : '',
  ].filter(Boolean);
}
