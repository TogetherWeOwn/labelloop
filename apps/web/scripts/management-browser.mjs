import assert from 'node:assert/strict';
import { mkdir, writeFile, readFile, readdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { chromium } from '@playwright/test';
import { preview } from 'vite';

const require = createRequire(import.meta.url);
const evidence = 'management-evidence';
await mkdir(evidence, { recursive: true });
const server = await preview({ configFile: 'vite.management.config.ts', preview: { host: '127.0.0.1', port: 4187, strictPort: true } });
const browser = await chromium.launch();
const results = [];
const failures = [];
const pages = ['new-project', 'releases', 'recipes', 'runs', 'evaluation', 'models'];
const states = ['ready', 'loading', 'empty', 'error', 'stale'];
const origin = 'http://127.0.0.1:4187';
const entry = `${origin}/management-fixture.html`;
try {
  for (const width of [1280, 390]) {
    for (const role of ['admin', 'reviewer']) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, reducedMotion: 'reduce' });
      const errors = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.route('**/*', (route) => {
        const url = new URL(route.request().url());
        if (url.origin === origin || url.protocol === 'data:') return route.continue();
        errors.push(`Unexpected external request to ${url.origin}`);
        return route.abort();
      });
      for (const screen of pages) {
        for (const state of states) {
          const key = `${screen}-${role}-${width}-${state}`;
          try {
            await page.goto(`${entry}?screen=${screen}&role=${role}&state=${state}`);
            await page.locator('main h1').waitFor();
            if (!['loading', 'error'].includes(state)) await page.getByText('Recorded fixture preview', { exact: false }).waitFor();
            if (state === 'loading') await page.getByText('Loading records…').waitFor();
            if (state === 'stale') await page.getByText('Stale data', { exact: false }).waitFor();
            if (state === 'error') await page.getByRole('button', { name: 'Try again' }).waitFor();
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, `${key}: page overflow`);
            assert.equal(await page.getByRole('link', { name: 'Get the source code.' }).count(), 1);
            if (role === 'reviewer') {
              assert.equal(await page.getByRole('button', { name: /^(Approve .*continue|Accept Almond|Freeze r|Start a run|Promote m-|Roll back)/ }).count(), 0, `${key}: forbidden control`);
            }
            await page.keyboard.press('Tab');
            assert.equal(await page.locator(':focus').innerText(), 'Skip to content', `${key}: skip link first`);
            await page.keyboard.press('Enter');
            await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
            const violations = await page.evaluate(async () => (await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } })).violations.map((item) => ({ id: item.id, impact: item.impact, nodes: item.nodes.map((node) => node.target) })));
            assert.deepEqual(violations, [], `${key}: ${JSON.stringify(violations)}`);
            assert.deepEqual(errors, [], `${key}: browser errors`);
            if (state === 'ready' && role === 'admin') await page.screenshot({ path: `${evidence}/${key}.png`, fullPage: true });
            results.push({ key, pass: true, axe: 0, keyboard: 'skip-to-main', overflow: false });
          } catch (error) {
            failures.push({ key, error: String(error) });
            await page.screenshot({ path: `${evidence}/${key}-failed.png`, fullPage: true });
          }
        }
      }
      await page.goto(`${entry}?screen=new-project&role=${role}`);
      await page.getByRole('navigation', { name: 'Setup steps' }).waitFor();
      for (const step of ['Scope', 'Classes', 'Evidence', 'Labelling guide', 'Sources and groups', 'Check and publish']) {
        const key = `setup-${step}-${role}-${width}`;
        try {
          const button = page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: new RegExp(step) });
          await button.focus();
          await page.keyboard.press('Enter');
          await page.getByRole('heading', { level: 2, name: new RegExp(step) }).waitFor();
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, `${key}: page overflow`);
          await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
          const violations = await page.evaluate(async () => (await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } })).violations.map((item) => ({ id: item.id, nodes: item.nodes.map((node) => node.target) })));
          assert.deepEqual(violations, [], `${key}: ${JSON.stringify(violations)}`);
          assert.deepEqual(errors, [], `${key}: browser errors`);
          if (role === 'admin') await page.screenshot({ path: `${evidence}/${key}.png`, fullPage: true });
          results.push({ key, pass: true, axe: 0, keyboard: 'step-enter', overflow: false });
        } catch (error) { failures.push({ key, error: String(error) }); }
      }
      await page.close();
    }
  }
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  // The command harness only captures payloads. It has no API, auth or training capability.
  const open = (screen) => page.goto(`${entry}?screen=${screen}&state=command-contract`);
  const last = () => page.evaluate(() => JSON.parse(document.documentElement.dataset.lastCommand || 'null'));
  await open('releases');
  // Frozen records must not expose Freeze, even with a connected test callback.
  assert.equal(await page.getByRole('button', { name: 'Freeze r0008' }).count(), 0);
  await page.goto(`${entry}?screen=releases&state=command-contract&release=draft`);
  await page.getByRole('button', { name: 'Freeze r0008', exact: true }).click();
  assert.equal(await last(), null, 'opening freeze confirmation must not mutate');
  await page.getByRole('button', { name: 'Freeze', exact: true }).click();
  assert.deepEqual(await last(), { kind: 'freeze_release', release_id: 'r0008', expected_revision: 2 });
  await open('recipes');
  const frames = page.locator('.recipe-image');
  const widths = await frames.evaluateAll((elements) => elements.map((element) => element.getBoundingClientRect().width));
  assert.ok(Math.abs(widths[0] / widths[1] - 2) < 0.01, 'shared-scale preview must keep 640 twice 320');
  const geometry = await page.locator('figure[data-recipe]').evaluateAll((figures) => figures.map((figure) => ({
    recipe: figure.dataset.recipe,
    side: Number(figure.querySelector('svg').viewBox.baseVal.width),
    boxes: [...figure.querySelectorAll('rect[data-object]')].map((box) => ({ id: box.dataset.object, x: Number(box.getAttribute('x')), y: Number(box.getAttribute('y')), width: Number(box.getAttribute('width')), height: Number(box.getAttribute('height')) })),
  })));
  await writeFile(`${evidence}/recipe-dom.json`, JSON.stringify(geometry, null, 2));
  await page.getByRole('button', { name: 'Compile r0008 with 2 recipes' }).click();
  assert.deepEqual(await last(), { kind: 'compile', release_id: 'r0008', recipe_ids: ['full-letterbox-640-v1', 'full-letterbox-320-v1'] });
  await open('runs');
  await page.getByRole('button', { name: 'Start a run', exact: true }).click();
  await page.getByLabel('Epochs', { exact: true }).fill('3');
  await page.getByRole('button', { name: 'Start run on this server', exact: true }).click();
  assert.deepEqual(await last(), { kind: 'start_local_run', release_id: 'r0008', recipe_id: 'full-letterbox-640-v1', initialization_sha256: 'f'.repeat(64), epochs: 3 });
  await page.getByRole('button', { name: 'Cancel run', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm cancel run', exact: true }).click();
  assert.deepEqual(await last(), { kind: 'cancel_local_run', run_id: 'run-0031' });
  await open('evaluation');
  await page.getByRole('button', { name: '320', exact: true }).click();
  assert.match(await page.locator('h1').innerText(), /320/);
  await page.getByRole('button', { name: 'Open t-2 again' }).click();
  await page.getByRole('button', { name: 'Confirm open as development data' }).click();
  assert.deepEqual(await last(), { kind: 'open_locked_test', evaluation_id: 'eval-0045', acknowledge_development_reuse: true });
  await open('models');
  await page.getByRole('button', { name: 'Promote m-0009 for 640' }).click();
  assert.equal(await last(), null, 'opening promotion must not mutate');
  await page.getByRole('button', { name: 'Confirm promotion', exact: true }).click();
  assert.deepEqual(await last(), { kind: 'promote', project_id: 'shelf-fixture', recipe_id: 'full-letterbox-640-v1', expected_revision: 4, release_id: 'm-0009' });
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: 'Roll back 320 to m-0004' }).click();
  assert.equal(await page.getByRole('button', { name: 'Confirm rollback' }).isDisabled(), true);
  await page.getByLabel('Reason for rollback').fill('Fixture drift evidence');
  await page.getByRole('button', { name: 'Confirm rollback' }).click();
  assert.deepEqual(await last(), { kind: 'rollback', project_id: 'shelf-fixture', recipe_id: 'full-letterbox-320-v1', expected_revision: 4, release_id: 'm-0004', reason: 'Fixture drift evidence' });
  await open('new-project');
  await page.getByRole('table', { name: 'Import rows to fix' }).waitFor();
  assert.match(await page.getByRole('table', { name: 'Import rows to fix' }).innerText(), /14[\s\S]*Class name is empty/);
  assert.match(await page.getByRole('region', { name: 'Dataset import validation' }).innerText(), /Recorded validation example only/);
  assert.equal(await page.getByRole('button', { name: 'Approve classes and continue', exact: true }).isDisabled(), true, 'a validation report is not class approval');
  assert.equal(await last(), null, 'reading import validation must not mutate');
  await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Sources and groups/ }).click();
  const sourceFacts = page.getByRole('table', { name: 'Returned source asset details (2 of 1326 images)', exact: true });
  await sourceFacts.waitFor();
  assert.match(await sourceFacts.innerText(), /1920 × 1080 px/);
  assert.match(await sourceFacts.innerText(), /Not assigned/);
  assert.match(await sourceFacts.innerText(), new RegExp('1'.repeat(64)));
  await page.getByRole('button', { name: 'Compare source-0041 and source-0042', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: 'Same group', exact: true }).isDisabled(), true, 'metadata is not pair-image evidence');
  assert.equal(await page.getByRole('button', { name: 'Different groups', exact: true }).isDisabled(), true);
  assert.equal(await last(), null, 'inspecting source facts must not assign groups or splits');
  await page.goto(`${entry}?screen=new-project&state=source-details-missing`);
  await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Sources and groups/ }).click();
  await page.getByText('Source asset details are unavailable.', { exact: false }).waitFor();
  assert.equal(await page.getByRole('table', { name: /Returned source asset details/ }).count(), 0, 'missing details must not synthesize records from totals');
  await page.goto(`${entry}?screen=new-project&state=empty`);
  await page.getByText('No dataset import validation report is available.', { exact: true }).waitFor();
  await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Sources and groups/ }).click();
  await page.getByText('No source asset details returned.', { exact: true }).waitFor();
  results.push({ key: 'setup-read-only-ingestion-evidence', pass: true, realCommands: false });
  await open('new-project');
  await page.getByRole('button', { name: 'Accept Almond milk 1 L', exact: true }).click();
  assert.deepEqual(await last(), { kind: 'accept_class', draft_id: 'draft-shelf', expected_revision: 7, id: 'almond' });
  for (const step of ['Scope', 'Classes', 'Evidence', 'Labelling guide', 'Sources and groups', 'Check and publish']) {
    await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: new RegExp(step) }).click();
    await page.getByRole('heading', { level: 2, name: new RegExp(step) }).waitFor();
  }
  results.push({ key: 'command-contracts', pass: true, commands: ['compile', 'start-local', 'cancel-local', 'locked-test', 'promote', 'rollback', 'accept-class'], realCommands: false });
  for (const state of ['dispatch-ready', 'dispatch-fixture', 'dispatch-stale', 'dispatch-failed']) {
    await page.goto(`${entry}?screen=new-project&state=${state}`);
    const accept = page.getByRole('button', { name: 'Accept Almond milk 1 L', exact: true });
    await accept.waitFor();
    if (state === 'dispatch-fixture' || state === 'dispatch-stale') {
      assert.equal(await accept.isDisabled(), true, `${state}: dispatch must fail closed`);
      assert.equal(await last(), null);
    } else {
      await accept.click();
      await page.getByRole('status').filter({ hasText: state === 'dispatch-ready' ? 'Command completed' : 'The change could not be confirmed' }).waitFor();
      assert.deepEqual(await last(), { kind: 'accept_class', draft_id: 'draft-shelf', expected_revision: 7, id: 'almond' });
      if (state === 'dispatch-failed') {
        assert.equal(await accept.isDisabled(), true, 'uncertain command requires a refresh before retry');
        await page.getByRole('button', { name: 'Try again', exact: true }).click();
        await page.waitForFunction(() => ![...document.querySelectorAll('button')].find((button) => button.textContent === 'Accept Almond milk 1 L')?.disabled);
      }
    }
  }
  await page.goto(`${entry}?screen=new-project&state=dispatch-ready&role=reviewer`);
  await page.getByText('Read-only. Only an admin', { exact: false }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Accept Almond milk 1 L', exact: true }).count(), 0);
  assert.equal(await last(), null);
  results.push({ key: 'module-dispatch-guards', pass: true, realCommands: false });
  for (const pendingCommand of [false, true]) {
    await page.goto(`${entry}?state=port-lifecycle`);
    const accept = page.getByRole('button', { name: 'Accept Almond milk 1 L', exact: true });
    await accept.waitFor();
    if (pendingCommand) {
      await accept.click();
      assert.equal((await last()).draft_id, 'draft-A');
    }
    await page.getByRole('button', { name: 'Replace port', exact: true }).click();
    await page.getByText('Loading records…', { exact: true }).waitFor();
    assert.equal(await accept.count(), 0, 'old snapshot must disappear before the replacement read');
    await page.getByRole('button', { name: 'Finish replacement read', exact: true }).click();
    await accept.waitFor();
    assert.equal(await accept.isEnabled(), true, 'aborting the previous command must not leave the new port busy');
    await accept.click();
    await page.getByRole('status').filter({ hasText: 'Command completed' }).waitFor();
    assert.equal((await last()).draft_id, 'draft-B');
  }
  await page.goto(`${entry}?screen=releases&state=refresh-lifecycle`);
  await page.getByRole('button', { name: 'Freeze r0008', exact: true }).click();
  await page.getByRole('button', { name: 'Refresh external records', exact: true }).click();
  await page.getByText('The release changed.', { exact: false }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Freeze', exact: true }).isDisabled(), true);
  assert.equal(await last(), null, 'refresh must not silently retarget the confirmation');
  await page.goto(`${entry}?screen=evaluation&state=refresh-lifecycle`);
  await page.getByText('No evaluation yet.', { exact: false }).waitFor();
  await page.getByRole('button', { name: 'Refresh external records', exact: true }).click();
  await page.getByRole('group', { name: 'Evaluation recipe' }).waitFor();
  assert.equal(await page.getByRole('button', { name: '640', exact: true }).getAttribute('aria-pressed'), 'true');
  await page.goto(`${entry}?screen=new-project&state=refresh-lifecycle`);
  await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Scope/ }).click();
  const name = page.getByLabel('Project name', { exact: true });
  await name.fill('Unsaved local name');
  assert.equal(await page.getByRole('button', { name: 'Approve scope and continue', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: 'Refresh external records', exact: true }).evaluate((button) => button.click());
  await page.getByText('The draft changed while you were editing.', { exact: false }).waitFor();
  assert.equal(await name.inputValue(), 'Unsaved local name');
  assert.equal(await last(), null, 'conflicted edits must not overwrite the newer revision');
  await page.getByRole('button', { name: 'Discard local changes and load current revision', exact: true }).click();
  assert.equal(await name.inputValue(), 'Changed in another session');
  await name.fill('Saved local name');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  assert.equal((await last()).kind, 'save_setup');
  assert.equal(await page.getByRole('button', { name: 'Approve scope and continue', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: 'Finish pending save', exact: true }).click();
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find((button) => button.textContent === 'Approve scope and continue')?.disabled);
  await page.getByRole('button', { name: 'Approve scope and continue', exact: true }).click();
  await page.getByRole('heading', { level: 2, name: /Classes/ }).waitFor();
  const commands = await page.evaluate(() => JSON.parse(document.documentElement.dataset.commands || '[]'));
  assert.deepEqual(commands.map((command) => command.kind), ['save_setup', 'approve_step']);
  assert.equal(commands[1].expected_revision, commands[0].expected_revision + 1);
  results.push({ key: 'refresh-and-port-lifecycles', pass: true, realCommands: false });
  const assistantPanel = page.getByRole('region', { name: 'Assistant class draft request', exact: true });
  const openAssistant = page.getByRole('button', { name: 'Draft with the assistant', exact: true });
  const captureAssistant = page.getByRole('button', { name: 'Capture assistant draft request', exact: true });
  const discardAssistant = page.getByRole('button', { name: 'Discard assistant request', exact: true });
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await open('new-project');
    const initialClasses = await page.getByRole('table', { name: 'Classes in this draft', exact: true }).innerText();
    await openAssistant.focus(); await page.keyboard.press('Enter');
    await assistantPanel.waitFor();
    assert.equal(await last(), null, 'opening the assistant panel must not dispatch');
    assert.match(await assistantPanel.innerText(), /No provider is called and no spend is authorized/);
    assert.equal(await page.getByRole('button', { name: 'Accept Almond milk 1 L', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: 'Add class', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Scope/ }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: 'Approve classes and continue', exact: true }).isDisabled(), true);
    await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
    const violations = await page.evaluate(async () => (await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } })).violations.map((item) => ({ id: item.id, nodes: item.nodes.map((node) => node.target) })));
    assert.deepEqual(violations, [], `assistant-request-${width}: axe`);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.screenshot({ path: `${evidence}/assistant-request-${width}.png`, fullPage: true });
    await captureAssistant.focus(); await page.keyboard.press('Enter');
    await assistantPanel.getByText('The assistant draft request was not confirmed.', { exact: false }).waitFor();
    assert.deepEqual(await last(), { kind: 'draft_assistant_classes', draft_id: 'draft-shelf', expected_revision: 7 });
    assert.equal(await page.getByRole('table', { name: 'Classes in this draft', exact: true }).innerText(), initialClasses, 'failed request never invents or approves rows');
    await discardAssistant.focus(); await page.keyboard.press('Enter');
    assert.equal(await assistantPanel.count(), 0);
    results.push({ key: `assistant-request-${width}`, pass: true, axe: 0, keyboard: 'open-capture-discard-enter', realCommands: false });
  }
  await page.goto(`${entry}?screen=new-project&state=refresh-lifecycle`);
  const oatRows = page.getByRole('table', { name: 'Classes in this draft', exact: true }).getByRole('row').filter({ has: page.getByRole('rowheader', { name: 'Oat milk 1 L', exact: true }) });
  await oatRows.waitFor();
  assert.equal(await oatRows.count(), 1, 'the fixture starts with one approved class sharing the proposal display name');
  const approvedOat = await oatRows.innerText();
  assert.match(approvedOat, /Approved by an admin/);
  await openAssistant.click();
  await page.getByRole('button', { name: 'Refresh external records', exact: true }).click();
  await assistantPanel.getByText('The draft changed while this request was open.', { exact: false }).waitFor();
  assert.match(await assistantPanel.innerText(), /draft-shelf · revision 7/);
  assert.equal(await captureAssistant.isDisabled(), true);
  assert.equal(await last(), null, 'an open assistant request must not retarget a refreshed revision');
  await discardAssistant.click(); await openAssistant.click(); await captureAssistant.click();
  assert.deepEqual(await last(), { kind: 'draft_assistant_classes', draft_id: 'draft-shelf', expected_revision: 8 });
  assert.equal(await page.getByRole('button', { name: 'Capturing assistant request…', exact: true }).isDisabled(), true);
  assert.equal(await discardAssistant.isDisabled(), true, 'cannot discard an in-flight request');
  assert.equal(await page.getByRole('button', { name: 'Accept Almond milk 1 L', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: 'Finish pending save', exact: true }).click();
  const acceptAssistant = page.getByRole('button', { name: 'Accept Oat milk 1 L', exact: true });
  await acceptAssistant.waitFor();
  assert.equal(await assistantPanel.count(), 0);
  const proposedRow = oatRows.filter({ has: acceptAssistant });
  assert.equal(await oatRows.count(), 2, 'the proposal is added without replacing the existing same-name class');
  assert.equal(await proposedRow.count(), 1, 'the returned proposal must appear exactly once in the draft catalog');
  assert.match(await proposedRow.innerText(), /Proposed/);
  assert.equal(await oatRows.filter({ hasNot: acceptAssistant }).innerText(), approvedOat, 'drafting must not alter the existing approved class');
  assert.equal(await page.getByRole('button', { name: 'Approve classes and continue', exact: true }).isDisabled(), true);
  assert.equal((await last()).kind, 'draft_assistant_classes', 'returning a proposal never accepts it');
  await acceptAssistant.click();
  assert.deepEqual(await last(), { kind: 'accept_class', draft_id: 'draft-shelf', expected_revision: 9, id: 'assistant-oat' });
  const assistantCommands = await page.evaluate(() => JSON.parse(document.documentElement.dataset.commands || '[]'));
  assert.deepEqual(assistantCommands.map((command) => command.kind), ['draft_assistant_classes', 'accept_class'], 'drafting never approves a step, starts bulk work or changes splits');
  results.push({ key: 'assistant-revision-proposal-and-separate-acceptance', pass: true, realCommands: false });
  for (const state of ['ready', 'dispatch-fixture', 'dispatch-stale', 'dispatch-published', 'dispatch-unapproved-scope', 'dispatch-paid-paused']) {
    await page.goto(`${entry}?screen=new-project&state=${state}`);
    await openAssistant.waitFor();
    assert.equal(await openAssistant.isDisabled(), true, `${state}: assistant capture fails closed`);
    assert.equal(await last(), null);
  }
  await page.goto(`${entry}?screen=new-project&state=dispatch-ready&role=reviewer`);
  await page.getByText('Read-only. Only an admin', { exact: false }).waitFor();
  assert.equal(await openAssistant.count(), 0);
  await page.goto(`${entry}?screen=new-project&state=refresh-lifecycle`);
  await openAssistant.click();
  await page.getByRole('button', { name: 'Pause paid steps', exact: true }).click();
  await page.getByText('Paid steps paused.', { exact: false }).waitFor();
  assert.equal(await captureAssistant.isDisabled(), true, 'a paid-pause refresh closes the dispatch gate even on an open panel');
  assert.equal(await last(), null);
  await discardAssistant.click();
  results.push({ key: 'assistant-role-provenance-scope-and-paid-pause-guards', pass: true, realCommands: false });
  const sourcesStep = () => page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Sources and groups/ }).click();
  const metadataKeys = page.getByLabel('Declared metadata keys (session, sequence, subject)', { exact: true });
  const discardFields = page.getByRole('button', { name: 'Discard local changes and load current revision', exact: true });
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await open('new-project'); await sourcesStep();
    const originalKeys = await metadataKeys.inputValue();
    await metadataKeys.fill('session, subject');
    assert.equal(await last(), null, 'editing metadata keys must not dispatch or assign groups');
    assert.equal(await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Scope/ }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: 'Approve sources and groups and continue', exact: true }).isDisabled(), true);
    await discardFields.focus(); await page.keyboard.press('Enter');
    assert.equal(await metadataKeys.inputValue(), originalKeys);
    assert.equal(await last(), null, 'explicit discard must not trigger autosave');
    assert.equal(await page.getByRole('button', { name: 'Save changes', exact: true }).count(), 0, 'discard restores a clean form');
    const changedKeys = 'session, sequence';
    assert.notEqual(changedKeys, originalKeys, 'the save case must edit the recorded declaration');
    await metadataKeys.fill(changedKeys);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
    const violations = await page.evaluate(async () => (await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } })).violations.map((item) => item.id));
    assert.deepEqual(violations, [], `source-metadata-${width}: axe`);
    await page.screenshot({ path: `${evidence}/source-metadata-${width}.png`, fullPage: true });
    await page.getByRole('button', { name: 'Save changes', exact: true }).focus(); await page.keyboard.press('Enter');
    assert.deepEqual(await last(), { kind: 'save_setup', draft_id: 'draft-shelf', expected_revision: 7, step: 'Sources and groups', values: { metadata_keys: changedKeys } });
    await page.getByText('Your last change did not save.', { exact: false }).waitFor();
    assert.equal(await metadataKeys.inputValue(), changedKeys, 'failed save retains metadata keys');
    await discardFields.click();
    assert.equal(await metadataKeys.inputValue(), originalKeys);
    results.push({ key: `source-metadata-${width}`, pass: true, axe: 0, keyboard: 'save-and-discard-enter', realCommands: false });
  }
  await page.goto(`${entry}?screen=new-project&state=refresh-lifecycle`); await sourcesStep();
  const originalSourceFacts = await sourceFacts.innerText();
  await metadataKeys.fill('session, subject');
  await page.getByRole('button', { name: 'Refresh external records', exact: true }).evaluate((button) => button.click());
  await page.getByText('The draft changed while you were editing.', { exact: false }).waitFor();
  assert.equal(await metadataKeys.inputValue(), 'session, subject');
  assert.equal(await page.getByRole('button', { name: 'Save changes', exact: true }).isDisabled(), true);
  assert.equal(await last(), null, 'conflicting source metadata cannot overwrite the refreshed draft');
  await discardFields.click();
  await metadataKeys.fill('session, sequence');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  assert.equal((await last()).expected_revision, 8);
  assert.equal(await discardFields.isDisabled(), true, 'pending save cannot be discarded');
  assert.equal(await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Scope/ }).isDisabled(), true);
  await page.getByRole('button', { name: 'Finish pending save', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('input[name="metadata_keys"]')?.readOnly);
  assert.equal(await discardFields.count(), 0);
  assert.equal(await metadataKeys.inputValue(), 'session, sequence');
  assert.equal(await sourceFacts.innerText(), originalSourceFacts, 'metadata declaration cannot change source facts or split membership');
  const sourceCommands = await page.evaluate(() => JSON.parse(document.documentElement.dataset.commands || '[]'));
  assert.deepEqual(sourceCommands.map((command) => command.kind), ['save_setup'], 'saving does not approve setup or resolve pairs');
  await page.getByRole('button', { name: 'Compare source-0041 and source-0042', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: 'Same group', exact: true }).isDisabled(), true);
  for (const state of ['ready', 'dispatch-fixture', 'dispatch-stale', 'dispatch-published']) {
    await page.goto(`${entry}?screen=new-project&state=${state}`); await sourcesStep();
    assert.equal(await metadataKeys.getAttribute('readonly'), '', `${state}: metadata edits fail closed`);
  }
  await page.goto(`${entry}?screen=new-project&state=dispatch-ready&role=reviewer`); await sourcesStep();
  assert.equal(await metadataKeys.getAttribute('readonly'), '');
  assert.equal(await page.getByRole('button', { name: 'Save changes', exact: true }).count(), 0);
  results.push({ key: 'source-metadata-revision-role-and-authority-guards', pass: true, realCommands: false });
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await open('new-project');
    await page.getByRole('button', { name: 'Edit Almond milk 1 L', exact: true }).click();
    const editor = page.getByRole('form', { name: 'Edit class almond', exact: true });
    await editor.waitFor();
    assert.equal(await last(), null, 'opening the editor must not accept or mutate');
    assert.equal(await page.getByRole('button', { name: 'Accept Almond milk 1 L', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Scope/ }).isDisabled(), true);
    await editor.getByLabel('Class name', { exact: true }).fill('   ');
    assert.equal(await editor.getByRole('button', { name: 'Save class changes', exact: true }).isDisabled(), true);
    await editor.getByLabel('Class name', { exact: true }).fill('Almond drink 1 L');
    await editor.getByLabel('Aliases', { exact: true }).fill('almond beverage');
    await editor.getByLabel('Parent', { exact: true }).fill('Plant drink');
    await editor.getByLabel('Not this', { exact: true }).fill('Other sizes and desserts');
    await editor.getByLabel('Mirror-safe', { exact: true }).check();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
    const violations = await page.evaluate(async () => (await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } })).violations.map((item) => item.id));
    assert.deepEqual(violations, [], `class-editor-${width}: axe`);
    await page.screenshot({ path: `${evidence}/class-editor-${width}.png`, fullPage: true });
    await editor.getByRole('button', { name: 'Save class changes', exact: true }).focus();
    await page.keyboard.press('Enter');
    assert.deepEqual(await last(), { kind: 'edit_class', draft_id: 'draft-shelf', expected_revision: 7, id: 'almond', values: { name: 'Almond drink 1 L', aliases: 'almond beverage', parent: 'Plant drink', exclusions: 'Other sizes and desserts', mirror_safe: true } });
    await editor.getByText('Your class change did not save.', { exact: false }).waitFor();
    assert.equal(await editor.getByLabel('Class name', { exact: true }).inputValue(), 'Almond drink 1 L', 'failed save retains edits');
    await editor.getByRole('button', { name: 'Discard class edits', exact: true }).click();
    assert.equal(await editor.count(), 0);
    results.push({ key: `class-editor-${width}`, pass: true, axe: 0, keyboard: 'save-enter', realCommands: false });
  }
  await page.goto(`${entry}?screen=new-project&state=refresh-lifecycle`);
  await page.getByRole('button', { name: 'Edit Almond milk 1 L', exact: true }).click();
  const editor = page.getByRole('form', { name: 'Edit class almond', exact: true });
  await editor.getByLabel('Class name', { exact: true }).fill('Retained class edit');
  await page.getByRole('button', { name: 'Refresh external records', exact: true }).click();
  await editor.getByText('The draft changed while you were editing.', { exact: false }).waitFor();
  assert.equal(await editor.getByLabel('Class name', { exact: true }).inputValue(), 'Retained class edit');
  assert.equal(await editor.getByRole('button', { name: 'Save class changes', exact: true }).isDisabled(), true);
  assert.equal(await last(), null, 'conflicted class edit cannot overwrite a newer revision');
  await editor.getByRole('button', { name: 'Discard class edits', exact: true }).click();
  await page.getByRole('button', { name: 'Edit Almond milk 1 L', exact: true }).click();
  await editor.getByLabel('Class name', { exact: true }).fill('Renamed almond');
  await editor.getByRole('button', { name: 'Save class changes', exact: true }).click();
  assert.equal((await last()).expected_revision, 8);
  assert.equal(await page.getByRole('button', { name: 'Approve classes and continue', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: 'Finish pending save', exact: true }).click();
  await page.getByRole('button', { name: 'Accept Renamed almond', exact: true }).waitFor();
  assert.equal(await editor.count(), 0, 'successful save closes editor after refresh');
  const renamedRow = page.getByRole('row').filter({ has: page.getByRole('rowheader', { name: 'Renamed almond', exact: true }) });
  assert.match(await renamedRow.innerText(), /Proposed/);
  assert.match(await renamedRow.innerText(), /4 add more/);
  await page.getByRole('button', { name: 'Accept Renamed almond', exact: true }).click();
  await page.getByRole('status').filter({ hasText: 'Command completed' }).waitFor();
  assert.deepEqual(await last(), { kind: 'accept_class', draft_id: 'draft-shelf', expected_revision: 9, id: 'almond' });
  for (const state of ['ready', 'dispatch-stale', 'dispatch-published']) {
    await page.goto(`${entry}?screen=new-project&state=${state}`);
    assert.equal(await page.getByRole('button', { name: 'Edit Almond milk 1 L', exact: true }).isDisabled(), true, `${state}: class editing fails closed`);
  }
  await page.goto(`${entry}?screen=new-project&state=dispatch-ready&role=reviewer`);
  await page.getByRole('navigation', { name: 'Setup steps' }).waitFor();
  assert.equal(await page.getByRole('button', { name: /^Edit / }).count(), 0);
  results.push({ key: 'class-editor-revision-provenance-and-role-guards', pass: true, realCommands: false });
  const referenceValues = { class_id: 'almond', source: 'https://example.invalid/reference.png', permitted_use: 'Declared licence-clean fixture', view: 'Front', lighting: 'Store', scale: 'Native', confused_with: 'soy' };
  const evidenceStep = () => page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Evidence/ }).click();
  const referenceForm = page.getByRole('form', { name: 'Link reference evidence', exact: true });
  const saveReference = referenceForm.getByRole('button', { name: 'Save proposed reference', exact: true });
  const fillReference = async () => {
    await referenceForm.getByLabel('Reference class', { exact: true }).selectOption('almond');
    for (const [label, value] of [['Reference source URL', referenceValues.source], ['Reference permitted use', referenceValues.permitted_use], ['Reference view', referenceValues.view], ['Reference lighting', referenceValues.lighting], ['Reference scale', referenceValues.scale]]) await referenceForm.getByLabel(label, { exact: true }).fill(value);
    await referenceForm.getByLabel('Often confused with', { exact: true }).selectOption('soy');
  };
  let referenceRequests = 0;
  await page.route('https://example.invalid/**', (route) => { referenceRequests += 1; return route.abort(); });
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await open('new-project'); await evidenceStep();
    await page.getByRole('button', { name: 'Add linked reference', exact: true }).click();
    assert.equal(await last(), null, 'opening reference entry must not mutate');
    assert.equal(await saveReference.isDisabled(), true, 'missing class and metadata block saving');
    assert.equal(await page.getByRole('button', { name: 'Approve reference ref-1', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: 'Approve evidence and continue', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: /Classes/ }).isDisabled(), true);
    await fillReference();
    for (const source of ['javascript:alert(1)', 'data:image/png;base64,AA==', '/relative.png', 'https://user:example@example.invalid/reference.png']) {
      await referenceForm.getByLabel('Reference source URL', { exact: true }).fill(source);
      assert.equal(await saveReference.isDisabled(), true, `${source}: source URL rejected`);
    }
    await referenceForm.getByLabel('Reference source URL', { exact: true }).fill(referenceValues.source);
    await referenceForm.getByLabel('Often confused with', { exact: true }).selectOption('almond');
    assert.equal(await saveReference.isDisabled(), true, 'a class cannot be confused with itself');
    await referenceForm.getByLabel('Often confused with', { exact: true }).selectOption('soy');
    await referenceForm.getByLabel('Reference permitted use', { exact: true }).fill('   ');
    assert.equal(await saveReference.isDisabled(), true, 'permitted use is required');
    await referenceForm.getByLabel('Reference permitted use', { exact: true }).fill(` ${referenceValues.permitted_use} `);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
    const violations = await page.evaluate(async () => (await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } })).violations.map((item) => item.id));
    assert.deepEqual(violations, [], `reference-entry-${width}: axe`);
    await page.screenshot({ path: `${evidence}/reference-entry-${width}.png`, fullPage: true });
    await saveReference.focus(); await page.keyboard.press('Enter');
    assert.deepEqual(await last(), { kind: 'add_reference', draft_id: 'draft-shelf', expected_revision: 7, values: referenceValues });
    await referenceForm.getByText('Your reference did not save.', { exact: false }).waitFor();
    assert.equal(await referenceForm.getByLabel('Reference source URL', { exact: true }).inputValue(), referenceValues.source, 'failed save retains reference fields');
    await referenceForm.getByRole('button', { name: 'Discard reference entry', exact: true }).click();
    assert.equal(await referenceForm.count(), 0);
    results.push({ key: `reference-entry-${width}`, pass: true, axe: 0, keyboard: 'save-enter', realCommands: false });
  }
  await page.goto(`${entry}?screen=new-project&state=refresh-lifecycle`); await evidenceStep();
  await page.getByRole('button', { name: 'Add linked reference', exact: true }).click(); await fillReference();
  await page.getByRole('button', { name: 'Refresh external records', exact: true }).click();
  await referenceForm.getByText('The draft changed while you were linking evidence.', { exact: false }).waitFor();
  assert.equal(await saveReference.isDisabled(), true);
  assert.equal(await referenceForm.getByLabel('Reference source URL', { exact: true }).inputValue(), referenceValues.source);
  assert.equal(await last(), null, 'conflicted reference entry cannot retarget or overwrite');
  await referenceForm.getByRole('button', { name: 'Discard reference entry', exact: true }).click();
  await page.getByRole('button', { name: 'Add linked reference', exact: true }).click(); await fillReference(); await saveReference.click();
  assert.equal((await last()).expected_revision, 8);
  assert.equal(await page.getByRole('button', { name: 'Approve reference ref-1', exact: true }).isDisabled(), true, 'approval cannot race the deferred save');
  await page.getByRole('button', { name: 'Finish pending save', exact: true }).click();
  const approveLinked = page.getByRole('button', { name: 'Approve reference ref-linked', exact: true });
  await approveLinked.waitFor(); assert.equal(await referenceForm.count(), 0);
  assert.match(await page.getByRole('row').filter({ has: approveLinked }).innerText(), /Proposed/);
  await approveLinked.click();
  await page.getByRole('status').filter({ hasText: 'Command completed' }).waitFor();
  assert.deepEqual(await last(), { kind: 'accept_reference', draft_id: 'draft-shelf', expected_revision: 9, id: 'ref-linked' });
  const referenceCommands = await page.evaluate(() => JSON.parse(document.documentElement.dataset.commands || '[]'));
  assert.deepEqual(referenceCommands.map((command) => command.kind), ['add_reference', 'accept_reference'], 'saving a link never approves it');
  for (const state of ['ready', 'dispatch-stale', 'dispatch-published']) {
    await page.goto(`${entry}?screen=new-project&state=${state}`); await evidenceStep();
    assert.equal(await page.getByRole('button', { name: 'Add linked reference', exact: true }).isDisabled(), true, `${state}: linking fails closed`);
    assert.equal(await page.getByRole('button', { name: 'Approve reference ref-1', exact: true }).isDisabled(), true, `${state}: reference approval fails closed`);
  }
  await page.goto(`${entry}?screen=new-project&state=dispatch-ready&role=reviewer`); await evidenceStep();
  assert.equal(await page.getByRole('button', { name: 'Add linked reference', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: /^Approve reference/ }).count(), 0);
  await page.goto(`${entry}?screen=new-project&state=empty`); await evidenceStep();
  await page.getByText('No reference evidence returned.', { exact: false }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Add linked reference', exact: true }).isDisabled(), true, 'no class means no linked evidence');
  assert.equal(referenceRequests, 0, 'source URLs are passive metadata; never fetched by the form or table');
  results.push({ key: 'reference-revision-provenance-role-and-ingestion-guards', pass: true, realCommands: false });
  await page.close();
  const assets = await readdir('dist/management-fixture/assets');
  for (const asset of assets.filter((name) => /\.(js|css)$/.test(name))) {
    const text = await readFile(`dist/management-fixture/assets/${asset}`, 'utf8');
    assert.doesNotMatch(text, /PAPERCLIP_API_KEY|ANTHROPIC_AUTH_TOKEN|BEGIN PRIVATE KEY|ul_[A-Za-z0-9]{20,}/);
  }
} catch (error) {
  failures.push({ key: 'behavior-contracts', error: String(error) });
} finally {
  await writeFile(`${evidence}/results.json`, JSON.stringify({ head: process.env.HEAD_SHA ?? 'not-on-actions', fixtureOnly: true, results, failures }, null, 2));
  await browser.close();
  await new Promise((resolve) => server.httpServer.close(resolve));
}
console.log(JSON.stringify({ passed: results.length, failures }, null, 2));
assert.deepEqual(failures, [], 'Management browser evidence contains failures');
