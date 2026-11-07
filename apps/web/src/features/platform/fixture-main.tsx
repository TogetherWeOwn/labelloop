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
  dryRun: state === 'over_limit' && platformFixture.dryRun ? { ...platformFixture.dryRun, operationLimitUsd: 1 } : platformFixture.dryRun,
};
const port: PlatformReadPort = state === 'error' ? { read: () => Promise.reject(new Error('Fixture unavailable')) }
  : state === 'loading' ? { read: () => new Promise<PlatformSnapshot>(() => { /* Intentional loading fixture. */ }) }
    : fixturePort(snapshot);

createRoot(document.getElementById('root')!).render(<>
  <main className="platform-fixture-page"><PlatformModule role={role} page={page} port={port} /></main>
  <footer className="platform-fixture-footer">labelloop is free software under the AGPL-3.0. <a href="https://github.com/TogetherWeOwn/labelloop">Get the source code.</a></footer>
</>);
