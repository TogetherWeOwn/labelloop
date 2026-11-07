import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ConnectionsView } from './ConnectionsView.tsx';
import { PlatformRunsView } from './PlatformRunsView.tsx';
import { PlatformModule } from './PlatformModule.tsx';
import { exceededLimits, fingerprintText, statusCopy } from './contracts.ts';
import { fixturePort, platformFixture } from './fixtures.ts';

// Static rendering checks do not claim browser/a11y coverage; Actions runs that separately.
describe('Platform fixture modules', () => {
  it('hides connection records and management controls from reviewers', () => {
    const html = renderToStaticMarkup(<ConnectionsView role="reviewer" snapshot={platformFixture} />);
    expect(html).toContain('Only an admin');
    expect(html).not.toContain('workspace-fixture-01');
    expect(html).not.toContain('9f3c');
    expect(html).not.toContain('<button');
    expect(renderToStaticMarkup(<PlatformModule role="reviewer" page="connections" port={fixturePort()} />)).not.toContain('Loading');
  });

  it('uses exact workspace identity and fingerprint-only presentation', () => {
    const html = renderToStaticMarkup(<ConnectionsView role="admin" snapshot={platformFixture} />);
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

  it('names every exceeded limit including invalid cost data', () => {
    const draft = platformFixture.dryRun!;
    expect(exceededLimits(draft)).toEqual([]);
    expect(exceededLimits({ ...draft, operationLimitUsd: 1, monthlyLimitUsd: 1, iterationLimitUsd: 1 }))
      .toEqual(['Per-operation limit', 'Monthly limit', 'Per-iteration limit']);
    expect(exceededLimits({ ...draft, estimateUsd: NaN })).toEqual(['Budget information unavailable']);
    expect(exceededLimits({ ...draft, estimateUsd: -1 })).toEqual(['Budget information unavailable']);
    expect(exceededLimits({ ...draft, estimateUsd: 5, operationLimitUsd: 5, monthlyUsedAndReservedUsd: 0, iterationUsedAndReservedUsd: 0, iterationLimitUsd: 5 })).toEqual([]);
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
