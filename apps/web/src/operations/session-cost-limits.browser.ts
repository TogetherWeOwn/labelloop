import { expect, test } from '@playwright/test';
import type { BudgetPage, BudgetSummary } from './cost-client.ts';

const endpoint = '/api/projects/costs-demo/costs';
const policy = { monthly_limit: 1_000_000, iteration_limit: 2_000_000, operation_limit: 0, autonomous_spend: false };
const total = { operation_count: 0, actual: 0, confirmed_estimate: 0, reserved: 0, unconfirmed: 0, exposure: 0, delta: 0 };
const summary: BudgetSummary = {
  project_id: 'costs-demo', currency: 'USD', unit: 'micro_usd', policy_revision: 1, policy, pause_reason: 'insufficient_credits',
  month: '2026-11', iteration_id: 'iteration-a', monthly: total, iteration: total,
};
const ledger: BudgetPage = { project_id: 'costs-demo', currency: 'USD', unit: 'micro_usd', policy_revision: 1, policy, pause_reason: 'insufficient_credits', operations: [], next_after: null };

for (const width of [1280, 390]) {
  test(`session limits at ${width}px submit exact CSRF-bound micro-USD and refresh without resumption`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    let revision = 1;
    let currentPolicy = policy;
    let writes = 0;
    let reads = 0;
    let release: (() => void) | undefined;
    const delayed = new Promise<void>((resolve) => { release = resolve; });
    await page.route('**/api/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (request.method() === 'POST') {
        writes += 1;
        expect(url.pathname).toBe(`${endpoint}/policy`);
        expect(await request.headerValue('x-csrf-token')).toBe('fixture-csrf');
        expect(request.postDataJSON()).toEqual({ expected_revision: 1, policy: { ...policy, monthly_limit: 1_234_567 } });
        await delayed;
        revision = 2; currentPolicy = { ...policy, monthly_limit: 1_234_567 };
        await route.fulfill({ json: { project_id: 'costs-demo', policy_revision: 2 } });
      } else {
        reads += 1;
        expect([endpoint, `${endpoint}/summary`]).toContain(url.pathname);
        await route.fulfill({ json: { ...(url.pathname.endsWith('/summary') ? summary : ledger), policy_revision: revision, policy: currentPolicy } });
      }
    });
    await page.goto('/operational-fixtures.html?view=costs&state=session-limits');
    const save = page.getByRole('button', { name: 'Save limits', exact: true });
    await expect(save).toBeEnabled();
    await page.getByLabel('Monthly limit (USD)').fill('1.234567');
    await save.click();
    await expect(save).toBeDisabled();
    await expect(page.getByLabel('Monthly limit (USD)')).toBeDisabled();
    await save.click({ force: true });
    expect(writes).toBe(1);
    release?.();
    await expect(page.getByLabel('Monthly limit (USD)')).toHaveValue('1.234567');
    await expect(save).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Resume paid steps' })).toBeDisabled();
    expect(reads).toBe(4);
    expect(writes).toBe(1);
  });

  test(`session limits at ${width}px require autonomous confirmation and retain it on parent renders`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    let writes = 0;
    await page.route('**/api/**', async (route) => {
      const request = route.request();
      if (request.method() === 'POST') {
        writes += 1;
        expect(request.postDataJSON()).toEqual({ expected_revision: 1, policy: { ...policy, autonomous_spend: true } });
        await route.fulfill({ json: { project_id: 'costs-demo', policy_revision: 2 } });
      } else await route.fulfill({ json: new URL(request.url()).pathname.endsWith('/summary') ? summary : ledger });
    });
    await page.goto('/operational-fixtures.html?view=costs&state=session-limits');
    await expect(page.getByRole('button', { name: 'Save limits' })).toBeEnabled();
    await page.getByRole('switch', { name: 'Spend without asking' }).click();
    await page.getByRole('button', { name: 'Save limits' }).click();
    const confirmation = page.getByRole('region', { name: 'Confirm autonomous spending' });
    await expect(confirmation).toBeVisible();
    expect(writes).toBe(0);
    await page.getByRole('button', { name: 'Re-render cost parent' }).click();
    await expect(confirmation).toBeVisible();
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    expect(writes).toBe(0);
    await page.getByRole('button', { name: 'Save limits' }).click();
    await page.getByRole('button', { name: 'Turn on', exact: true }).click();
    await expect(confirmation).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Resume paid steps' })).toBeDisabled();
    expect(writes).toBe(1);
  });
}

