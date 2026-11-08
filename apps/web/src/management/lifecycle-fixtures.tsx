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

export function RefreshLifecycleFixture({ screen, importRejected = false }: { screen: Screen; importRejected?: boolean }) {
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
        if (command.kind === 'save_setup' || command.kind === 'edit_class' || command.kind === 'add_reference' || command.kind === 'draft_assistant_classes' || command.kind === 'request_dataset_import' || command.kind === 'request_source_upload' || command.kind === 'request_reference_upload') return new Promise((resolve) => {
          finishSave = () => {
            snapshot.draft.revision += 1;
            if (command.kind === 'save_setup' && command.step === 'Scope') {
              if (typeof command.values.name === 'string') snapshot.draft.scope.name = command.values.name;
              snapshot.draft.approved_steps = [];
            }
            if (command.kind === 'save_setup' && command.step === 'Sources and groups' && typeof command.values.metadata_keys === 'string') snapshot.draft.sources.metadata_keys = command.values.metadata_keys;
            if (command.kind === 'edit_class') snapshot.draft.classes = snapshot.draft.classes.map((row) => row.id === command.id ? { ...row, ...command.values } : row);
            if (command.kind === 'add_reference' || command.kind === 'request_reference_upload') snapshot.draft.references = [...(snapshot.draft.references ?? []), { id: command.kind === 'add_reference' ? 'ref-linked' : 'ref-uploaded', ...command.values, provenance: 'Proposed' }];
            if (command.kind === 'draft_assistant_classes') snapshot.draft.classes = [...snapshot.draft.classes, { id: 'assistant-oat', name: 'Oat milk 1 L', aliases: '', parent: '', exclusions: 'Soy milk', mirror_safe: false, examples: 0, provenance: 'Proposed' }];
            if (command.kind === 'request_dataset_import') {
              snapshot.draft.dataset_import = { id: 'import-returned', source_name: command.file.name, rows: importRejected ? 14 : 1, state: importRejected ? 'Rejected' : 'Proposed', errors: importRejected ? [{ row: 14, field: 'name', message: 'Class name is empty' }] : [] };
              if (!importRejected) {
                snapshot.draft.classes = [...snapshot.draft.classes, { id: 'imported-yoghurt', name: 'Imported yoghurt', aliases: '', parent: '', exclusions: 'Milk cartons', mirror_safe: false, examples: 0, provenance: 'Proposed' }];
                snapshot.draft.approved_steps = snapshot.draft.approved_steps?.filter((step) => step === 'Scope') ?? null;
              }
            }
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
      forgetSetupEvidence: () => {
        snapshot.draft.revision += 1;
        snapshot.draft.guide = null; snapshot.draft.references = null; snapshot.draft.approved_steps = null;
        snapshot.draft.sources.groups = null;
        document.dispatchEvent(new Event('visibilitychange'));
      },
      finish: () => { finishSave?.(); },
      pausePaid: () => { snapshot.paid_paused = true; document.dispatchEvent(new Event('visibilitychange')); },
    };
  });
  return <>
    <p>Refresh lifecycle fixture. Revisions and save responses are simulated; no server is connected.</p>
    <button onClick={() => rig.refresh()}>Refresh external records</button>
    <button onClick={() => rig.finish()}>Finish pending save</button>
    <button onClick={() => rig.forgetSetupEvidence()}>Return unknown setup evidence</button>
    <button onClick={() => rig.pausePaid()}>Pause paid steps</button>
    <ManagementModule screen={screen} role="admin" port={rig.port} />
  </>;
}
