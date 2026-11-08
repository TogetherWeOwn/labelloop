import { useState } from 'react';
import type { Actions, ProjectDraft, ScreenProps, SetupStep } from './contracts.ts';
import { Button, CommandButton, Facts, Notice, PageHeading, ReadOnly, Table, Utc } from './shared.tsx';

const setupSteps: readonly SetupStep[] = ['Scope', 'Classes', 'Evidence', 'Labelling guide', 'Sources and groups', 'Check and publish'];

export function NewProjectView({ data, actions }: ScreenProps) {
  const draft = data.draft;
  const [stepIndex, setStepIndex] = useState(1);
  const [name, setName] = useState('');
  const [pairId, setPairId] = useState<string | null>(null);
  const step = setupSteps[stepIndex];
  const readOnly = actions.role !== 'admin' || draft.published || !actions.connected || actions.busy;
  const earlierApproved = setupSteps.slice(0, stepIndex).every((part) => draft.approved_steps.includes(part));
  const unresolved = step === 'Classes' && draft.classes.some((row) => row.provenance === 'Proposed')
    || step === 'Evidence' && draft.references.some((row) => row.provenance === 'Proposed')
    || step === 'Sources and groups' && draft.sources.pending_pairs.length > 0;
  const pair = draft.sources.pending_pairs.find((item) => item.id === pairId);
  async function approve() {
    if (await actions.send({ kind: 'approve_step', draft_id: draft.id, expected_revision: draft.revision, step })) setStepIndex(Math.min(stepIndex + 1, setupSteps.length - 1));
  }
  async function addClass() {
    if (name.trim() && await actions.send({ kind: 'add_class', draft_id: draft.id, expected_revision: draft.revision, name: name.trim() })) setName('');
  }
  return <>
    <PageHeading title="New project." gloss="Set it up once, then let it learn." meta={<>Draft · saved <Utc value={draft.saved_at} /></>} lede="Nothing is labelled until you approve the classes. You can come back to any step; the draft saves as you go." />
    <ReadOnly role={actions.role} />
    <div className="grid2 setup-grid"><section className="card setup-content"><h2>{step} <span className="small">step {stepIndex + 1} of 6</span></h2>
      {draft.published && <Notice tone="ok">Project release 1 is published. These versions cannot change.</Notice>}
      {step === 'Scope' && <DraftFields key={`${draft.revision}:scope`} draft={draft} actions={actions} step={step} values={draft.scope} readOnly={readOnly} labels={{ name: 'Project name', detect: 'What to detect', representation: 'Representation layers', intended_use: 'Intended use', specificity: 'Specificity', permitted_use: 'Permitted use of source images', external_providers: 'Approved external LLM providers (empty = local only)' }} />}
      {step === 'Classes' && <>
        <p>Each class gets a permanent id. Renaming keeps it. Merging or splitting classes later makes a new ontology version.</p>
        <Table caption="Classes in this draft" columns={['Class', 'Aliases', 'Parent', 'Examples', 'Not this', 'Mirror-safe', 'Provenance', 'Actions']}>
          {draft.classes.map((row) => <tr key={row.id}><th scope="row">{row.name}</th><td>{row.aliases || 'None'}</td><td>{row.parent || 'None'}</td><td>{row.examples} {row.examples < 5 && <span className="tag warn">add more</span>}</td><td>{row.exclusions}</td><td>{row.mirror_safe ? 'Yes' : 'No'}</td><td><span className={`tag ${row.provenance === 'Proposed' ? 'proposed' : 'reviewed'}`}>{row.provenance}</span></td><td>{row.provenance === 'Proposed' && <CommandButton actions={actions} onClick={() => { void actions.send({ kind: 'accept_class', draft_id: draft.id, expected_revision: draft.revision, id: row.id }); }}>Accept {row.name}</CommandButton>}</td></tr>)}
        </Table>
        {actions.role === 'admin' && <form onSubmit={(event) => { event.preventDefault(); void addClass(); }} className="stack"><label htmlFor="new-class">Add a class</label><input id="new-class" value={name} readOnly={readOnly} onChange={(event) => setName(event.currentTarget.value)} /><CommandButton actions={actions} reason={!name.trim() ? 'Enter a class name first.' : undefined} onClick={() => { void addClass(); }}>Add class</CommandButton><p className="help">Mirror-safe means a left-right flip does not change the class. Text on packs usually means no.</p></form>}
        <Notice tone="info">Dataset import and assistant drafting are not connected in this module. Assistant rows remain Proposed until accepted individually.</Notice>
      </>}
      {step === 'Evidence' && <Table caption="Reference evidence" columns={['Class', 'Source and permitted use', 'View / lighting / scale', 'Often confused with', 'Provenance', 'Actions']}>
        {draft.references.map((reference) => <tr key={reference.id}><th scope="row">{reference.class_id}</th><td>{reference.source} · {reference.permitted_use}</td><td>{reference.view} / {reference.lighting} / {reference.scale}</td><td>{reference.confused_with}</td><td><span className="tag proposed">{reference.provenance}</span></td><td>{reference.provenance === 'Proposed' && <CommandButton actions={actions} onClick={() => { void actions.send({ kind: 'accept_reference', draft_id: draft.id, expected_revision: draft.revision, id: reference.id }); }}>Approve reference {reference.id}</CommandButton>}</td></tr>)}
      </Table>}
      {step === 'Labelling guide' && <DraftFields key={`${draft.revision}:guide`} draft={draft} actions={actions} step={step} values={draft.guide} readOnly={readOnly} labels={{ extent: 'Visible or estimated extent (default visible)', truncation: 'Targets at image edges', occlusion: 'Occluded targets', reflections: 'Reflections', min_target_px: 'Minimum target size in pixels', exhaustive: 'Exhaustive for scope', background: 'Out-of-scope background' }} />}
      {step === 'Sources and groups' && <>
        <Facts items={[[ 'Images / source groups', `${draft.sources.images} / ${draft.sources.groups}` ], ['Declared metadata keys', draft.sources.metadata_keys], ['Split preview', draft.sources.split_summary]]} />
        <Notice>{draft.sources.pending_pairs.length} near-duplicate pairs need a look. A new cross-split relationship quarantines the affected holdout; the browser never assigns a split.</Notice>
        {draft.sources.pending_pairs.map((item) => <Button key={item.id} onClick={() => setPairId(item.id)}>Compare {item.left} and {item.right}</Button>)}
        {pair && <section className="stack"><h3>Near-duplicate comparison</h3><div className="grid2 even"><div className="well">{pair.left} · source image unavailable in recorded fixture</div><div className="well">{pair.right} · source image unavailable in recorded fixture</div></div><CommandButton actions={actions} reason="Original pair pixels are required before deciding a group relationship.">Same group</CommandButton><CommandButton actions={actions} reason="Original pair pixels are required before deciding a group relationship.">Different groups</CommandButton></section>}
      </>}
      {step === 'Check and publish' && <>
        <Facts items={Object.entries(draft.versions)} />
        <Notice>Acceptance targets are unset. Automatic promotion stays off until the project has explicit targets and passes its pilot.</Notice>
        <p>Autonomous spend is off. Budget limits belong to Costs; setup never authorizes paid work.</p>
        <CommandButton actions={actions} primary reason={!setupSteps.slice(0, -1).every((part) => draft.approved_steps.includes(part)) ? 'Approve all preceding steps before publishing.' : draft.published ? 'This release is already published.' : undefined} onClick={() => { void actions.send({ kind: 'publish_project', draft_id: draft.id, expected_revision: draft.revision }); }}>Publish project release 1</CommandButton>
      </>}
      <Notice tone="info">Changing classes re-opens Evidence and Guide. The server invalidates dependent approvals; an unapproved catalog cannot start bulk labelling.</Notice>
      {stepIndex < setupSteps.length - 1 && !draft.published && <CommandButton actions={actions} primary reason={!earlierApproved ? 'Approve the earlier steps first.' : unresolved ? 'Decide all Proposed items or near-duplicate pairs first.' : undefined} onClick={() => { void approve(); }}>Approve {step.toLowerCase()} and continue</CommandButton>}
    </section>
    <aside className="stack setup-rail"><nav className="card" aria-label="Setup steps"><details open><summary>Step {stepIndex + 1} of 6 · {step} · All steps</summary><ol className="steps">{setupSteps.map((part, index) => <li key={part} aria-current={index === stepIndex ? 'step' : undefined}><Button onClick={() => setStepIndex(index)}><span className="mono">0{index + 1}</span> {part}</Button><span className="tag">{draft.approved_steps.includes(part) ? 'Done' : index === stepIndex ? 'Now' : 'Not approved'}</span></li>)}</ol></details></nav><Notice>Automation stays off for a class until it has enough reviewed examples and passes its pilot. Almond milk needs at least 1 more.</Notice></aside></div>
  </>;
}