for (const failure of ['expired', 'stale', 'foreign-receipt', 'wrong-revision', 'malformed'] as const) {
  test(`session limits ${failure} require an explicit authoritative refresh before any retry`, async ({ page }) => {
    let writes = 0;
    let reads = 0;
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/api/**', async (route) => {
      const request = route.request();
      if (request.method() === 'POST') {
        writes += 1;
        expect(new URL(request.url()).pathname).toBe(`${endpoint}/policy`);
        if (failure === 'expired' || failure === 'stale') await route.fulfill({ status: failure === 'expired' ? 401 : 409, json: { detail: 'private command diagnostic' } });
        else await route.fulfill({ json: failure === 'malformed' ? { detail: 'private command diagnostic' } : { project_id: failure === 'foreign-receipt' ? 'foreign' : 'costs-demo', policy_revision: failure === 'wrong-revision' ? 1 : 2 } });
      } else {
        reads += 1;
        await route.fulfill({ json: new URL(request.url()).pathname.endsWith('/summary') ? summary : ledger });
      }
    });
    await page.goto('/operational-fixtures.html?view=costs&state=session-limits');
    const save = page.getByRole('button', { name: 'Save limits' });
    await expect(save).toBeEnabled();
    await page.getByLabel('Monthly limit (USD)').fill('4');
    await save.click();
    await expect(page.getByText('The change was not confirmed. Refresh records before trying again.', { exact: true })).toBeVisible();
    await expect(save).toBeDisabled();
    await expect(page.locator('body')).not.toContainText('private command diagnostic');
    await save.click({ force: true });
    expect(writes).toBe(1);
    expect(reads).toBe(2);
    await page.getByRole('button', { name: 'Refresh records', exact: true }).click();
    await expect(save).toBeEnabled();
    await expect(page.getByLabel('Monthly limit (USD)')).toHaveValue('1');
    expect(reads).toBe(4);
    expect(writes).toBe(1);
    expect(errors).toEqual([]);
  });
}

for (const replacement of ['scope', 'session']) {
  test(`replacing cost ${replacement} cancels the old limits request and ignores its late receipt`, async ({ page }) => {
    let release: (() => void) | undefined;
    const delayed = new Promise<void>((resolve) => { release = resolve; });
    let finish: (() => void) | undefined;
    const finished = new Promise<void>((resolve) => { finish = resolve; });
    let reads = 0;
    let writes = 0;
    await page.route('**/api/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (request.method() === 'POST') {
        writes += 1;
        await delayed;
        try { await route.fulfill({ json: { project_id: 'costs-demo', policy_revision: 2 } }); } finally { finish?.(); }
      } else {
        reads += 1;
        await route.fulfill({ json: { ...(url.pathname.endsWith('/summary') ? summary : ledger), project_id: url.pathname.includes('costs-replacement') ? 'costs-replacement' : 'costs-demo' } });
      }
    });
    await page.goto('/operational-fixtures.html?view=costs&state=session-limits');
    const save = page.getByRole('button', { name: 'Save limits' });
    await expect(save).toBeEnabled();
    const started = page.waitForRequest((request) => request.method() === 'POST');
    await save.click();
    await started;
    const cancelled = page.waitForEvent('requestfailed', (request) => request.method() === 'POST');
    await page.getByRole('button', { name: replacement === 'scope' ? 'Replace cost scope' : 'Replace cost session' }).click();
    await cancelled;
    await expect(save).toBeEnabled();
    release?.();
    await finished;
    await expect(page.getByText('The change was not confirmed. Refresh records before trying again.', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Resume paid steps' })).toBeDisabled();
    expect(writes).toBe(1);
    expect(reads).toBe(4);
  });
}

for (const scenario of ['session-limits-reviewer', 'session-limits-invalid']) {
  test(`${scenario} cannot connect a limits mutation`, async ({ page }) => {
    await page.route('**/api/**', async (route) => {
      expect(route.request().method()).toBe('GET');
      await route.fulfill({ json: new URL(route.request().url()).pathname.endsWith('/summary') ? summary : ledger });
    });
    await page.goto(`/operational-fixtures.html?view=costs&state=${scenario}`);
    await expect(page.getByRole('heading', { name: 'Spending policy' })).toBeVisible();
    if (scenario === 'session-limits-reviewer') await expect(page.getByRole('button', { name: 'Save limits' })).toHaveCount(0);
    else await expect(page.getByRole('button', { name: 'Save limits' })).toBeDisabled();
  });
}
