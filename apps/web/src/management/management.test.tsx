import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Actions, ManagementSnapshot } from './contracts.ts';
import { EvaluationView } from './EvaluationView.tsx';
import { ModelsView } from './ModelsView.tsx';
import { NewProjectView } from './NewProjectView.tsx';
import { RecipesView } from './RecipesView.tsx';
import { ReleasesView } from './ReleasesView.tsx';
import { RunsView } from './RunsView.tsx';
import { createFixturePort, fixtureSnapshot, previews, recipe640 } from './fixtures.ts';

const actions: Actions = { role: 'admin', connected: true, busy: false, send: () => Promise.resolve(false) };
const views = [NewProjectView, ReleasesView, RecipesView, RunsView, EvaluationView, ModelsView];
const render = (View: typeof views[number], data = fixtureSnapshot, role: Actions['role'] = 'admin') => renderToStaticMarkup(<View data={data} actions={{ ...actions, role }} />);

describe('management fixture read port', () => {
  it('has no mutation capability and returns independent immutable evidence copies', async () => {
    const port = createFixturePort();
    expect(port.execute).toBeUndefined();
    const first = await port.read(new AbortController().signal);
    const second = await port.read(new AbortController().signal);
    first.draft.scope.name = 'Modified local copy';
    expect(second.draft.scope.name).toBe('Shelf audit');
    expect(second.provenance).toBe('fixture');
  });
  it('honours cancellation without making any network request', async () => {
    const controller = new AbortController(); controller.abort();
    await expect(createFixturePort().read(controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
  });
  it('keeps release counts and training provenance consistent', () => {
    const release = fixtureSnapshot.release!;
    expect(release.classes.reduce((sum, row) => sum + row.train + row.val + row.test, 0)).toBe(release.objects);
    expect(release.provenance.reviewed + release.provenance.auto_accepted).toBe(release.classes.reduce((sum, row) => sum + row.train, 0));
  });
});

describe('screen contracts and role rendering', () => {
  it.each(views)('renders the reviewer read-only without mutation controls: %s', (View) => {
    const html = render(View, fixtureSnapshot, 'reviewer');
    expect(html).toContain('Read-only. Only an admin');
    expect(html).not.toMatch(/>Approve .*continue<|>Accept Almond|>Freeze r0008<|>Start a run<|>Promote m-|>Roll back 640/);
  });
  it('preserves proposed catalog and evidence provenance', () => {
    const html = render(NewProjectView);
    expect(html).toContain('Proposed');
    expect(html).toContain('Accept Almond milk 1 L');
    expect(html).toContain('Decide all Proposed items');
  });
  it('never offers edits to a frozen canonical release', () => {
    const data = structuredClone(fixtureSnapshot);
    data.release!.state = 'Frozen';
    data.release!.frozen_at = data.updated_at;
    data.release!.frozen_by = 'Fixture admin';
    const html = render(ReleasesView, data);
    expect(html).toContain('Manifest SHA-256');
    expect(html).not.toContain('>Freeze r0008<');
    expect(html).not.toContain('>Edit');
  });
  it('shows compiler failure reasons and separate accepted-label counts', () => {
    const data = structuredClone(fixtureSnapshot);
    data.release!.failed_checks = ['Val split is empty for 320'];
    const html = render(ReleasesView, data);
    expect(html).toContain('Val split is empty for 320');
    expect(html).toContain('Reviewed by a person');
    expect(html).toContain('Auto-accepted, policy P-2');
    expect(html).toContain('Proposed and uncertain labels never enter');
  });
  it('uses recorded box geometry, explicitly excludes the below-minimum image and locks v1', () => {
    const html = render(RecipesView);
    expect(html).toContain('Make v2');
    expect(html).toContain('Exclude this image from 320 unless recropped');
    expect(html).toContain('fixture-transforms-320-v1');
    expect(html).toContain('grey 114');
    expect(html).toContain('frame-320');
  });
  it('does not misrepresent an invented image as a server-delivered source', () => {
    const data = structuredClone(fixtureSnapshot); data.provenance = 'server';
    expect(render(RecipesView, data)).not.toContain('<image');
  });
  it('displays local fixed-shape selection, retention and hash facts without paid controls', () => {
    const html = render(RunsView);
    expect(html).toContain('development-v3');
    expect(html).toContain('n=266 frames');
    expect(html).toContain('12410 of 12418');
    expect(html).toContain('Targets were dropped');
    expect(html).not.toContain('Start · estimate');
  });
  it('shows every mismatched argument in words', () => {
    const data = structuredClone(fixtureSnapshot); data.runs = [data.runs[1]];
    const html = render(RunsView, data);
    expect(html).toContain('fliplr'); expect(html).toContain('scale'); expect(html).toContain('missing');
  });
  it('renders no numeric fallback for unsupported classes, even if an upstream flag is wrong', () => {
    const data = structuredClone(fixtureSnapshot);
    data.evaluations = [{ ...data.evaluations[0], classes: [{ name: 'Unsupported sentinel', support: 0, minimum: 100, supported: true, metric: { current: 0.99871, candidate: 0.99872 } }] }];
    const html = render(EvaluationView, data);
    expect(html).toContain('Unsupported');
    expect(html).not.toContain('0.99871'); expect(html).not.toContain('0.99872');
  });
  it('names the set and n, separates recipes and labels provider diagnostics', () => {
    const html = render(EvaluationView);
    expect(html).toContain('t-2'); expect(html).toContain('n=1114 frames');
    expect(html).toContain('320'); expect(html).toContain('640');
    expect(html).toContain('Diagnostic only');
    expect(html).toContain('Open t-2 again');
  });
  it('renders complete atomic release fields and never invents subset qualification', () => {
    const html = render(ModelsView);
    for (const label of ['Weights SHA-256', 'Preprocessing SHA-256', 'Class-map SHA-256', 'Confidence threshold', 'Tiling', 'Export SHA-256', 'Ultralytics / Torch', 'Evaluations']) expect(html).toContain(label);
    expect(html).toContain('Almond milk is unsupported');
    expect(html).not.toContain('>Qualifies<');
  });
  it('accepts recipe-specific operational extensions without duplicating the registry', () => {
    const html = renderToStaticMarkup(<ModelsView data={fixtureSnapshot} actions={actions} extensions={{ render: (id) => <p>Operational slot: {id}</p> }} />);
    expect(html.match(/Operational slot:/g)).toHaveLength(2);
    expect(html).toContain(recipe640);
  });
  it('handles empty collections on every collection screen', () => {
    const data: ManagementSnapshot = { ...fixtureSnapshot, release: null, recipes: [], runs: [], evaluations: [], registry: [] };
    for (const View of views.slice(1)) expect(render(View, data)).toMatch(/No |needs a release/);
  });
  it('escapes class names and model-supplied text as text, never markup', () => {
    const data = structuredClone(fixtureSnapshot);
    data.draft.classes = [{ ...data.draft.classes[0], name: '<img src=x onerror=alert(1)>' }];
    expect(render(NewProjectView, data)).toContain('&lt;img');
    expect(render(NewProjectView, data)).not.toContain('<img src=x');
  });
});

describe('recorded original-frame recipe geometry', () => {
  it('keeps shared source and direct-from-original gains for both recipes', () => {
    expect(previews.map((record) => [record.side, record.resized_width, record.resized_height, ...record.padding])).toEqual([[640, 640, 360, 0, 140, 0, 140], [320, 320, 180, 0, 70, 0, 70]]);
    for (const recipe of previews) {
      expect(recipe.sx).toBe(recipe.resized_width / recipe.source_width);
      expect(recipe.sy).toBe(recipe.resized_height / recipe.source_height);
      for (const box of recipe.boxes) {
        expect(box.xyxy[0]).toBeGreaterThanOrEqual(recipe.padding[0]);
        expect(box.xyxy[2]).toBeLessThanOrEqual(recipe.side - recipe.padding[2]);
      }
    }
  });
});
