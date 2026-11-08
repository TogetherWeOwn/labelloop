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
  estimateUsd: number;
  hourlyUsd: number;
  monthlyUsedAndReservedUsd: number;
  iterationUsedAndReservedUsd: number;
  monthlyLimitUsd: number;
  iterationLimitUsd: number;
  operationLimitUsd: number;
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

export const usd = (value: number) => Number.isFinite(value) && value >= 0 ? `$${value.toFixed(2)}` : 'Unconfirmed';
export function exceededLimits(draft: DryRun): string[] {
  const amounts = [draft.estimateUsd, draft.monthlyUsedAndReservedUsd, draft.iterationUsedAndReservedUsd,
    draft.monthlyLimitUsd, draft.iterationLimitUsd, draft.operationLimitUsd];
  // Compare the same cents shown in this fixture UI, not binary floating sums.
  // These are presentation checks, never a replacement for the server ledger.
  const cents = amounts.map((amount) => Math.round(amount * 100));
  if (amounts.some((amount) => !Number.isFinite(amount) || amount < 0) ||
      cents.some((amount) => !Number.isSafeInteger(amount))) return ['Budget information unavailable'];
  const [estimate, monthlyUsed, iterationUsed, monthlyLimit, iterationLimit, operationLimit] = cents;
  return [
    estimate > operationLimit ? 'Per-operation limit' : '',
    monthlyUsed + estimate > monthlyLimit ? 'Monthly limit' : '',
    iterationUsed + estimate > iterationLimit ? 'Per-iteration limit' : '',
  ].filter(Boolean);
}
