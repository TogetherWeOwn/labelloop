import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { Actions, ManagementCommand, ManagementPort, ManagementSnapshot, ModelExtensions, RecipeId, Role, Screen } from './contracts.ts';
import { EvaluationView } from './EvaluationView.tsx';
import { ModelsView } from './ModelsView.tsx';
import { NewProjectView } from './NewProjectView.tsx';
import { RecipesView } from './RecipesView.tsx';
import { ReleasesView } from './ReleasesView.tsx';
import { RunsView } from './RunsView.tsx';
import { Button, Notice, Utc } from './shared.tsx';
import './management.css';

export function ManagementModule({ screen, role, port, initialRecipe, platformRuns, modelExtensions }: {
  screen: Screen; role: Role; port: ManagementPort; initialRecipe?: RecipeId;
  platformRuns?: ReactNode; modelExtensions?: ModelExtensions;
}) {
  const [data, setData] = useState<ManagementSnapshot | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const request = useRef<AbortController | null>(null);
  const command = useRef<AbortController | null>(null);
  const previous = useRef('');
  const busyRef = useRef(false);
  const load = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    try {
      const snapshot = await port.read(controller.signal);
      if (controller.signal.aborted) return;
      const jobStatus = JSON.stringify([snapshot.runs.map((run) => [run.id, run.state]), snapshot.recipes.map((recipe) => [recipe.id, recipe.job]), snapshot.evaluations.map((evaluation) => [evaluation.id, evaluation.state]), snapshot.stale]);
      if (previous.current && previous.current !== jobStatus) setStatus('Recorded job status or freshness changed. Updated records are shown.');
      previous.current = jobStatus;
      setData(snapshot); setFailed(false);
    } catch {
      if (!controller.signal.aborted) { setFailed(true); setStatus('Records could not be refreshed. Retry before making a change.'); }
    }
  }, [port]);
  useEffect(() => {
    const initial = window.setTimeout(() => { void load(); }, 0);
    const refresh = () => { if (document.visibilityState === 'visible' && !busyRef.current) void load(); };
    const interval = window.setInterval(refresh, 30_000);
    document.addEventListener('visibilitychange', refresh);
    return () => { request.current?.abort(); command.current?.abort(); window.clearTimeout(initial); window.clearInterval(interval); document.removeEventListener('visibilitychange', refresh); };
  }, [load]);
  const connected = Boolean(port.execute && data?.provenance === 'server' && !data.stale && !failed);
  async function send(input: ManagementCommand) {
    if (role !== 'admin' || !connected || !port.execute || busyRef.current) return false;
    busyRef.current = true; setBusy(true);
    request.current?.abort();
    const controller = new AbortController(); command.current = controller;
    try {
      await port.execute(input, controller.signal);
      if (controller.signal.aborted) return false;
      setStatus('Command completed. Updated canonical records requested.');
      await load();
      return true;
    } catch {
      if (!controller.signal.aborted) {
        setFailed(true);
        setStatus('The change could not be confirmed. The last loaded record remains shown. Refresh before trying again; the server must reconcile an uncertain start.');
      }
      return false;
    } finally {
      busyRef.current = false;
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  const actions: Actions = { role, connected, busy, send };
  return <section className="management" aria-label="Project management">
    <div role="status" aria-live="polite" className="sr">{status}</div>
    {!data && failed && <h1>Project management</h1>}
    {failed && <Notice tone="danger">Records did not load or refresh. Check the connection and try again. <Button onClick={() => { void load(); }}>Try again</Button></Notice>}
    {!data && !failed && <div className="card" aria-busy="true"><h1>Project management</h1><p>Loading records…</p></div>}
    {data && <>
      {data.provenance === 'fixture' && <Notice tone="info">Recorded fixture preview — all names, counts, hashes and metrics are invented examples, not measured quality or connected commands. No project, release, training job or model will change here.</Notice>}
      {role === 'admin' && !port.execute && <p>Management commands are not connected. Inspect the recorded data; use the authenticated facade after shell integration.</p>}
      {data.paid_paused && <Notice tone="danger">Paid steps paused. No paid work will be retried automatically. Local work has no account charge.</Notice>}
      <p className={`label ${data.stale || failed ? 'stale' : ''}`}>{data.stale || failed ? 'Stale data · ' : ''}{data.provenance === 'fixture' ? 'Recorded fixture at ' : 'Last refreshed '}<Utc value={data.updated_at} /> · visible records refresh every 30 s</p>
      {screen === 'new-project' && <NewProjectView data={data} actions={actions} />}
      {screen === 'releases' && <ReleasesView data={data} actions={actions} />}
      {screen === 'recipes' && <RecipesView data={data} actions={actions} />}
      {screen === 'runs' && <RunsView data={data} actions={actions} platform={platformRuns} />}
      {screen === 'evaluation' && <EvaluationView data={data} actions={actions} initialRecipe={initialRecipe} />}
      {screen === 'models' && <ModelsView data={data} actions={actions} extensions={modelExtensions} />}
    </>}
  </section>;
}
