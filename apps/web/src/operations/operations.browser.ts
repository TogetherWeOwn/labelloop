import { expect, test } from '@playwright/test';
import axe from 'axe-core';

declare global { interface Window { axe: typeof axe } }

for (const width of [1280, 390]) {
  test.describe(`${width}px operational views`, () => {
    test.use({ viewport: { width, height: 800 } });
    for (const view of ['costs', 'audit', 'models']) for (const state of ['ready', 'loading', 'empty', 'error', 'stale', 'readonly']) {
      test(`${view} ${state}: axe, keyboard and screenshot`, async ({ page }, testInfo) => {
        const errors: string[] = [];
        page.on('pageerror', (error) => errors.push(error.message));
        await page.goto(`/operational-fixtures.html?view=${view}&state=${state}`);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
        if (view === 'audit' && ['ready', 'stale', 'readonly'].includes(state)) await expect(page.getByRole('table')).toBeVisible();
        await page.keyboard.press('Tab');
        await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
        await page.keyboard.press('Enter');
        await expect(page.locator('main')).toBeFocused();
        await page.keyboard.press('Tab');
        await expect(page.locator(':focus')).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        if (width === 390 && view === 'costs' && ['ready', 'stale', 'readonly', 'empty'].includes(state)) {
          const policy = await page.getByRole('complementary', { name: 'Budget and policy' }).boundingBox();
          const ledger = await page.locator('.ledger').boundingBox();
          expect(policy?.y).toBeLessThan(ledger?.y ?? 0);
        }
        await page.addScriptTag({ content: axe.source });
        const result = await page.evaluate(async () => window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } }));
        await testInfo.attach('axe.json', { body: JSON.stringify({ violations: result.violations, incomplete: result.incomplete }), contentType: 'application/json' });
        expect(result.violations).toEqual([]);
        expect(errors).toEqual([]);
        await page.screenshot({ path: testInfo.outputPath(`${view}-${state}-${width}.png`), fullPage: true });
      });
    }
    test('filters ledger and keeps run scope; readonly policy has no controls', async ({ page }) => {
      await page.goto('/operational-fixtures.html?view=costs');
      await page.getByRole('button', { name: 'LLM review', exact: true }).focus();
      await page.keyboard.press('Enter');
      await expect(page.getByRole('rowheader')).toHaveCount(1);
      await expect(page.getByRole('rowheader')).toContainText('batch-49');
      await page.goto('/operational-fixtures.html?view=costs&scope=run');
      await expect(page.getByRole('rowheader')).toHaveCount(1);
      await expect(page.getByRole('rowheader')).toContainText('run-0030');
      await page.goto('/operational-fixtures.html?view=costs&state=readonly');
      await expect(page.getByRole('switch')).toHaveCount(0);
      await expect(page.locator('input')).toHaveCount(0);
    });
    test('paginated audit, search, service filter and reviewer export source', async ({ page }) => {
      await page.goto('/operational-fixtures.html?view=audit');
      await expect(page.locator('tbody tr')).toHaveCount(3);
      await page.getByRole('button', { name: 'Show 50 more' }).focus();
      await page.keyboard.press('Enter');
      await expect(page.locator('tbody tr')).toHaveCount(6);
      await page.getByLabel('Image, run, model or person').fill('refused');
      await expect(page.locator('tbody tr')).toHaveCount(1);
      await expect(page.locator('tbody')).toContainText('worker-llm');
      await page.getByLabel('Image, run, model or person').fill('');
      await page.getByRole('button', { name: 'Services', exact: true }).click();
      await expect(page.locator('tbody tr')).toHaveCount(3);
      await expect(page.locator('tbody')).not.toContainText('worker-llm');
      await page.goto('/operational-fixtures.html?view=audit&state=readonly');
      await expect(page.locator('tbody tr')).toHaveCount(3);
      await expect(page.locator('tbody')).not.toContainText('Promoted');
      await expect(page.getByRole('button', { name: 'Connection', exact: true })).toHaveCount(0);
      const download = page.waitForEvent('download');
      await page.getByRole('button', { name: 'Export JSON lines' }).click();
      expect((await download).suggestedFilename()).toBe('audit.jsonl');
    });
    test('autonomous spending requires exact draft confirmation; stale and failure block commands', async ({ page }) => {
      await page.goto('/operational-fixtures.html?view=costs&state=editable');
      await page.getByRole('switch').focus();
      await page.keyboard.press('Space');
      await page.getByLabel('Monthly limit (USD)').fill('42');
      await page.getByRole('button', { name: 'Save limits' }).click();
      await expect(page.getByRole('region', { name: 'Confirm autonomous spending' })).toContainText('$42.00');
      await page.getByRole('button', { name: 'Cancel', exact: true }).click();
      await expect(page.getByRole('region', { name: 'Confirm autonomous spending' })).toHaveCount(0);
      await page.getByRole('button', { name: 'Save limits' }).click();
      await page.getByRole('button', { name: 'Turn on', exact: true }).click();
      await expect(page.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
      await page.goto('/operational-fixtures.html?view=costs&state=command-error');
      await page.getByLabel('Monthly limit (USD)').fill('99');
      await page.getByRole('button', { name: 'Save limits' }).click();
      await expect(page.getByRole('status')).toContainText('not confirmed');
      await expect(page.getByRole('button', { name: 'Save limits' })).toBeDisabled();
      await page.getByRole('button', { name: 'Refresh records' }).click();
      await expect(page.getByRole('button', { name: 'Save limits' })).toBeEnabled();
      await expect(page.getByLabel('Monthly limit (USD)')).toHaveValue('40');
      await page.getByRole('button', { name: 'Save limits' }).click();
      await expect(page.getByRole('status')).toContainText('not confirmed');
      await page.goto('/operational-fixtures.html?view=costs&state=stale');
      await expect(page.getByRole('button', { name: 'Resume paid steps' })).toBeDisabled();
    });
  });
}

