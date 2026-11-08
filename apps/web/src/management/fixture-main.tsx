import { createRoot } from 'react-dom/client';
import type { Actions, ManagementPort, RecipeId, Role, Screen } from './contracts.ts';
import { EvaluationView } from './EvaluationView.tsx';
import { ModelsView } from './ModelsView.tsx';
import { NewProjectView } from './NewProjectView.tsx';
import { RecipesView } from './RecipesView.tsx';
import { ReleasesView } from './ReleasesView.tsx';
import { RunsView } from './RunsView.tsx';
import { ManagementModule } from './ManagementModule.tsx';
import { PortLifecycleFixture, RefreshLifecycleFixture } from './lifecycle-fixtures.tsx';
import { createFixturePort, fixtureSnapshot } from './fixtures.ts';
import './fixture.css';

const query = new URLSearchParams(window.location.search);
const screens: readonly Screen[] = ['new-project', 'releases', 'recipes', 'runs', 'evaluation', 'models'];
const selected = query.get('screen');
const screen: Screen = screens.find((item) => item === selected) ?? 'new-project';
const role: Role = query.get('role') === 'reviewer' ? 'reviewer' : 'admin';
const scenario = query.get('state') ?? 'ready';
const snapshot = structuredClone(fixtureSnapshot);
if (scenario === 'stale') snapshot.stale = true;
if (scenario === 'paused') snapshot.paid_paused = true;
if (scenario === 'empty') {
  snapshot.release = null; snapshot.recipes = []; snapshot.runs = []; snapshot.evaluations = []; snapshot.registry = [];
  snapshot.draft.classes = []; snapshot.draft.references = [];
  snapshot.draft.dataset_import = null; snapshot.draft.source_assets = [];
  snapshot.draft.sources = { images: 0, groups: 0, metadata_keys: '', pending_pairs: [], split_summary: 'No source assignments' };
}
if (scenario === 'source-details-missing') snapshot.draft.source_assets = null;
if (scenario === 'dispatch-published') snapshot.draft.published = true;
if (scenario === 'frozen' && snapshot.release) {
  snapshot.release.state = 'Frozen'; snapshot.release.frozen_at = snapshot.updated_at; snapshot.release.frozen_by = 'Fixture admin';
}
if (scenario === 'building' && snapshot.release) snapshot.release.state = 'Building';
if (scenario === 'gate-failed' && snapshot.release) snapshot.release.failed_checks = ['Val split is empty for 320: Platform would move training images into validation.'];
if (scenario === 'quarantine' && snapshot.release) snapshot.release.quarantine = '5 images link the test split to training. That test split is quarantined until reassessed.';
if (scenario === 'identical' && snapshot.release) snapshot.release.identical = true;
if (scenario === 'qualified' || scenario === 'command-contract') {
  snapshot.registry = snapshot.registry.map((record, index) => index === 0 ? { ...record, qualification: 'qualified', reason: 'Invented all-gates-passed scenario; not measured qualification.' } : record);
  if (snapshot.release) snapshot.release.state = 'Frozen';
}
if (query.get('release') === 'draft' && snapshot.release) snapshot.release.state = 'Draft';
const dispatchContract = scenario.startsWith('dispatch-');
if (dispatchContract) {
  // Simulate the authenticated port boundary, never a server or real mutation.
  snapshot.provenance = scenario === 'dispatch-fixture' ? 'fixture' : 'server';
  snapshot.stale = scenario === 'dispatch-stale';
}
const fixturePort = createFixturePort(snapshot);
const port: ManagementPort = dispatchContract ? {
  read: (signal) => fixturePort.read(signal),
  execute: (command) => {
    document.documentElement.dataset.lastCommand = JSON.stringify(command);
    if (scenario === 'dispatch-failed') return Promise.reject(new Error('Recorded uncertain command'));
    return Promise.resolve({ message: 'Captured only; no canonical mutation.' });
  },
} : scenario === 'loading' ? { read: (signal) => new Promise((_resolve, reject) => { signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }); }) } : scenario === 'error' ? { read: () => Promise.reject(new Error('Recorded fixture failure')) } : fixturePort;
const initialRecipe = query.get('recipe') as RecipeId | null;
// Browser contract tests exercise command payloads without connecting a server or
// adding mutation capabilities to the recorded fixture port. Never a production entry.
const actions: Actions = { role, connected: true, busy: false, send: (command) => {
  document.documentElement.dataset.lastCommand = JSON.stringify(command);
  return Promise.resolve(false);
} };
const props = { data: snapshot, actions };
const commandView = {
  'new-project': <NewProjectView {...props} />,
  releases: <ReleasesView {...props} />,
  recipes: <RecipesView {...props} />,
  runs: <RunsView {...props} />,
  evaluation: <EvaluationView {...props} initialRecipe={initialRecipe ?? undefined} />,
  models: <ModelsView {...props} />,
}[screen];
createRoot(document.getElementById('root')!).render(<>
  <a className="fixture-skip" href="#main">Skip to content</a>
  <header className="fixture-header"><p>labelloop · isolated management fixture, not the application shell</p><nav aria-label="Fixture screens">{screens.map((item) => <a key={item} href={`?screen=${item}&role=${role}`} aria-current={screen === item ? 'page' : undefined}>{item.replaceAll('-', ' ')}</a>)}</nav></header>
  <main id="main">{dispatchContract && <p>Dispatch-contract fixture: server provenance is simulated to exercise the module guard. No server connection or canonical mutation exists.</p>}{scenario === 'port-lifecycle' ? <PortLifecycleFixture /> : scenario === 'refresh-lifecycle' ? <RefreshLifecycleFixture screen={screen} /> : scenario === 'command-contract' ? <section className="management"><p>Command-contract fixture. Commands are captured for tests only; nothing is changed or sent to a server.</p>{commandView}</section> : <ManagementModule screen={screen} role={role} port={port} initialRecipe={initialRecipe ?? undefined} />}</main>
  <footer>labelloop is free software under the AGPL-3.0. <a href="https://github.com/TogetherWeOwn/labelloop">Get the source code.</a></footer>
</>);
