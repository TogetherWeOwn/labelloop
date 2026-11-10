import { readFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import type { BudgetExport, BudgetPage, BudgetSummary, CostOperation } from './cost-client.ts';

const endpoint = '/api/projects/costs-demo/costs';
const policy = { monthly_limit: 1_000_000, iteration_limit: 2_000_000, operation_limit: 0, autonomous_spend: false };
const total = { operation_count: 8, actual: 300_000, confirmed_estimate: 350_000, reserved: 500_000, unconfirmed: 200_000, exposure: 1_000_000, delta: -50_000 };
const summary: BudgetSummary = {
  project_id: 'costs-demo', currency: 'USD', unit: 'micro_usd', policy_revision: 1, policy, pause_reason: 'insufficient_credits',
  month: '2026-11', iteration_id: 'iteration-a', monthly: total, iteration: { ...total, reserved: 600_000, exposure: 1_100_000 },
};
const operation: CostOperation = {
  operation_id: 'operation-a', iteration_id: 'iteration-a', month: '2026-11', price_version: 'private-price-version',
  estimate: 100_000, actual: null, exposure: 900_000, state: 'unconfirmed', approved_by: null,
  reserved_autonomously: true, reserved_at: null, kind: null, annotation_job_id: null, review_pause: null,
};
const ledger: BudgetPage = {
  project_id: 'costs-demo', currency: 'USD', unit: 'micro_usd', policy_revision: 1, policy, pause_reason: 'insufficient_credits',
  operations: [operation, { ...operation, operation_id: 'operation-b', kind: 'LLM review', annotation_job_id: 'Batch <not markup>', review_pause: 'approval_exhausted', state: 'settled', actual: 0, exposure: 0, reserved_at: '2026-11-08T12:00:00.123456Z' }], next_after: null,
};

for (const width of [1280, 390]) {
  test.describe(`${width}px session cost transport`, () => {
    test.use({ viewport: { width, height: 800 } });

    test('mounted supplied view preserves complete summaries, unknowns and read-only commands', async ({ page, context }) => {
      const requests: URL[] = [];
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      // Invented marker tests cookie transport only, never real authentication.
      await context.addCookies([{ name: 'fixture-session-marker', value: 'recorded-only', url: 'http://127.0.0.1:4173', httpOnly: true, sameSite: 'Strict' }]);
      await page.route('**/api/**', async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        requests.push(url);
        expect(request.method()).toBe('GET');
        expect(await request.headerValue('cookie')).toContain('fixture-session-marker=recorded-only');
        expect(url.origin).toBe('http://127.0.0.1:4173');
        expect([endpoint, `${endpoint}/summary`]).toContain(url.pathname);
        await route.fulfill({ json: url.pathname.endsWith('/summary') ? summary : ledger });
      });
      await page.goto('/operational-fixtures.html?view=costs&state=session');
      await expect(page.locator('tbody tr')).toHaveCount(2);
      await expect(page.getByRole('meter', { name: 'Spent and committed this month' })).toHaveAttribute('aria-valuetext', '100% of limit; $1.00 against $1.00');
      await expect(page.getByRole('meter', { name: 'This iteration · iteration-a' })).toHaveAttribute('aria-valuetext', '55% of limit; $1.10 against $2.00');
      await expect(page.locator('tbody')).toContainText('Reservation time unavailable');
      await expect(page.locator('tbody')).toContainText('Operation kind unavailable');
      await expect(page.locator('tbody')).toContainText('Object unavailable');
      await expect(page.locator('tbody')).toContainText('Batch <not markup>');
      await expect(page.locator('tbody')).toContainText('Billing confirmed · review paused: approval exhausted');
      await expect(page.locator('tbody tr').filter({ hasText: 'operation-a' })).toContainText('Unconfirmed');
      await expect(page.locator('tbody tr').filter({ hasText: 'operation-b' })).toContainText('$0.00');
      await expect(page.locator('tbody tr').first()).toContainText('operation-b');
      await expect(page.locator('tbody tr').last()).toContainText('Reservation time unavailable');
      await expect(page.locator('tbody img, tbody a')).toHaveCount(0);
      await expect(page.locator('tbody')).not.toContainText('Refused');
      await expect(page.getByRole('button', { name: 'Save limits' })).toBeDisabled();
      await expect(page.getByRole('button', { name: 'Resume paid steps' })).toBeDisabled();
      await expect(page.getByRole('switch', { name: 'Spend without asking' })).toBeDisabled();
      await expect(page.getByLabel('Monthly limit (USD)')).toBeDisabled();
      await expect(page.locator('body')).not.toContainText('private-price-version');
      const download = page.waitForEvent('download');
      await page.getByRole('button', { name: 'Download shown rows CSV' }).click();
      const file = await download;
      expect(file.suggestedFilename()).toBe('costs.csv');
      const path = await file.path();
      expect(path).not.toBeNull();
      const csv = await readFile(path, 'utf8');
      expect(csv.split('\r\n')).toHaveLength(3);
      expect(csv.split('\r\n').find((row) => row.includes('"operation-a"'))).toContain('"","operation-a","","",""');
      expect(csv).toContain('"Unconfirmed"');
      expect(csv).toContain('"$0.00"');
      expect(csv.split('\r\n')[0]).toContain('"Price version"');
      expect(csv.split('\r\n').slice(1).every((row) => row.endsWith(',"private-price-version"'))).toBe(true);
      expect(requests).toHaveLength(2);
      expect(requests[0].searchParams.toString()).toBe('month=2026-11&iteration_id=iteration-a');
      expect(requests[1].searchParams.toString()).toBe('limit=50');
      expect(errors).toEqual([]);
    });
  });
}