test('audit port replacement clears paginated rows and aborts the old export', async ({ page }) => {
  const downloads: string[] = [];
  page.on('download', (item) => downloads.push(item.suggestedFilename()));
  await page.goto('/operational-fixtures.html?view=audit&state=scope-swap');
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await page.getByRole('button', { name: 'Show 50 more' }).click();
  await expect(page.locator('tbody tr')).toHaveCount(6);
  await page.getByRole('button', { name: 'Export JSON lines' }).click();
  await expect(page.getByRole('status')).toContainText('Preparing');
  await page.getByRole('button', { name: 'Replace authorized port' }).click();
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await expect(page.locator('tbody')).toContainText('Replacement scope');
  await expect(page.locator('tbody')).not.toContainText('Promoted');
  await expect(page.getByRole('button', { name: 'Show 50 more' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Release previous export' }).click();
  await expect(page.getByRole('button', { name: 'Release previous export' })).toBeDisabled();
  expect(downloads).toEqual([]);
  await expect(page.getByRole('button', { name: 'Export JSON lines' })).toBeEnabled();
});

test('stable audit port refresh restarts pagination before applying new freshness', async ({ page }) => {
  await page.goto('/operational-fixtures.html?view=audit&state=freshness-refresh');
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await page.getByRole('button', { name: 'Show 50 more' }).click();
  await expect(page.locator('tbody tr')).toHaveCount(6);
  await page.getByRole('button', { name: 'Refresh audit evidence' }).click();
  await expect(page.getByText('Loading the latest records…')).toBeVisible();
  await expect(page.locator('tbody tr')).toHaveCount(0);
  await page.getByRole('button', { name: 'Complete refreshed query' }).click();
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await expect(page.locator('tbody')).toContainText('Refreshed evidence');
  await expect(page.getByRole('button', { name: 'Show 50 more' })).toBeVisible();
  await page.getByRole('button', { name: 'Show 50 more' }).click();
  await expect(page.locator('tbody tr')).toHaveCount(6);
  await page.getByRole('button', { name: 'Mark audit stale' }).click();
  await expect(page.getByRole('button', { name: 'Show 50 more' })).toBeDisabled();
  await page.getByRole('button', { name: 'Refresh audit evidence' }).click();
  await expect(page.locator('tbody tr')).toHaveCount(0);
  await page.getByRole('button', { name: 'Complete refreshed query' }).click();
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await expect(page.getByRole('button', { name: 'Show 50 more' })).toBeEnabled();
});

test('fixture views never contact providers or API endpoints', async ({ page }) => {
  const unexpected: string[] = [];
  await page.route('**/*', (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:4173' || url.pathname.startsWith('/api/')) { unexpected.push(url.pathname); return route.abort(); }
    return route.continue();
  });
  for (const view of ['costs', 'audit', 'models']) {
    await page.goto(`/operational-fixtures.html?view=${view}`);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  }
  expect(unexpected).toEqual([]);
});
