import { useState } from 'react';
import type { ScreenProps } from './contracts.ts';
import { Button, CommandButton, Download, Facts, Notice, PageHeading, ReadOnly, Table, Tile, Utc } from './shared.tsx';

export function ReleasesView({ data, actions }: ScreenProps) {
  const [confirm, setConfirm] = useState<{ id: string; revision: number } | null>(null);
  const [details, setDetails] = useState(false);
  const release = data.release;
  if (!release) return <><PageHeading title="Dataset releases." gloss="Freeze the canonical record." /><ReadOnly role={actions.role} /><Notice tone="info">No release yet. Complete annotation review before building a release.</Notice></>;
  const frozen = release.state === 'Frozen';
  const reason = release.state === 'Building' ? 'Wait until the release finishes building.' : release.failed_checks.length ? release.failed_checks.join(' ') : release.identical ? `${release.id} would be identical to ${release.previous_id}.` : release.quarantine ?? undefined;
  const total = release.provenance.reviewed + release.provenance.auto_accepted;
  return <>
    <PageHeading title={`Release ${release.id}.`} gloss={frozen ? 'Frozen. Nothing in it can change.' : `Draft, compared with ${release.previous_id}.`} lede="A release freezes exactly which images and label revisions go into training and evaluation." meta={<>Built from {release.images.toLocaleString()} images · compare with {release.previous_id}</>}>
      <Download name={`${release.id}-fixture-manifest.json`} value={release}>Download {data.provenance === 'fixture' ? 'fixture ' : ''}manifest</Download>
      {!frozen && !confirm && <CommandButton actions={actions} primary reason={reason} onClick={() => setConfirm({ id: release.id, revision: release.revision })}>Freeze {release.id}</CommandButton>}
    </PageHeading><ReadOnly role={actions.role} />
    {confirm && !frozen && <section className="confirm"><h2>Freeze {confirm.id}?</h2><p>Nothing in it can change after this.</p><div className="acts"><CommandButton actions={actions} primary reason={confirm.id !== release.id || confirm.revision !== release.revision ? 'The release changed. Cancel and inspect the current revision before confirming again.' : reason} onClick={() => { void actions.send({ kind: 'freeze_release', release_id: confirm.id, expected_revision: confirm.revision }).then((ok) => { if (ok) setConfirm(null); }); }}>Freeze</CommandButton><Button onClick={() => setConfirm(null)}>Cancel</Button></div></section>}
    {release.failed_checks.length > 0 && <Notice tone="danger"><p>Compiler checks failed. Freeze is unavailable.</p><ul>{release.failed_checks.map((check) => <li key={check}>{check}</li>)}</ul></Notice>}
    {release.quarantine && <Notice>{release.quarantine}</Notice>}
    {release.identical && <Notice tone="info">{release.id} would be identical to {release.previous_id}.</Notice>}
    {frozen && <section className="card"><Facts items={[[ 'Manifest SHA-256', release.manifest_sha256 ], ['Frozen by', release.frozen_by ?? 'Recorded principal unavailable'], ['Frozen at', release.frozen_at ? <Utc value={release.frozen_at} /> : 'Time unavailable']]} /><a href="?screen=recipes">Compile recipes</a></section>}
    <div className="tiles" aria-busy={release.state === 'Building'}>
      {[[release.images, 'images', release.deltas[0]], [release.objects, 'objects', release.deltas[1]], [release.excluded, 'excluded', release.deltas[2]]].map(([value, label, delta]) => <Tile key={label} value={release.state === 'Building' ? 'Building…' : Number(value).toLocaleString()} label={String(label)}><span className="mono small">{Number(delta) > 0 ? '+' : ''}{delta} since {release.previous_id}</span></Tile>)}
      <Tile value={`${release.complete} of ${release.images}`} label="included images checked complete" />
    </div>
    {release.state === 'Building' && <p>Running · {release.progress.done} of {release.progress.total} images</p>}
    <div className="grid2"><div className="stack"><section className="card"><h2>Where the labels came from</h2><p className="label">Training split only · {total.toLocaleString()} labels</p><div className="keyrow"><span>✓ Reviewed by a person · {release.provenance.reviewed.toLocaleString()}</span><span>◇ Auto-accepted, policy {release.provenance.policy} · {release.provenance.auto_accepted.toLocaleString()}</span></div>
      <div className="dist" aria-hidden="true"><span className="reviewed-fill" style={{ width: `${total ? release.provenance.reviewed / total * 100 : 0}%` }} /><span className="auto-fill" style={{ width: `${total ? release.provenance.auto_accepted / total * 100 : 0}%` }} /></div><p>Validation and test use reviewed labels only. Proposed and uncertain labels never enter a release.</p></section>
      <section className="card"><h2>Class distribution</h2><p className="small">{release.id} · {release.images} images · counts by split, not model predictions</p><Table caption="Objects per class and split" columns={['Class', 'Train', 'Val', 'Test', 'Change']}>
        {release.classes.map((row) => <tr key={row.name}><th scope="row">{row.name}</th><td>{row.train}</td><td>{row.val}</td><td>{row.test}</td><td>{row.delta > 0 ? '+' : ''}{row.delta}</td></tr>)}
      </Table>{release.classes.filter((row) => row.test < row.minimum).map((row) => <Notice key={row.name}>{row.name}: {row.test} test objects, under the {row.minimum} the gate needs. Unsupported in evaluation; no filled-in metric.</Notice>)}</section></div>
      <aside className="stack"><section className="card"><h2>Changes since {release.previous_id}</h2><Facts items={[[ 'Images', `${release.deltas[0]} net` ], ['Objects', `${release.deltas[1]} net`], ['Ontology', data.draft.versions.ontology], ['Split groups', data.draft.versions.splits]]} /><Button onClick={() => setDetails(!details)} aria-expanded={details}>See every changed image</Button>{details && <Table caption="Changed canonical images" columns={['Image', 'Change']}>
        {release.changes.map((change) => <tr key={change.image_id}><th scope="row">{change.image_id}</th><td>{change.description}</td></tr>)}
      </Table>}</section><section className="card"><h2>Excluded · {release.excluded}</h2><ul className="hist">{release.exclusions.map((exclusion) => <li key={exclusion.reason}><details><summary>{exclusion.count} images · {exclusion.reason}</summary><p>Recorded examples: {exclusion.image_ids.join(', ')}. Full image review opens in the shared review shell after integration.</p></details></li>)}</ul></section></aside></div>
  </>;
}
