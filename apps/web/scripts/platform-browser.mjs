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
try {
  for (const width of [1280, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 800 } });
    const mutations = [];
    page.on('request', (request) => {
      if (request.method() !== 'GET') mutations.push(request.url());
    });
    for (const screen of ['connections', 'runs']) {
      for (const state of ['', 'empty', 'paused', 'error', 'loading', 'check_failed', 'over_limit']) {
        await page.goto(`http://127.0.0.1:4174/platform-fixture.html?page=${screen}&state=${state}`);
        if (state === 'loading') await page.getByRole('status').filter({ hasText: 'Loading' }).waitFor();
        else if (state === 'error') await page.getByRole('status').filter({ hasText: 'Could not refresh' }).waitFor();
        else {
          await page.getByRole('heading', { level: 1 }).waitFor();
          if (screen === 'runs') {
            await page.getByRole('button', { name: 'Start a run', exact: true }).click();
            await page.getByRole('heading', { name: 'Start run on Ultralytics Platform' }).waitFor();
            assert.equal(await page.getByRole('button', { name: /^Start · estimate/ }).isEnabled().catch(() => false), false);
            if (state === 'over_limit') await page.getByText('Cannot start: Per-operation limit.').waitFor();
            if (state === 'paused') await page.getByText(/Paid steps paused · Out of credits/).waitFor();
            // Keyboard activation and focus are checked without a mouse.
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
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false, `${screen}/${state} overflow at ${width}`);
        await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
        const result = await page.evaluate(async () => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } }));
        assert.deepEqual(result.violations.map(({ id, nodes }) => ({ id, targets: nodes.map((node) => node.target) })), [], `${screen}/${state} axe at ${width}`);
        await page.screenshot({ path: `${output}/${screen}-${state || 'default'}-${width}.png`, fullPage: true });
      }
      await page.goto(`http://127.0.0.1:4174/platform-fixture.html?page=${screen}&role=reviewer`);
      if (screen === 'connections') {
        await page.getByText('Only an admin can manage connections.').waitFor();
        assert.equal(await page.getByText('workspace-fixture-01').count(), 0);
        assert.equal(await page.getByRole('button').count(), 0);
      } else {
        await page.getByRole('heading', { level: 1 }).waitFor();
        assert.equal(await page.getByRole('button', { name: 'Start a run' }).count(), 0);
        assert.equal(await page.getByRole('button', { name: 'Cancel run' }).count(), 0);
      }
    }
    assert.deepEqual(mutations, [], 'Fixture must not make mutation requests');
    await page.close();
  }
  // Synthetic sentinel values are deliberately not real keys. Scan every emitted
  // text asset and both entry bundles, not just the visible DOM.
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
  console.log('Platform fixtures: 1280/390, shared states, role gates, keyboard, axe, no mutations, bundle-secret scan passed.');
} finally {
  await browser.close();
  await new Promise((resolve, reject) => server.httpServer.close((error) => error ? reject(error) : resolve()));
}
