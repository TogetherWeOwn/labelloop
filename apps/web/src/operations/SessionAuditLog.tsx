import { useState } from 'react';
import type { Role } from './contracts.ts';
import { AuditLog } from './AuditLog.tsx';
import { sessionAuditPort } from './session-audit.ts';

type Props = { project: string; role: Role; updatedAt: string; stale?: boolean };

// Role is presentation, never authorization. The server session scopes every read.
export function SessionAuditLog(props: Props) {
  return <SessionAuditRead key={JSON.stringify([props.project, props.role])} {...props} />;
}

function SessionAuditRead({ project, ...props }: Props) {
  const [port] = useState(() => sessionAuditPort(project));
  return <AuditLog port={port} {...props} />;
}
