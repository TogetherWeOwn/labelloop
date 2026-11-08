import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ConnectionsView } from './ConnectionsView.tsx';
import { PlatformRunsView } from './PlatformRunsView.tsx';
import { PlatformModule } from './PlatformModule.tsx';
import { checkedTime, exceededLimits, fingerprintText, shortHash, statusCopy } from './contracts.ts';
import { fixturePort, platformFixture } from './fixtures.ts';

// Static rendering checks do not claim browser/a11y coverage; Actions runs that separately.
describe('Platform fixture modules', () => {
  it('hides connection records and management controls from reviewers', () => {
    const html = renderToStaticMarkup(<ConnectionsView role="reviewer" snapshot={platformFixture} now={Date.parse('2026-11-07T02:17:00Z')} />);
    expect(html).toContain('Only an admin');
    expect(html).not.toContain('workspace-fixture-01');
    expect(html).not.toContain('9f3c');
    expect(html).not.toContain('<button');
    expect(renderToStaticMarkup(<PlatformModule role="reviewer" page="connections" port={fixturePort()} />)).not.toContain('Loading');
  });

  it('uses exact workspace identity and fingerprint-only presentation', () => {
    const html = renderToStaticMarkup(<ConnectionsView role="admin" snapshot={platformFixture} now={Date.parse('2026-11-07T02:17:00Z')} />);
    expect(html).toContain('workspace-fixture-01');
    expect(html).toContain('shelf-team');
    expect(html).toContain('ul_••••9f3c');
    expect(html).not.toContain('type="text"');
    expect(html).toContain('not permission to train');
    expect(html).toContain('not used');
  });

  it('refuses malformed fingerprint fragments instead of echoing them', () => {
    expect(fingerprintText({ prefix: 'ul_', lastFour: 'too-long', sha256Prefix: 'not-a-hash' })).toBe('Fingerprint unavailable');
  });

  it('shows read-only run status, mismatches and unconfirmed actuals honestly', () => {
    for (const run of platformFixture.runs) {
      const html = renderToStaticMarkup(<PlatformRunsView role="reviewer" snapshot={{ ...platformFixture, runs: [run] }} />);
      expect(html).toContain(statusCopy[run.status].label);
      expect(html).toContain(statusCopy[run.status].reason.replaceAll("'", '&#x27;'));
      expect(html).not.toContain('Start a run');
      expect(html).not.toContain('Cancel run');
      expect(html).toContain(run.operationId);
      if (run.actualUsd === null) expect(html).toContain('Actual unconfirmed');
      if (run.status === 'args_mismatch') {
        expect(html).toContain('fliplr');
        expect(html).toContain('Missing');
      }
    }
  });

  it('claims provider checkpoint selection only when a candidate is recorded', () => {
    for (const run of platformFixture.runs) {
      const html = renderToStaticMarkup(<PlatformRunsView role="reviewer" snapshot={{ ...platformFixture, runs: [run] }} />);
      if (run.checkpointSha256) {
        expect(html).toContain('Selected under provider rect validation');
        expect(html).toContain('best.pt');
      } else {
        expect(html).toContain('Selection unconfirmed');
        expect(html).not.toContain('Selected under provider rect validation');
      }
    }
    const completed = platformFixture.runs.find((run) => run.status === 'completed')!;
    expect(renderToStaticMarkup(<PlatformRunsView role="reviewer" snapshot={{ ...platformFixture,
      runs: [{ ...completed, checkpointSha256: null }] }} />)).toContain('Selection unconfirmed');
  });

  it('names every exceeded limit including invalid cost data', () => {
    const draft = platformFixture.dryRun!;
    expect(exceededLimits(draft)).toEqual([]);
    expect(exceededLimits({ ...draft, operationLimitMicrousd: 1_000_000, monthlyLimitMicrousd: 1_000_000, iterationLimitMicrousd: 1_000_000 }))
      .toEqual(['Per-operation limit', 'Monthly limit', 'Per-iteration limit']);
    for (const estimateMicrousd of [NaN, -1, 0.5, Number.MAX_VALUE]) {
      expect(exceededLimits({ ...draft, estimateMicrousd })).toEqual(['Budget information unavailable']);
    }
    expect(exceededLimits({ ...draft, estimateMicrousd: 5_000_000, operationLimitMicrousd: 5_000_000, monthlyUsedAndReservedMicrousd: 0, iterationUsedAndReservedMicrousd: 0, iterationLimitMicrousd: 5_000_000 })).toEqual([]);
  });

  it('compares exact microdollars including sub-cent limit overruns', () => {
    const draft = { ...platformFixture.dryRun!, estimateMicrousd: 200_000,
      monthlyUsedAndReservedMicrousd: 100_000, monthlyLimitMicrousd: 300_000,
      iterationUsedAndReservedMicrousd: 100_000, iterationLimitMicrousd: 300_000 };
    expect(exceededLimits(draft)).toEqual([]);
    for (const estimateMicrousd of [200_001, 204_000, 210_000]) {
      expect(exceededLimits({ ...draft, estimateMicrousd })).toEqual(['Monthly limit', 'Per-iteration limit']);
    }
    expect(exceededLimits({ ...draft, estimateMicrousd: 1_004_000, operationLimitMicrousd: 1_000_000 }))
      .toEqual(['Per-operation limit', 'Monthly limit', 'Per-iteration limit']);
    expect(exceededLimits({ ...draft, estimateMicrousd: Number.MAX_SAFE_INTEGER,
      operationLimitMicrousd: Number.MAX_SAFE_INTEGER, monthlyUsedAndReservedMicrousd: 1,
      monthlyLimitMicrousd: Number.MAX_SAFE_INTEGER, iterationUsedAndReservedMicrousd: 1,
      iterationLimitMicrousd: Number.MAX_SAFE_INTEGER })).toEqual(['Monthly limit', 'Per-iteration limit']);
  });

  it('renders state-specific progress from the run record', () => {
    const html = renderToStaticMarkup(<PlatformRunsView role="reviewer" snapshot={platformFixture} />);
    expect(html).toContain('role="progressbar"');
    expect(html).toContain('aria-valuenow="41"');
    expect(html).toContain('waiting for GPU');
    expect(html).toContain('100 epochs · 1 h 52 m');
    expect(html).toContain('args mismatch');
    expect(html).toContain('out of credits');
    expect(html).not.toContain('0 of 100');
    const completed = platformFixture.runs.find((run) => run.status === 'completed')!;
    expect(renderToStaticMarkup(<PlatformRunsView role="reviewer" snapshot={{ ...platformFixture,
      runs: [{ ...completed, durationSeconds: null }] }} />)).toContain('duration unconfirmed');
  });

  it('formats last check in UTC with age and rejects invalid timestamps', () => {
    const now = Date.parse('2026-11-07T02:17:00Z');
    const html = renderToStaticMarkup(<ConnectionsView role="admin" snapshot={platformFixture} now={now} />);
    expect(html).toContain('01:52 UTC · 25 min ago');
    expect(html.match(/class="ok-i"/g)).toHaveLength(3);
    expect(html.match(/class="todo-i"/g)).toHaveLength(2);
    expect(checkedTime('invalid', now)).toBe('Check time unavailable');
    expect(checkedTime('2026-11-07T01:52:00Z', Date.parse('2026-11-07T01:51:00Z'))).toBe('Check time unavailable');
    expect(shortHash('9c1e'.repeat(16))).toBe('9c1e…');
    expect(shortHash('invalid')).toBe('Hash unavailable');
  });

  it('qualifies both entry builds with sentinels and treats imported tokens as web input', () => {
    const workflow = readFileSync(new URL('../../../../../.github/workflows/ci.yml', import.meta.url), 'utf8');
    expect(workflow).toMatch(/"web":\[[^\]]*"docs\/design\/mockups\/src\/tokens\.css"/);
    for (const build of ['build', 'build:platform-fixture']) {
      expect(workflow).toContain(`- run: pnpm --dir apps/web ${build}\n        env:\n          VITE_PLATFORM_KEY: ul_secretSentinelNeverBundle123456\n          VITE_AUTH_HEADER: Bearer bundleSentinelCredential123456`);
    }
  });

  it('exposes only abortable reads and isolates snapshots', async () => {
    const port = fixturePort();
    expect(Object.keys(port)).toEqual(['read']);
    const signal = new AbortController().signal;
    const first = await port.read(signal);
    expect(first).toEqual(platformFixture);
    expect(first).not.toBe(await port.read(signal));
    const controller = new AbortController();
    controller.abort();
    await expect(port.read(controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
  });
});