for (const failure of ['expired-summary', 'expired-page', 'foreign-summary', 'foreign-page', 'mixed-policy', 'malformed-page'] as const) {
  test(`session costs ${failure} hides partial success and retries only on request`, async ({ page }) => {
    const requests: URL[] = [];
    const errors: string[] = [];
    let recovered = false;
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/api/**', async (route) => {
      const url = new URL(route.request().url());
      requests.push(url);
      const isSummary = url.pathname.endsWith('/summary');
      if (!recovered && ((isSummary && failure === 'expired-summary') || (!isSummary && failure === 'expired-page'))) {
        await route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"private fixture diagnostic"}' });
      } else if (!recovered && !isSummary && failure === 'malformed-page') {
        await route.fulfill({ json: { detail: 'private fixture diagnostic' } });
      } else {
        const record = isSummary ? summary : ledger;
        await route.fulfill({ json: { ...record, ...(!recovered && failure === (isSummary ? 'foreign-summary' : 'foreign-page') ? { project_id: 'foreign' } : {}), ...(!recovered && !isSummary && failure === 'mixed-policy' ? { policy_revision: 2 } : {}) } });
      }
    });
    await page.goto('/operational-fixtures.html?view=costs&state=session');
    await expect(page.getByText('Records could not be loaded. Try again.', { exact: true })).toBeVisible();
    await expect(page.locator('tbody tr')).toHaveCount(0);
    await expect(page.getByRole('meter')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Download shown rows CSV' })).toHaveCount(0);
    await expect(page.locator('body')).not.toContainText('private fixture diagnostic');
    expect(requests).toHaveLength(failure.endsWith('summary') ? 1 : 2);
    const before = requests.length;
    recovered = true;
    await page.getByRole('button', { name: 'Try again', exact: true }).click();
    await expect(page.locator('tbody tr')).toHaveCount(2);
    expect(requests).toHaveLength(before + 2);
    expect(errors).toEqual([]);
  });
}

test('session costs empty page retains authoritative totals without asserting nothing paid ran', async ({ page }) => {
  await page.route('**/api/**', async (route) => {
    await route.fulfill({ json: new URL(route.request().url()).pathname.endsWith('/summary') ? summary : { ...ledger, operations: [] } });
  });
  await page.goto('/operational-fixtures.html?view=costs&state=session');
  await expect(page.getByText('No operations on this page match this scope and filter.', { exact: true })).toBeVisible();
  await expect(page.getByRole('meter', { name: 'Spent and committed this month' })).toHaveAttribute('aria-valuetext', '100% of limit; $1.00 against $1.00');
  await expect(page.locator('body')).not.toContainText('Nothing paid has run yet');
});

test('session costs first page does not replace complete summaries or imply a complete export', async ({ page }) => {
  const operations: CostOperation[] = Array.from({ length: 50 }, (_, index) => ({ ...operation, operation_id: `page-operation-${index}`, month: '2026-10', iteration_id: 'older-iteration', state: 'cancelled', exposure: 0 }));
  const requests: URL[] = [];
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    await route.fulfill({ json: url.pathname.endsWith('/summary') ? summary : { ...ledger, operations, next_after: operations[49].operation_id } });
  });
  await page.goto('/operational-fixtures.html?view=costs&state=session');
  await expect(page.locator('tbody tr')).toHaveCount(50);
  await expect(page.getByRole('meter', { name: 'Spent and committed this month' })).toHaveAttribute('aria-valuetext', '100% of limit; $1.00 against $1.00');
  await expect(page.getByText('Complete month and iteration summaries. Ledger rows are independently read pages, not a shared snapshot. Refused-attempt history is not included here.', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Download shown rows CSV' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Download CSV', exact: true })).toHaveCount(0);
  expect(requests).toHaveLength(2);
});

const firstPageOperations: CostOperation[] = Array.from({ length: 50 }, (_, index) => ({ ...operation, operation_id: `loaded-${index}` }));
const firstPage: BudgetPage = { ...ledger, operations: firstPageOperations, next_after: firstPageOperations[49].operation_id };

for (const width of [1280, 390]) {
  test(`session cost paging at ${width}px follows server cursor and exports only loaded rows`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    const requests: URL[] = [];
    let release: (() => void) | undefined;
    const delayed = new Promise<void>((resolve) => { release = resolve; });
    await page.route('**/api/**', async (route) => {
      const url = new URL(route.request().url());
      requests.push(url);
      if (url.pathname.endsWith('/summary')) await route.fulfill({ json: summary });
      else if (url.searchParams.has('after')) {
        expect(url.searchParams.get('after')).toBe(firstPage.next_after);
        await delayed;
        await route.fulfill({ json: { ...ledger, operations: [{ ...operation, operation_id: 'last-loaded' }] } });
      } else await route.fulfill({ json: firstPage });
    });
    await page.goto('/operational-fixtures.html?view=costs&state=session');
    await expect(page.locator('tbody tr')).toHaveCount(50);
    expect(requests).toHaveLength(2);
    await page.getByRole('button', { name: 'Training', exact: true }).click();
    await expect(page.locator('tbody tr')).toHaveCount(0);
    await page.getByRole('button', { name: 'Show 50 more operations', exact: true }).click();
    await expect(page.getByText('Loading more operations…', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Show 50 more operations', exact: true })).toHaveAttribute('aria-disabled', 'true');
    // Exercise the pending-click guard without waiting for the held response.
    await page.getByRole('button', { name: 'Show 50 more operations', exact: true }).click({ force: true });
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await expect(page.locator('tbody tr')).toHaveCount(50);
    release?.();
    await expect(page.locator('tbody tr')).toHaveCount(51);
    await expect(page.getByRole('button', { name: 'Show 50 more operations', exact: true })).toHaveCount(0);
    await expect(page.getByText('51 loaded operations · filters apply to loaded rows only', { exact: true })).toBeVisible();
    await expect(page.getByRole('meter', { name: 'Spent and committed this month' })).toHaveAttribute('aria-valuetext', '100% of limit; $1.00 against $1.00');
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download shown rows CSV' }).click();
    const file = await download;
    const path = await file.path();
    expect(path).not.toBeNull();
    const csv = await readFile(path, 'utf8');
    expect(csv.split('\r\n')).toHaveLength(52);
    expect(csv).toContain('last-loaded');
    expect(requests).toHaveLength(4);
  });
}

for (const failure of ['expired', 'duplicate', 'changed-policy', 'foreign'] as const) {
  test(`session cost paging ${failure} retains loaded rows and retries only explicitly`, async ({ page }) => {
    const requests: URL[] = [];
    let recovered = false;
    await page.route('**/api/**', async (route) => {
      const url = new URL(route.request().url());
      requests.push(url);
      if (url.pathname.endsWith('/summary')) await route.fulfill({ json: summary });
      else if (!url.searchParams.has('after')) await route.fulfill({ json: firstPage });
      else if (!recovered && failure === 'expired') await route.fulfill({ status: 401, json: { detail: 'private paging diagnostic' } });
      else await route.fulfill({ json: {
        ...ledger, operations: [{ ...operation, operation_id: !recovered && failure === 'duplicate' ? 'loaded-0' : 'last-loaded' }],
        ...(!recovered && failure === 'changed-policy' ? { policy_revision: 2 } : {}),
        ...(!recovered && failure === 'foreign' ? { project_id: 'foreign' } : {}),
      } });
    });
    await page.goto('/operational-fixtures.html?view=costs&state=session');
    await expect(page.locator('tbody tr')).toHaveCount(50);
    await page.getByRole('button', { name: 'Show 50 more operations', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('More operations could not be loaded');
    await expect(page.locator('tbody tr')).toHaveCount(50);
    await expect(page.locator('body')).not.toContainText('private paging diagnostic');
    expect(requests).toHaveLength(4);
    recovered = true;
    await page.getByRole('button', { name: 'Retry loading more operations', exact: true }).click();
    await expect(page.locator('tbody tr')).toHaveCount(51);
    await expect(page.getByRole('alert')).toHaveCount(0);
    expect(requests).toHaveLength(6);
    expect(requests[3].searchParams.get('after')).toBe(requests[5].searchParams.get('after'));
  });
}

for (const width of [1280, 390]) {
  test(`complete cost export at ${width}px ignores loaded rows and filters without replacing them`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    const requests: URL[] = [];
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    let release: (() => void) | undefined;
    const delayed = new Promise<void>((resolve) => { release = resolve; });
    const complete: BudgetExport = { ...ledger, policy_revision: 5, next_after: null, operations: [...ledger.operations, ...Array.from({ length: 58 }, (_, index) => ({ ...operation, operation_id: `unloaded-${index}`, month: '2026-10', iteration_id: 'old-iteration' }))] };
    await page.route('**/api/**', async (route) => {
      const url = new URL(route.request().url());
      requests.push(url);
      expect(route.request().method()).toBe('GET');
      expect(await route.request().headerValue('accept')).toBe('application/json');
      if (url.pathname.endsWith('/export')) {
        expect(url.search).toBe('');
        await delayed;
        await route.fulfill({ json: complete });
      } else await route.fulfill({ json: url.pathname.endsWith('/summary') ? summary : firstPage });
    });
    await page.goto('/operational-fixtures.html?view=costs&state=session');
    await expect(page.locator('tbody tr')).toHaveCount(50);
    expect(requests).toHaveLength(2);
    await page.getByRole('button', { name: 'Training', exact: true }).click();
    await expect(page.locator('tbody tr')).toHaveCount(0);
    await expect(page.getByText('Complete project JSON includes all iterations and operation kinds', { exact: false })).toBeVisible();
    const fileReady = page.waitForEvent('download');
    const button = page.getByRole('button', { name: 'Download complete project JSON', exact: true });
    await button.click();
    await expect(button).toBeDisabled();
    await expect(page.getByText('Preparing the authorized project export…', { exact: true })).toBeVisible();
    release?.();
    const file = await fileReady;
    expect(file.suggestedFilename()).toBe('costs.json');
    const path = await file.path();
    expect(path).not.toBeNull();
    expect(JSON.parse(await readFile(path, 'utf8'))).toEqual(complete);
    await expect(button).toBeEnabled();
    await expect(page.getByText('Project export downloaded.', { exact: true })).toBeVisible();
    await expect(page.locator('tbody tr')).toHaveCount(0);
    await page.getByRole('button', { name: 'All', exact: true }).click();
    await expect(page.locator('tbody tr')).toHaveCount(50);
    await expect(page.getByRole('meter', { name: 'Spent and committed this month' })).toHaveAttribute('aria-valuetext', '100% of limit; $1.00 against $1.00');
    expect(requests).toHaveLength(3);
    expect(errors).toEqual([]);
  });
}

for (const failure of ['expired', 'foreign', 'partial', 'invalid-later'] as const) {
  test(`complete cost export ${failure} downloads nothing, retains rows and retries explicitly`, async ({ page }) => {
    let exports = 0;
    let downloads = 0;
    let recovered = false;
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('download', () => { downloads += 1; });
    await page.route('**/api/**', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('/export')) {
        exports += 1;
        if (!recovered && failure === 'expired') await route.fulfill({ status: 401, json: { detail: 'private export diagnostic' } });
        else await route.fulfill({ json: {
          ...ledger,
          ...(!recovered && failure === 'foreign' ? { project_id: 'foreign' } : {}),
          ...(!recovered && failure === 'partial' ? { next_after: operation.operation_id } : {}),
          ...(!recovered && failure === 'invalid-later' ? { operations: [...ledger.operations, { ...operation, operation_id: 'bad-later', exposure: -1 }] } : {}),
        } });
      } else await route.fulfill({ json: url.pathname.endsWith('/summary') ? summary : ledger });
    });
    await page.goto('/operational-fixtures.html?view=costs&state=session');
    await expect(page.locator('tbody tr')).toHaveCount(2);
    const button = page.getByRole('button', { name: 'Download complete project JSON', exact: true });
    await button.click();
    await expect(page.getByText('Project export could not be loaded. Try again.', { exact: true })).toBeVisible();
    await expect(button).toBeEnabled();
    await expect(page.locator('tbody tr')).toHaveCount(2);
    await expect(page.locator('body')).not.toContainText('private export diagnostic');
    expect(exports).toBe(1);
    expect(downloads).toBe(0);
    recovered = true;
    const fileReady = page.waitForEvent('download');
    await button.click();
    await fileReady;
    await expect(page.getByText('Project export downloaded.', { exact: true })).toBeVisible();
    expect(exports).toBe(2);
    expect(downloads).toBe(1);
    expect(errors).toEqual([]);
  });
}

test('scope replacement cancels an old complete export without downloading its late response', async ({ page }) => {
  let release: (() => void) | undefined;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  let finish: (() => void) | undefined;
  const finished = new Promise<void>((resolve) => { finish = resolve; });
  let downloads = 0;
  page.on('download', () => { downloads += 1; });
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const replacement = url.pathname.includes('costs-replacement');
    if (url.pathname === `${endpoint}/export`) {
      await delayed;
      try { await route.fulfill({ json: ledger }); } finally { finish?.(); }
    } else {
      const value = url.pathname.endsWith('/summary') ? summary : ledger;
      await route.fulfill({ json: { ...value, project_id: replacement ? 'costs-replacement' : 'costs-demo' } });
    }
  });
  await page.goto('/operational-fixtures.html?view=costs&state=session-scope-swap');
  await expect(page.locator('tbody tr')).toHaveCount(2);
  const oldExport = page.waitForRequest((request) => new URL(request.url()).pathname === `${endpoint}/export`);
  await page.getByRole('button', { name: 'Download complete project JSON', exact: true }).click();
  await oldExport;
  const cancelled = page.waitForEvent('requestfailed', (request) => new URL(request.url()).pathname === `${endpoint}/export`);
  await page.getByRole('button', { name: 'Replace cost scope' }).click();
  await cancelled;
  await expect(page.locator('tbody tr')).toHaveCount(2);
  release?.();
  await finished;
  await expect(page.getByRole('button', { name: 'Download complete project JSON', exact: true })).toBeEnabled();
  await expect(page.getByText('Project export downloaded.', { exact: true })).toHaveCount(0);
  expect(downloads).toBe(0);
});

test('scope replacement cancels the old read and cannot show its late result', async ({ page }) => {
  let release: (() => void) | undefined;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  let finish: (() => void) | undefined;
  const finished = new Promise<void>((resolve) => { finish = resolve; });
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const replacement = url.pathname.includes('costs-replacement');
    if (!replacement && url.pathname.endsWith('/summary')) {
      await delayed;
      try { await route.fulfill({ json: summary }); } finally { finish?.(); }
    } else {
      const record = url.pathname.endsWith('/summary') ? summary : { ...ledger, operations: [{ ...operation, operation_id: 'replacement-only' }] };
      await route.fulfill({ json: { ...record, project_id: replacement ? 'costs-replacement' : 'costs-demo' } });
    }
  });
  const oldRequest = page.waitForRequest((request) => new URL(request.url()).pathname === `${endpoint}/summary`);
  await page.goto('/operational-fixtures.html?view=costs&state=session-scope-swap');
  await oldRequest;
  await expect(page.getByRole('heading', { name: 'Loading records' })).toBeVisible();
  await page.getByRole('button', { name: 'Replace cost scope' }).click();
  await expect(page.locator('tbody tr')).toHaveCount(1);
  await expect(page.locator('tbody')).toContainText('replacement-only');
  release?.();
  await finished;
  await expect(page.locator('tbody')).toContainText('replacement-only');
  await expect(page.locator('tbody')).not.toContainText('operation-a');
});
