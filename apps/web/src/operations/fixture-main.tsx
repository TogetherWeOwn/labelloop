import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { AuditPage, AuditQuery, CostCommands, CostsRecord, CostScope, ViewState } from './contracts.ts';
import { AuditLog, AuditView } from './AuditLog.tsx';
import { CostsView } from './CostsView.tsx';
import { RecipeOperations } from './RecipeOperations.tsx';
import { auditFixture, costsFixture, fixtureTime, recipesFixture, recordedAuditPort } from './fixtures.ts';
import './operations.css';
import './fixture.css';

const params = new URLSearchParams(location.search);
const view = params.get('view') ?? 'costs';
const scenario = params.get('state') ?? 'ready';
const role = scenario === 'readonly' ? 'reviewer' : 'admin';
const query: AuditQuery = { kinds: ['Review', 'Promotion', 'Connection', 'Budget'], principals: ['person', 'service'], search: '', from: '', through: '' };
// These are separate simulated server result sets; never use fixture role routing
// as authentication. Production integration must replace this entry entirely.
const authorizedEvents = role === 'reviewer' ? auditFixture.filter((event) => event.kind === 'Review') : auditFixture;
const auditPort = recordedAuditPort(authorizedEvents);
const ready = <T,>(data: T): ViewState<T> => scenario === 'loading' ? { status: 'loading' } : scenario === 'error' ? { status: 'error', retry: () => location.reload() } : { status: 'ready', data, updatedAt: fixtureTime, stale: scenario === 'stale' };

function FixtureApp() {
  const [costs, setCosts] = useState<CostsRecord>(scenario === 'empty' ? { ...costsFixture, entries: [], pause_reason: null } : scenario === 'unset' ? { ...costsFixture, policy: { monthly_limit: null, iteration_limit: null, operation_limit: null, autonomous_spend: false } } : costsFixture);
  const [auditQuery, setAuditQuery] = useState(query);
  const [refresh, setRefresh] = useState(0);
  const commands: CostCommands | undefined = scenario === 'editable' || scenario === 'command-error' ? {
    savePolicy: (policy, expectedRevision) => {
      if (scenario === 'command-error' || costs.revision !== expectedRevision) return Promise.reject(new Error('Recorded command failure'));
      setCosts({ ...costs, policy, revision: expectedRevision + 1 }); return Promise.resolve();
    },
    resumePaid: (expectedRevision) => { setCosts({ ...costs, pause_reason: null, revision: expectedRevision + 1 }); return Promise.resolve(); },
  } : undefined;
  const scope: CostScope = params.get('scope') === 'iteration' ? { kind: 'iteration', id: 'iteration-4' } : params.get('scope') === 'run' ? { kind: 'run', id: 'run-0030' } : params.get('scope') === 'review_batch' ? { kind: 'review_batch', id: 'batch-49' } : { kind: 'project', id: 'shelf-demo' };
  const auditState = ready<AuditPage>({ events: [], total: 0, next_cursor: null });
  return <div className="fixture-frame"><a className="fixture-skip" href="#content">Skip to content</a><header className="fixture-header"><p>Recorded fixtures only · no live account, qualification or commands</p><p className="danger-text">{costs.pause_reason !== null ? 'Paid steps paused' : 'Paid steps unavailable in this fixture'}</p><nav aria-label="Operational fixture views">{['costs', 'audit', 'models'].map((name) => <a key={name} aria-current={view === name ? 'page' : undefined} href={`?view=${name}&state=${scenario}`}>{name === 'audit' ? 'Audit log' : name[0]?.toUpperCase() + name.slice(1)}</a>)}</nav></header>
    <main id="content" tabIndex={-1}>
      {view === 'costs' ? <CostsView key={refresh} state={ready(costs)} role={role} scope={scope} commands={commands} onRefresh={() => setRefresh(refresh + 1)} /> : view === 'models' ? <RecipeOperations state={ready(scenario === 'empty' ? [] : recipesFixture)} role={role} /> : scenario === 'loading' || scenario === 'error' || scenario === 'empty' ? <AuditView state={auditState} query={auditQuery} role={role} onQuery={setAuditQuery} onMore={() => undefined} onExport={() => undefined} /> : <AuditLog port={auditPort} role={role} stale={scenario === 'stale'} updatedAt={fixtureTime} />}
    </main><footer>labelloop is free software under the AGPL-3.0. <a href="https://github.com/TogetherWeOwn/labelloop">Get the source code.</a></footer>
  </div>;
}

const root = document.getElementById('root');
if (!root) throw new Error('Missing fixture root');
createRoot(root).render(<FixtureApp />);
