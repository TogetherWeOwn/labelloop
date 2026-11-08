import { createRoot } from 'react-dom/client';
import { PlatformModule } from './PlatformModule.tsx';
import { fixturePort, platformFixture } from './fixtures.ts';
import type { PlatformReadPort, PlatformSnapshot } from './contracts.ts';
import './fixture.css';

// Query controls exist only in this separate fixture entry, never in the auth shell.
const params = new URLSearchParams(window.location.search);
const page = params.get('page') === 'runs' ? 'runs' : 'connections';
const role = params.get('role') === 'reviewer' ? 'reviewer' : 'admin';
const state = params.get('state');
const snapshot: PlatformSnapshot = {
  ...platformFixture,
  paidStepsPaused: state === 'paused',
  runs: state === 'empty' ? [] : platformFixture.runs,
  providers: state === 'empty' ? [] : platformFixture.providers,
  connection: state === 'empty' ? { ...platformFixture.connection, state: 'not_connected', fingerprint: null, workspace: null }
    : state === 'check_failed' ? { ...platformFixture.connection, state: 'check_failed' } : platformFixture.connection,
  dryRun: state === 'over_limit' && platformFixture.dryRun ? { ...platformFixture.dryRun, operationLimitMicrousd: 1_000_000 } : platformFixture.dryRun,
};
let reads = 0;
const reader = fixturePort(snapshot);
const port: PlatformReadPort = {
  read(signal) {
    if (state === 'status_transitions') {
      const statuses = ['queued', 'queued', 'running', 'running', 'completed', 'completed', 'failed', 'failed'] as const;
      const index = reads++;
      const run = platformFixture.runs.find((run) => run.status === statuses[Math.min(index, statuses.length - 1)])!;
      return fixturePort({ ...snapshot, observedAt: new Date(Date.parse(snapshot.observedAt) + index * 30_000).toISOString(),
        runs: [{ ...run, id: 'run-31', operationId: 'operation-fixture-31' }] }).read(signal);
    }
    if (state === 'error') return Promise.reject(new Error('Fixture unavailable'));
    if (state === 'loading' || (state === 'pending_refresh' && reads++ > 0)) {
      return new Promise<PlatformSnapshot>(() => { /* Intentional pending read fixture. */ });
    }
    if (state === 'delayed') {
      return new Promise<PlatformSnapshot>((resolve) => window.setTimeout(() => resolve(snapshot), 500));
    }
    return reader.read(signal);
  },
};

createRoot(document.getElementById('root')!).render(<>
  <main className="platform-fixture-page"><PlatformModule role={role} page={page} port={port} /></main>
  <footer className="platform-fixture-footer">labelloop is free software under the AGPL-3.0. <a href="https://github.com/TogetherWeOwn/labelloop">Get the source code.</a></footer>
</>);
