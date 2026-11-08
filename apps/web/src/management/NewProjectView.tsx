import { useState } from 'react';
import type { Actions, ClassRow, ProjectDraft, ReferenceInput, ScreenProps, SetupStep } from './contracts.ts';
import { Button, CommandButton, Facts, Field, Notice, PageHeading, ReadOnly, Table, Utc } from './shared.tsx';

const setupSteps: readonly SetupStep[] = ['Scope', 'Classes', 'Evidence', 'Labelling guide', 'Sources and groups', 'Check and publish'];

export function NewProjectView({ data, actions }: ScreenProps) {
  const draft = data.draft;
  const [stepIndex, setStepIndex] = useState(1);
  const [name, setName] = useState('');
  const [pairId, setPairId] = useState<string | null>(null);
  const [unsavedFields, setUnsavedFields] = useState(false);
  const [editing, setEditing] = useState<{ row: ClassRow; revision: number; draftId: string } | null>(null);
  const [referenceEntry, setReferenceEntry] = useState<{ revision: number; draftId: string } | null>(null);
  const [assistantEntry, setAssistantEntry] = useState<{ revision: number; draftId: string; scope: string } | null>(null);
  const pendingEdits = unsavedFields || editing !== null || referenceEntry !== null || assistantEntry !== null;
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
      {step === 'Scope' && <DraftFields key={`${draft.id}:scope`} draft={draft} actions={actions} step={step} values={draft.scope} readOnly={readOnly} onDirtyChange={setUnsavedFields} labels={{ name: 'Project name', detect: 'What to detect', representation: 'Representation layers', intended_use: 'Intended use', specificity: 'Specificity', permitted_use: 'Permitted use of source images', external_providers: 'Approved external LLM providers (empty = local only)' }} />}
      {step === 'Classes' && <>
        <p>Each class gets a permanent id. Renaming keeps it. Merging or splitting classes later makes a new ontology version.</p>
        <Table caption="Classes in this draft" columns={['Class', 'Aliases', 'Parent', 'Examples', 'Not this', 'Mirror-safe', 'Provenance', 'Actions']}>
          {draft.classes.map((row) => <tr key={row.id}><th scope="row">{row.name}</th><td>{row.aliases || 'None'}</td><td>{row.parent || 'None'}</td><td>{row.examples} {row.examples < 5 && <span className="tag warn">add more</span>}</td><td>{row.exclusions}</td><td>{row.mirror_safe ? 'Yes' : 'No'}</td><td><span className={`tag ${row.provenance === 'Proposed' ? 'proposed' : 'reviewed'}`}>{row.provenance}</span></td><td><CommandButton actions={actions} disabled={readOnly || pendingEdits} onClick={() => setEditing({ row, revision: draft.revision, draftId: draft.id })}>Edit {row.name}</CommandButton>{row.provenance === 'Proposed' && <CommandButton actions={actions} disabled={readOnly || pendingEdits} onClick={() => { void actions.send({ kind: 'accept_class', draft_id: draft.id, expected_revision: draft.revision, id: row.id }); }}>Accept {row.name}</CommandButton>}</td></tr>)}
        </Table>
        {editing && <ClassFields key={editing.row.id} draft={draft} actions={actions} row={editing.row} revision={editing.revision} draftId={editing.draftId} readOnly={readOnly} onClose={() => setEditing(null)} />}
        {actions.role === 'admin' && <form onSubmit={(event) => { event.preventDefault(); if (!readOnly && !pendingEdits) void addClass(); }} className="stack"><label htmlFor="new-class">Add a class</label><input id="new-class" value={name} readOnly={readOnly || pendingEdits} onChange={(event) => setName(event.currentTarget.value)} /><CommandButton actions={actions} disabled={readOnly || pendingEdits} reason={!name.trim() ? 'Enter a class name first.' : undefined} onClick={() => { void addClass(); }}>Add class</CommandButton><p className="help">Mirror-safe means a left-right flip does not change the class. Text on packs usually means no.</p></form>}
        <Notice tone="info">Dataset import and assistant generation are not connected in this module. Assistant request capture does not call a provider, authorize spend or approve classes. Returned proposals need individual admin acceptance.</Notice>
        <CommandButton actions={actions} disabled={readOnly || pendingEdits} reason={data.paid_paused ? 'Paid steps are paused. Assistant drafting is unavailable.' : !draft.approved_steps.includes('Scope') ? 'Approve the scope before requesting draft classes.' : undefined} onClick={() => setAssistantEntry({ revision: draft.revision, draftId: draft.id, scope: draft.scope.detect })}>Draft with the assistant</CommandButton>
        {assistantEntry && <AssistantDraft draft={draft} actions={actions} revision={assistantEntry.revision} draftId={assistantEntry.draftId} scope={assistantEntry.scope} readOnly={readOnly || data.paid_paused} onClose={() => setAssistantEntry(null)} />}
        {draft.dataset_import ? <section className="stack" aria-label="Dataset import validation">
          <h3>Dataset import validation</h3>
          <Facts items={[[ 'Report', draft.dataset_import.id ], ['Source', draft.dataset_import.source_name], ['Rows checked', draft.dataset_import.rows], ['Result', draft.dataset_import.state]]} />
          {data.provenance === 'fixture' && <p className="help">Recorded validation example only. No file was uploaded or imported.</p>}
          <p>Validation reports do not approve classes. Proposed rows need individual admin acceptance.</p>
          {draft.dataset_import.errors.length > 0 ? <Table caption="Import rows to fix" columns={['Row', 'Field', 'What to fix']}>
            {draft.dataset_import.errors.map((error, index) => <tr key={`${error.row}:${error.field}:${index}`}><th scope="row">{error.row}</th><td>{error.field}</td><td>{error.message}</td></tr>)}
          </Table> : <Notice tone="info">No row errors reported. This is not class approval.</Notice>}
        </section> : <Notice tone="info">No dataset import validation report is available.</Notice>}
      </>}
      {step === 'Evidence' && <>
        {draft.references.length === 0 ? <Notice tone="info">No reference evidence returned. Adding a link does not approve it.</Notice> : <Table caption="Reference evidence" columns={['Class', 'Source and permitted use', 'View / lighting / scale', 'Often confused with', 'Provenance', 'Actions']}>
          {draft.references.map((reference) => <tr key={reference.id}><th scope="row">{reference.class_id}</th><td>{reference.source} · {reference.permitted_use}</td><td>{reference.view} / {reference.lighting} / {reference.scale}</td><td>{reference.confused_with || 'None'}</td><td><span className={`tag ${reference.provenance === 'Proposed' ? 'proposed' : 'reviewed'}`}>{reference.provenance}</span></td><td>{reference.provenance === 'Proposed' && <CommandButton actions={actions} disabled={readOnly || pendingEdits} onClick={() => { void actions.send({ kind: 'accept_reference', draft_id: draft.id, expected_revision: draft.revision, id: reference.id }); }}>Approve reference {reference.id}</CommandButton>}</td></tr>)}
        </Table>}
        <Notice tone="info">Reference uploads and URL ingestion are not connected in this module. Link metadata capture does not fetch pixels or grant permitted use. New references remain Proposed until separately approved.</Notice>
        <CommandButton actions={actions} disabled={readOnly || pendingEdits} reason={draft.classes.length === 0 ? 'Add a class before linking evidence.' : undefined} onClick={() => setReferenceEntry({ revision: draft.revision, draftId: draft.id })}>Add linked reference</CommandButton>
        {referenceEntry && <ReferenceFields draft={draft} actions={actions} revision={referenceEntry.revision} draftId={referenceEntry.draftId} readOnly={readOnly} onClose={() => setReferenceEntry(null)} />}
      </>}
      {step === 'Labelling guide' && <DraftFields key={`${draft.id}:guide`} draft={draft} actions={actions} step={step} values={draft.guide} readOnly={readOnly} onDirtyChange={setUnsavedFields} labels={{ extent: 'Visible or estimated extent (default visible)', truncation: 'Targets at image edges', occlusion: 'Occluded targets', reflections: 'Reflections', min_target_px: 'Minimum target size in pixels', exhaustive: 'Exhaustive for scope', background: 'Out-of-scope background' }} />}
      {step === 'Sources and groups' && <>
        <Facts items={[[ 'Images / source groups', `${draft.sources.images} / ${draft.sources.groups}` ], ['Split preview', draft.sources.split_summary]]} />
        <DraftFields key={`${draft.id}:sources`} draft={draft} actions={actions} step={step} values={{ metadata_keys: draft.sources.metadata_keys }} readOnly={readOnly} onDirtyChange={setUnsavedFields} labels={{ metadata_keys: 'Declared metadata keys (session, sequence, subject)' }} />
        <p>Declared metadata keys describe grouping constraints, not split assignments. Saving does not decide near-duplicate pairs or approve this step. The server validates the keys, computes transitive groups and invalidates dependent approvals.</p>
        <p>Source uploads and image connections are not connected in this module. Dimensions, hashes and split assignments below are read-only records, not browser calculations.</p>
        {data.provenance === 'fixture' && <p className="help">Invented source facts only. These digests are not hashes of delivered image files.</p>}
        {draft.source_assets == null ? <Notice tone="info">Source asset details are unavailable. Collection totals do not establish image dimensions, hashes or split membership.</Notice> : draft.source_assets.length === 0 ? <Notice tone="info">No source asset details returned.</Notice> : <Table caption={`Returned source asset details (${draft.source_assets.length} of ${draft.sources.images} images)`} columns={['Source image', 'Normalized dimensions', 'SHA-256', 'Source group', 'Split', 'Assignment revision']}>
          {draft.source_assets.map((asset) => <tr key={asset.id}><th scope="row">{asset.id}</th><td>{asset.width} × {asset.height} px</td><td><code>{asset.sha256}</code></td><td>{asset.source_group}</td><td>{asset.split ?? 'Not assigned'}</td><td>{asset.assignment_revision ?? 'Unavailable'}</td></tr>)}
        </Table>}
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
      {stepIndex < setupSteps.length - 1 && !draft.published && <CommandButton actions={actions} primary reason={pendingEdits ? 'Save or discard your changes before approving this step.' : !earlierApproved ? 'Approve the earlier steps first.' : unresolved ? 'Decide all Proposed items or near-duplicate pairs first.' : undefined} onClick={() => { void approve(); }}>Approve {step.toLowerCase()} and continue</CommandButton>}
    </section>
    <div className="stack setup-rail"><nav className="card" aria-label="Setup steps"><details open><summary>Step {stepIndex + 1} of 6 · {step} · All steps</summary><ol className="steps">{setupSteps.map((part, index) => <li key={part} aria-current={index === stepIndex ? 'step' : undefined}><Button disabled={pendingEdits || actions.busy} onClick={() => setStepIndex(index)}><span className="mono">0{index + 1}</span> {part}</Button><span className="tag">{draft.approved_steps.includes(part) ? 'Done' : index === stepIndex ? 'Now' : 'Not approved'}</span></li>)}</ol></details></nav><Notice>Automation stays off for a class until it has enough reviewed examples and passes its pilot. Almond milk needs at least 1 more.</Notice></div></div>
  </>;
}

function AssistantDraft({ draft, actions, revision, draftId, scope, readOnly, onClose }: { draft: ProjectDraft; actions: Actions; revision: number; draftId: string; scope: string; readOnly: boolean; onClose: () => void }) {
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const conflict = draftId !== draft.id || revision !== draft.revision;
  const reason = conflict ? 'The draft changed. Discard this request and inspect the current scope before drafting again.' : !draft.approved_steps.includes('Scope') ? 'Approve the scope before requesting draft classes.' : undefined;
  async function capture() {
    if (readOnly || saving || reason) return;
    setSaving(true);
    const saved = await actions.send({ kind: 'draft_assistant_classes', draft_id: draftId, expected_revision: revision });
    setSaving(false); setFailed(!saved);
    if (saved) onClose();
  }
  return <section className="stack" aria-label="Assistant class draft request">
    <h3>Draft with the assistant</h3>
    <Facts items={[[ 'Inspected scope', scope ], ['Inspected draft', `${draftId} · revision ${revision}`]]} />
    <Notice tone="info">Fixture request capture only. No provider is called and no spend is authorized. Real generation requires the server's permitted-provider, dry-run and budget gates; those are not connected here.</Notice>
    <p>Assistant output is untrusted. Only validated returned rows can enter this draft as Proposed; accepting each class is a separate admin action. No references, source groups, splits or bulk jobs are changed by this request.</p>
    {conflict && <Notice tone="danger">The draft changed while this request was open. No request was retargeted to the new revision.</Notice>}
    {failed && <Notice tone="danger">The assistant draft request was not confirmed. Refresh uncertain records before trying again. No class approval is implied.</Notice>}
    <CommandButton actions={actions} disabled={readOnly || saving} reason={reason} onClick={() => { void capture(); }}>{saving ? 'Capturing assistant request…' : 'Capture assistant draft request'}</CommandButton>
    <Button disabled={saving || actions.busy} onClick={onClose}>Discard assistant request</Button>
  </section>;
}

function ClassFields({ draft, actions, row, revision, draftId, readOnly, onClose }: { draft: ProjectDraft; actions: Actions; row: ClassRow; revision: number; draftId: string; readOnly: boolean; onClose: () => void }) {
  const [values, setValues] = useState(() => ({ name: row.name, aliases: row.aliases, parent: row.parent, exclusions: row.exclusions, mirror_safe: row.mirror_safe }));
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const conflict = draftId !== draft.id || revision !== draft.revision || !draft.classes.some((item) => item.id === row.id);
  const reason = conflict ? 'The draft changed. Discard these edits and inspect the current class before editing again.' : !values.name.trim() ? 'Enter a class name first.' : undefined;
  async function save() {
    if (readOnly || saving || reason) return;
    setSaving(true);
    const saved = await actions.send({ kind: 'edit_class', draft_id: draft.id, expected_revision: revision, id: row.id, values: { ...values, name: values.name.trim() } });
    setSaving(false); setFailed(!saved);
    if (saved) onClose();
  }
  return <form className="stack" aria-label={`Edit class ${row.id}`} onSubmit={(event) => { event.preventDefault(); void save(); }}>
    <h3>Edit {row.name}</h3>
    <Facts items={[[ 'Permanent class ID', row.id ], ['Current provenance', row.provenance]]} />
    <p>Saving does not accept a Proposed class or change its permanent ID. The server invalidates dependent approvals; merging or splitting classes requires a new ontology version.</p>
    {(['name', 'aliases', 'parent', 'exclusions'] as const).map((field) => <Field key={field} name={field} label={{ name: 'Class name', aliases: 'Aliases', parent: 'Parent', exclusions: 'Not this' }[field]} value={values[field]} readOnly={readOnly || saving} onChange={(value) => setValues((current) => ({ ...current, [field]: value }))} />)}
    <label htmlFor="class-mirror-safe">Mirror-safe</label><input id="class-mirror-safe" type="checkbox" checked={values.mirror_safe} disabled={readOnly || saving} onChange={(event) => { const checked = event.currentTarget.checked; setValues((current) => ({ ...current, mirror_safe: checked })); }} />
    {conflict && <Notice tone="danger">The draft changed while you were editing. Your class fields are retained; no overwrite was sent.</Notice>}
    {failed && <Notice tone="danger">Your class change did not save. Fields are retained; refresh uncertain records before trying again.</Notice>}
    <CommandButton actions={actions} disabled={readOnly || saving} reason={reason} onClick={() => { void save(); }}>{saving ? 'Saving class…' : 'Save class changes'}</CommandButton>
    <Button disabled={saving || actions.busy} onClick={onClose}>Discard class edits</Button>
  </form>;
}

function referenceReason(values: ReferenceInput, draft: ProjectDraft) {
  if (!draft.classes.some((row) => row.id === values.class_id)) return 'Choose a class from this draft.';
  if (values.confused_with && (values.confused_with === values.class_id || !draft.classes.some((row) => row.id === values.confused_with))) return 'Choose a different class for Often confused with, or None.';
  try {
    const url = new URL(values.source.trim());
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return 'Enter an HTTP or HTTPS source URL without embedded credentials.';
  } catch { return 'Enter an HTTP or HTTPS source URL without embedded credentials.'; }
  if (!values.permitted_use.trim() || !values.view.trim() || !values.lighting.trim() || !values.scale.trim()) return 'Record permitted use, view, lighting and scale before saving.';
}

function ReferenceFields({ draft, actions, revision, draftId, readOnly, onClose }: { draft: ProjectDraft; actions: Actions; revision: number; draftId: string; readOnly: boolean; onClose: () => void }) {
  const [values, setValues] = useState<ReferenceInput>({ class_id: '', source: '', permitted_use: '', view: '', lighting: '', scale: '', confused_with: '' });
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const conflict = draftId !== draft.id || revision !== draft.revision;
  const reason = conflict ? 'The draft changed. Discard this entry and inspect current records before linking evidence again.' : referenceReason(values, draft);
  function change(field: keyof ReferenceInput, value: string) { setValues((current) => ({ ...current, [field]: value })); }
  async function save() {
    if (readOnly || saving || reason) return;
    setSaving(true);
    const saved = await actions.send({ kind: 'add_reference', draft_id: draftId, expected_revision: revision, values: { ...values, source: values.source.trim(), permitted_use: values.permitted_use.trim(), view: values.view.trim(), lighting: values.lighting.trim(), scale: values.scale.trim() } });
    setSaving(false); setFailed(!saved);
    if (saved) onClose();
  }
  return <form className="stack" aria-label="Link reference evidence" onSubmit={(event) => { event.preventDefault(); void save(); }}>
    <h3>Link reference evidence</h3>
    <div className="field"><label htmlFor="reference-class">Reference class</label><select id="reference-class" value={values.class_id} disabled={readOnly || saving} onChange={(event) => change('class_id', event.currentTarget.value)}><option value="">Choose a class</option>{draft.classes.map((row) => <option key={row.id} value={row.id}>{row.name} · {row.id}</option>)}</select></div>
    {(['source', 'permitted_use', 'view', 'lighting', 'scale'] as const).map((field) => <Field key={field} name={field} label={{ source: 'Reference source URL', permitted_use: 'Reference permitted use', view: 'Reference view', lighting: 'Reference lighting', scale: 'Reference scale' }[field]} value={values[field]} readOnly={readOnly || saving} onChange={(value) => change(field, value)} />)}
    <div className="field"><label htmlFor="reference-confused">Often confused with</label><select id="reference-confused" value={values.confused_with} disabled={readOnly || saving} onChange={(event) => change('confused_with', event.currentTarget.value)}><option value="">None</option>{draft.classes.map((row) => <option key={row.id} value={row.id}>{row.name} · {row.id}</option>)}</select></div>
    <p>Saving records proposed metadata only, not approval or evidence that the source is usable. The server must validate ownership, permitted use and holdout exclusion before ingestion or approval.</p>
    {conflict && <Notice tone="danger">The draft changed while you were linking evidence. Your fields are retained; no overwrite was sent.</Notice>}
    {failed && <Notice tone="danger">Your reference did not save. Fields are retained; refresh uncertain records before trying again.</Notice>}
    <CommandButton actions={actions} disabled={readOnly || saving} reason={reason} onClick={() => { void save(); }}>{saving ? 'Saving reference…' : 'Save proposed reference'}</CommandButton>
    <Button disabled={saving || actions.busy} onClick={onClose}>Discard reference entry</Button>
  </form>;
}

type SetupValues = Readonly<Record<string, string | number | boolean>>;

function DraftFields({ draft, actions, step, values, labels, readOnly, onDirtyChange }: { draft: ProjectDraft; actions: Actions; step: SetupStep; values: SetupValues; labels: Record<string, string>; readOnly: boolean; onDirtyChange: (dirty: boolean) => void }) {
  const [edits, setEdits] = useState<{ revision: number; values: SetupValues } | null>(null);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const conflict = edits !== null && edits.revision !== draft.revision;
  const fields = edits?.values ?? values;
  function change(name: string, value: string | number | boolean) {
    setEdits({ revision: edits?.revision ?? draft.revision, values: { ...fields, [name]: value } });
    onDirtyChange(true);
  }
  async function save() {
    if (readOnly || saving || !edits || conflict) return;
    setSaving(true);
    const saved = await actions.send({ kind: 'save_setup', draft_id: draft.id, expected_revision: edits.revision, step, values: edits.values });
    if (saved) { setEdits(null); onDirtyChange(false); }
    setFailed(!saved); setSaving(false);
  }
  return <form className="stack" onSubmit={(event) => { event.preventDefault(); void save(); }} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) void save(); }}>
    {Object.entries(fields).map(([name, value]) => <div className="field" key={name}><label htmlFor={`setup-${name}`}>{labels[name]}</label>{typeof value === 'boolean' ? <input id={`setup-${name}`} name={name} type="checkbox" checked={value} disabled={readOnly || saving} onChange={(event) => change(name, event.currentTarget.checked)} /> : <input id={`setup-${name}`} name={name} type={typeof value === 'number' ? 'number' : 'text'} min={typeof value === 'number' ? 1 : undefined} value={value} readOnly={readOnly || saving} onChange={(event) => change(name, typeof value === 'number' ? Number(event.currentTarget.value) : event.currentTarget.value)} />}</div>)}
    <p className="help">{saving ? 'Saving…' : !actions.connected ? 'Recorded draft. Editing and autosave need the authenticated management client.' : edits ? 'Unsaved changes. Save before approving this step or leaving it.' : 'Changes save when you leave these fields.'}</p>
    {conflict && <Notice tone="danger">The draft changed while you were editing. Your local fields are retained; no overwrite was sent.</Notice>}
    {edits && <><CommandButton actions={actions} disabled={readOnly || saving} reason={conflict ? 'Resolve the changed revision before saving.' : undefined} onClick={() => { void save(); }}>Save changes</CommandButton><Button disabled={saving || actions.busy} onClick={() => { setEdits(null); setFailed(false); onDirtyChange(false); }}>Discard local changes and load current revision</Button></>}
    {failed && <Notice tone="danger">Your last change did not save. Try again.<Button type="submit" disabled={conflict}>Try again</Button></Notice>}
  </form>;
}