function DraftFields({ draft, actions, step, values, labels, readOnly }: { draft: ProjectDraft; actions: Actions; step: SetupStep; values: Readonly<Record<string, string | number | boolean>>; labels: Record<string, string>; readOnly: boolean }) {
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  async function save(form: HTMLFormElement) {
    if (readOnly || saving) return;
    const fields = new FormData(form);
    const next = Object.fromEntries(Object.entries(values).map(([name, value]) => {
      const field = fields.get(name);
      const text = typeof field === 'string' ? field : '';
      return [name, typeof value === 'boolean' ? fields.has(name) : typeof value === 'number' ? Number(text) : text];
    }));
    setSaving(true);
    const saved = await actions.send({ kind: 'save_setup', draft_id: draft.id, expected_revision: draft.revision, step, values: next });
    setFailed(!saved); setSaving(false);
  }
  return <form className="stack" onSubmit={(event) => { event.preventDefault(); void save(event.currentTarget); }} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) void save(event.currentTarget); }}>
    {Object.entries(values).map(([name, value]) => <div className="field" key={name}><label htmlFor={`setup-${name}`}>{labels[name]}</label>{typeof value === 'boolean' ? <input id={`setup-${name}`} name={name} type="checkbox" defaultChecked={value} disabled={readOnly || saving} /> : <input id={`setup-${name}`} name={name} type={typeof value === 'number' ? 'number' : 'text'} min={typeof value === 'number' ? 1 : undefined} defaultValue={value} readOnly={readOnly || saving} />}</div>)}
    <p className="help">{saving ? 'Saving…' : !actions.connected ? 'Recorded draft. Editing and autosave need the authenticated management client.' : 'Changes save when you leave these fields.'}</p>
    {failed && <Notice tone="danger">Your last change did not save. Try again.<Button type="submit">Try again</Button></Notice>}
  </form>;
}
