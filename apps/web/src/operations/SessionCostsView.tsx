import { useEffect, useRef, useState } from 'react';
import type { CostsRecord, CostScope, Role, ViewState } from './contracts.ts';
import { CostsView } from './CostsView.tsx';
import { appendSessionCosts, sessionCostsRecord } from './session-costs.ts';
import { costClient } from './cost-client.ts';
import { download } from './records.ts';

type Props = { project: string; month: string; iteration: string; role: Role; scope: CostScope; csrfToken?: string };

// Role controls presentation only. Fixed routes require the server's admin
// session; a supplied CSRF token connects limits only, never paid resumption.
export function SessionCostsView(props: Props) {
  return <SessionCostsRead key={JSON.stringify([props.project, props.month, props.iteration, props.role, props.scope.kind, props.scope.id, props.csrfToken])} {...props} />;
}

function SessionCostsRead({ project, month, iteration, role, scope, csrfToken }: Props) {
  const [attempt, setAttempt] = useState(0);
  const [after, setAfter] = useState<string | null>(null);
  const [pageStatus, setPageStatus] = useState<'idle' | 'pending' | 'failed'>('idle');
  const [state, setState] = useState<ViewState<CostsRecord>>({ status: 'loading' });
  const loaded = useRef<CostsRecord | null>(null);
  const exportRequest = useRef<AbortController | null>(null);
  const commandRequest = useRef<AbortController | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportMessage, setExportMessage] = useState('');
  useEffect(() => () => { exportRequest.current?.abort(); commandRequest.current?.abort(); }, []);
  const refresh = () => {
    commandRequest.current?.abort(); commandRequest.current = null;
    exportRequest.current?.abort(); exportRequest.current = null; setExporting(false); setExportMessage('');
    loaded.current = null; setAfter(null); setPageStatus('idle'); setState({ status: 'loading' }); setAttempt((value) => value + 1);
  };
  useEffect(() => {
    const controller = new AbortController();
    void sessionCostsRecord(project, { month, iteration_id: iteration }, after === null ? {} : { after }, controller.signal).then((record) => {
      if (controller.signal.aborted) return;
      const combined = after === null ? record : loaded.current === null ? null : appendSessionCosts(loaded.current, record);
      if (combined === null) throw new Error('Cost records unavailable');
      loaded.current = combined;
      setState({ status: 'ready', data: combined, updatedAt: new Date().toISOString(), stale: false });
      setPageStatus('idle');
    }).catch(() => {
      if (controller.signal.aborted) return;
      if (after !== null) setPageStatus('failed');
      else setState({ status: 'error', retry: () => { setState({ status: 'loading' }); setAttempt((value) => value + 1); } });
    });
    return () => controller.abort();
  }, [project, month, iteration, after, attempt]);
  return <CostsView state={state} readId={attempt} role={role} scope={scope} onRefresh={refresh} saveLimits={role !== 'admin' || csrfToken === undefined || !/^[A-Za-z0-9_-]{1,256}$/.test(csrfToken) ? undefined : async (policy, expectedRevision) => {
    if (commandRequest.current !== null) throw new Error('Cost change not confirmed');
    const controller = new AbortController();
    commandRequest.current = controller;
    try {
      await costClient(project).setLimits({ policy, expected_revision: expectedRevision }, csrfToken, controller.signal);
      controller.signal.throwIfAborted();
    } finally { if (commandRequest.current === controller) commandRequest.current = null; }
  }} paging={{
    pending: pageStatus === 'pending', failed: pageStatus === 'failed',
    onMore: () => {
      const record = loaded.current;
      if (pageStatus !== 'idle' || record?.source !== 'session' || record.next_after === null) return;
      setPageStatus('pending'); setAfter(record.next_after);
    },
    onRetry: () => { setPageStatus('pending'); setAttempt((value) => value + 1); },
  }} projectExport={{
    pending: exporting, message: exportMessage,
    onExport: () => {
      if (exportRequest.current !== null || state.status !== 'ready' || state.stale) return;
      const controller = new AbortController();
      exportRequest.current = controller;
      setExporting(true); setExportMessage('Preparing the authorized project export…');
      void costClient(project).exportJson(controller.signal).then((blob) => {
        if (controller.signal.aborted) return;
        download(blob, 'costs.json'); setExportMessage('Project export downloaded.');
      }).catch(() => { if (!controller.signal.aborted) setExportMessage('Project export could not be loaded. Try again.'); })
        .finally(() => {
          if (!controller.signal.aborted) { exportRequest.current = null; setExporting(false); }
        });
    },
  }} />;
}
