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
  await page.getByRole('button', { name: 'Accept Almond milk 1 L', exact: true }).click();
  assert.deepEqual(await last(), { kind: 'accept_class', draft_id: 'draft-shelf', expected_revision: 7, id: 'almond' });
  for (const step of ['Scope', 'Classes', 'Evidence', 'Labelling guide', 'Sources and groups', 'Check and publish']) {
    await page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: new RegExp(step) }).click();
    await page.getByRole('heading', { level: 2, name: new RegExp(step) }).waitFor();
  }
  await page.close();
  results.push({ key: 'command-contracts', pass: true, commands: ['compile', 'start-local', 'cancel-local', 'locked-test', 'promote', 'rollback', 'accept-class'], realCommands: false });
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
