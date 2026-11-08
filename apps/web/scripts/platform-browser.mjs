import assert from 'node:assert/strict';
import { mkdir, readFile, readdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { chromium } from '@playwright/test';
import { preview } from 'vite';

const require = createRequire(import.meta.url);
const output = 'platform-evidence';
await mkdir(output, { recursive: true });
const server = await preview({ configFile: 'vite.platform.config.ts', preview: { host: '127.0.0.1', port: 4174, strictPort: true } });
const browser = await chromium.launch();

async function evidence(page, name) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false, `${name} overflow`);
  await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const result = await page.evaluate(async () => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } }));
  await page.screenshot({ path: `${output}/${name}.png`, fullPage: true });
  assert.deepEqual(result.violations.map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) })), [], `${name} axe`);
}

async function loaded(page, screen, state) {
  // H1 exists during loading. Require the settled record and its state-specific body.
  await page.getByRole('status').filter({ hasText: 'Fixture record updated' }).waitFor();
  if (screen === 'connections') {
    await page.getByRole('heading', { name: 'Ultralytics Platform', exact: true }).waitFor();
    if (state === 'empty') await page.getByLabel('Paste a Platform API key', { exact: true }).waitFor();
    else await page.getByText('workspace-fixture-01', { exact: true }).waitFor();
    if (state === 'check_failed') await page.getByText('Check failed', { exact: true }).waitFor();
  } else if (state === 'empty') await page.getByText(/No Platform runs yet/).waitFor();
  else await page.getByRole('region', { name: 'Training runs, scrolls sideways' }).getByRole('button', { name: 'run-31', exact: true }).waitFor();
}

try {
  for (const width of [1280, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 800 } });
    const mutations = [];
    page.on('request', (request) => {
      if (request.method() !== 'GET') mutations.push(request.url());
    });
    for (const screen of ['connections', 'runs']) {
      for (const state of ['', 'empty', 'paused', 'error', 'loading', 'check_failed', 'over_limit', 'delayed']) {
        await page.goto(`http://127.0.0.1:4174/platform-fixture.html?page=${screen}&state=${state}`);
        if (state === 'loading') await page.getByRole('status').filter({ hasText: 'Loading' }).waitFor();
        else if (state === 'error') await page.getByRole('status').filter({ hasText: 'Could not refresh' }).waitFor();
        else {
          await loaded(page, screen, state);
          if (screen === 'runs') {
            await page.getByRole('button', { name: 'Start a run', exact: true }).click();
            await page.getByRole('heading', { name: 'Start run on Ultralytics Platform' }).waitFor();
            if (state === 'over_limit' || state === 'paused') {
              assert.equal(await page.getByRole('button', { name: /^Start · estimate/ }).count(), 0);
            } else {
              const start = page.getByRole('button', { name: /^Start · estimate/ });
              assert.equal(await start.count(), 1);
              assert.equal(await start.isEnabled(), false);
            }
            if (state === 'over_limit') await page.getByText('Cannot start: Per-operation limit.').waitFor();
            if (state === 'paused') await page.getByText(/Paid steps paused · Out of credits/).waitFor();
            // Verify and capture the open paid-summary panel before keyboard closing it.
            await evidence(page, `runs-${state || 'default'}-summary-${width}`);
            await page.getByRole('button', { name: 'Cancel', exact: true }).focus();
            await page.keyboard.press('Enter');
            assert.equal(await page.getByRole('heading', { name: 'Start run on Ultralytics Platform' }).count(), 0);
            if (!state) {
              await page.getByRole('button', { name: 'run-28', exact: true }).focus();
              await page.keyboard.press('Enter');
              await page.getByRole('region', { name: 'Settings mismatch, scrolls sideways' }).waitFor();
            }
          }
        }
        await evidence(page, `${screen}-${state || 'default'}-${width}`);
      }
      await page.goto(`http://127.0.0.1:4174/platform-fixture.html?page=${screen}&role=reviewer&state=delayed`);
      if (screen === 'connections') {
        await page.getByText('Only an admin can manage connections.').waitFor();
        assert.equal(await page.getByText('workspace-fixture-01').count(), 0);
        assert.equal(await page.getByRole('button').count(), 0);
      } else {
        await loaded(page, screen, 'delayed');
        await page.getByText('Read only. Only an admin can start or cancel runs.').waitFor();
        assert.equal(await page.getByRole('button', { name: 'Start a run' }).count(), 0);
        assert.equal(await page.getByRole('button', { name: 'Cancel run' }).count(), 0);
      }
      await evidence(page, `${screen}-reviewer-${width}`);
    }
    // A hanging second read must not leave the first response permanently fresh.
    await page.clock.install({ time: new Date('2026-11-07T01:52:00Z') });
    await page.goto('http://127.0.0.1:4174/platform-fixture.html?page=runs&state=pending_refresh');
    await loaded(page, 'runs', 'pending_refresh');
    assert.equal(await page.locator('.freshness.warn').count(), 0);
    await page.clock.fastForward(90_000);
    await page.locator('.freshness.warn').filter({ hasText: 'Stale data' }).waitFor();
    assert.deepEqual(mutations, [], 'Fixture must not make mutation requests');
    await page.close();
  }
  // Synthetic sentinel values are deliberately not real keys. Both builds receive
  // them in Actions; scan all emitted assets, including the normal entry bundle.
  const forbidden = ['ul_' + 'secretSentinelNeverBundle123456', 'Bearer ' + 'bundleSentinelCredential123456'];
  async function scan(dir) {
    for (const item of await readdir(dir, { withFileTypes: true })) {
      const path = `${dir}/${item.name}`;
      if (item.isDirectory()) await scan(path);
      else if (/\.(js|css|html|map|json)$/.test(path)) {
        const text = await readFile(path, 'utf8');
        for (const value of forbidden) assert.equal(text.includes(value), false, `Secret sentinel in ${path}`);
        assert.equal(/ul_[a-zA-Z0-9]{20,}/.test(text), false, `Credential-shaped value in ${path}`);
      }
    }
  }
  await scan('dist');
  console.log('Platform fixtures: 1280/390, shared states, delayed role gates, open dry-run keyboard/axe/screenshots, pending-read freshness, no mutations, bundle-secret scan passed.');
} finally {
  await browser.close();
  await new Promise((resolve, reject) => server.httpServer.close((error) => error ? reject(error) : resolve()));
}
