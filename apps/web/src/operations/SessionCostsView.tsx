import { useEffect, useState } from 'react';
import type { CostsRecord, CostScope, Role, ViewState } from './contracts.ts';
import { CostsView } from './CostsView.tsx';
import { sessionCostsRecord } from './session-costs.ts';

type Props = { project: string; month: string; iteration: string; role: Role; scope: CostScope };

// Role controls presentation only. Each fixed-route read still needs the server's
// admin session. This component connects no spending commands or product routing.
export function SessionCostsView(props: Props) {
  return <SessionCostsRead key={JSON.stringify([props.project, props.month, props.iteration, props.role, props.scope.kind, props.scope.id])} {...props} />;
}

function SessionCostsRead({ project, month, iteration, role, scope }: Props) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<ViewState<CostsRecord>>({ status: 'loading' });
  const refresh = () => { setState({ status: 'loading' }); setAttempt((value) => value + 1); };
  useEffect(() => {
    const controller = new AbortController();
    void sessionCostsRecord(project, { month, iteration_id: iteration }, {}, controller.signal).then((record) => {
      if (!controller.signal.aborted) setState({ status: 'ready', data: record, updatedAt: new Date().toISOString(), stale: false });
    }).catch(() => {
      if (!controller.signal.aborted) setState({ status: 'error', retry: () => { setState({ status: 'loading' }); setAttempt((value) => value + 1); } });
    });
    return () => controller.abort();
  }, [project, month, iteration, attempt]);
  return <CostsView state={state} readId={attempt} role={role} scope={scope} onRefresh={refresh} />;
}
