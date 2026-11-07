import type { PlatformReadPort, PlatformSnapshot, RunStatus } from './contracts.ts';

const at = '2026-11-07T01:52:00Z';
export const platformFixture: PlatformSnapshot = {
  source: 'fixture', observedAt: at, paidStepsPaused: false,
  connection: {
    state: 'connected', workspace: { id: 'workspace-fixture-01', slug: 'shelf-team', owner: 'Fixture admin' },
    fingerprint: { prefix: 'ul_', lastFour: '9f3c', sha256Prefix: '1a2b3c4d' }, checkedAt: at,
  },
  permittedUse: 'Fixture shelf photos. No project images are sent to a provider in this preview.',
  providers: [
    { id: 'remote-fixture', name: 'Remote fixture', model: 'vision-fixture-v1', fingerprint: { prefix: '', lastFour: 'a71d', sha256Prefix: '77e0c2' }, probe: 'passed', imagesAllowed: true, local: false },
    { id: 'failed-fixture', name: 'Failed fixture', model: 'schema-fixture-v1', fingerprint: { prefix: '', lastFour: '02be', sha256Prefix: '1f9a44' }, probe: 'failed', imagesAllowed: true, local: false },
    { id: 'local-fixture', name: 'Local', model: 'local-fixture-v1', fingerprint: null, probe: 'passed', imagesAllowed: true, local: true },
  ],
  runs: (['running', 'queued', 'completed', 'args_mismatch', 'uncertain', 'paused_402', 'busy_503', 'cancelled', 'failed'] satisfies RunStatus[]).map((status, i) => ({
    id: `run-${31 - i}`, recipe: i % 2 ? 'full-letterbox-320-v1' : 'full-letterbox-640-v1', release: 'r0008',
    status, epoch: status === 'completed' ? 100 : status === 'running' ? 41 : 0, epochs: 100,
    estimateUsd: 1.38, actualUsd: status === 'completed' ? 1.29 : status === 'cancelled' ? 0.17 : null,
    updatedAt: at, operationId: `operation-fixture-${31 - i}`,
    mismatches: status === 'args_mismatch' ? [
      { key: 'fliplr', requested: '0.0', resolved: '0.5' },
      { key: 'scale', requested: '0.0', resolved: null },
    ] : [],
    checkpointSha256: status === 'completed' ? 'a'.repeat(64) : null,
    targets: status === 'completed' ? { kept: 12410, total: 12418 } : null,
  })),
  dryRun: {
    release: 'r0008', recipe: 'full-letterbox-320-v1', baseModel: 'yolo26n', baseSha256: '9c1e'.repeat(16),
    gpu: 'RTX 4090', gpuAvailable: true, epochs: 100, images: 1114, estimateUsd: 1.38, hourlyUsd: 0.69,
    monthlyUsedAndReservedUsd: 4.48, iterationUsedAndReservedUsd: 0.41,
    monthlyLimitUsd: 40, iterationLimitUsd: 3, operationLimitUsd: 5,
  },
};

export function fixturePort(snapshot: PlatformSnapshot = platformFixture): PlatformReadPort {
  return {
    read(signal) {
      if (signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
      return Promise.resolve(structuredClone(snapshot));
    },
  };
}
