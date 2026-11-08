import { useState } from 'react';
import type { ManagementCommand, ManagementPort, ManagementSnapshot, Screen } from './contracts.ts';
import { fixtureSnapshot } from './fixtures.ts';
import { ManagementModule } from './ManagementModule.tsx';

function capture(command: ManagementCommand) {
  document.documentElement.dataset.lastCommand = JSON.stringify(command);
  const commands: unknown[] = JSON.parse(document.documentElement.dataset.commands ?? '[]') as unknown[];
  document.documentElement.dataset.commands = JSON.stringify([...commands, command]);
}

// Explicitly simulated external ports for Actions lifecycle regressions only.
// Nothing in these fixtures connects to an API or persists canonical state.
export function PortLifecycleFixture() {
  const [replacement, setReplacement] = useState(false);
  const [rig] = useState(() => {
    const first = structuredClone(fixtureSnapshot);
    first.provenance = 'server'; first.draft.id = 'draft-A';
    const second = structuredClone(first); second.draft.id = 'draft-B';
    let ready = false;
    let resolveRead: ((value: ManagementSnapshot) => void) | undefined;
    const portA: ManagementPort = {
      read: () => Promise.resolve(structuredClone(first)),
      execute: (command, signal) => {
        capture(command);
        return new Promise((_resolve, reject) => {
          signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
        });
      },
    };
    const portB: ManagementPort = {
      read: () => ready ? Promise.resolve(structuredClone(second)) : new Promise((resolve) => { resolveRead = resolve; }),
      execute: (command) => { capture(command); return Promise.resolve({ message: 'Captured only' }); },
    };
    return { portA, portB, finish: () => { ready = true; resolveRead?.(structuredClone(second)); } };
  });
  return <>
    <p>Port lifecycle fixture. Both server identities are simulated; commands are captured only.</p>
    <button onClick={() => setReplacement(true)}>Replace port</button>
    <button onClick={() => rig.finish()}>Finish replacement read</button>
    <ManagementModule screen="new-project" role="admin" port={replacement ? rig.portB : rig.portA} />
  </>;
}

export function RefreshLifecycleFixture({ screen }: { screen: Screen }) {
  const [rig] = useState(() => {
    const snapshot = structuredClone(fixtureSnapshot);
    snapshot.provenance = 'server';
    if (snapshot.release) snapshot.release.state = 'Draft';
    if (screen === 'evaluation') snapshot.evaluations = [];
    let finishSave: (() => void) | undefined;
    const port: ManagementPort = {
      read: () => Promise.resolve(structuredClone(snapshot)),
      execute: (command) => {
        capture(command);
        if (command.kind === 'save_setup') return new Promise((resolve) => {
          finishSave = () => {
            snapshot.draft.revision += 1;
            if (typeof command.values.name === 'string') snapshot.draft.scope.name = command.values.name;
            resolve({ message: 'Captured save only' });
          };
        });
        return Promise.resolve({ message: 'Captured only' });
      },
    };
    return {
      port,
      refresh: () => {
        snapshot.draft.revision += 1;
        snapshot.draft.scope.name = 'Changed in another session';
        if (snapshot.release) snapshot.release.revision += 1;
        snapshot.evaluations = structuredClone(fixtureSnapshot.evaluations);
        document.dispatchEvent(new Event('visibilitychange'));
      },
      finish: () => { finishSave?.(); },
    };
  });
  return <>
    <p>Refresh lifecycle fixture. Revisions and save responses are simulated; no server is connected.</p>
    <button onClick={() => rig.refresh()}>Refresh external records</button>
    <button onClick={() => rig.finish()}>Finish pending save</button>
    <ManagementModule screen={screen} role="admin" port={rig.port} />
  </>;
}
