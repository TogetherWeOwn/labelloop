import { readFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import type { AuditMetadata } from './audit-client.ts';

const endpoint = '/api/projects/audit-demo/audit-log';
const id = '00000000-0000-0000-0000-000000000001';
const events: AuditMetadata[] = [
  { id, at: '2026-11-07T12:00:00Z', kind: 'Promotion', actor_id: 'legacy-actor', actor_kind: 'unknown', actor_name: 'Current name <not markup>', record_id: id, revision: 3, sentence: 'Recorded promotion decision.', refused: null },
  { id, at: '2026-11-07T11:00:00Z', kind: 'Budget', actor_id: 'budget-service', actor_kind: 'service', actor_name: null, record_id: id, revision: 2, sentence: 'Paid operation refused.', refused: true },
  { id, at: '2026-11-07T10:00:00Z', kind: 'Review', actor_id: 'reviewer', actor_kind: 'human', actor_name: null, record_id: id, revision: 1, sentence: 'Recorded review event not paged into the view.', refused: null },
];

for (const width of [1280, 390]) {
  test.describe(`${width}px session audit transport`, () => {
    test.use({ viewport: { width, height: 800 } });

    test('mounted view uses server pages and complete original-metadata export', async ({ page, context }) => {
      const requests: URL[] = [];
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      // An invented marker proves same-origin cookie transport, not authentication.
      await context.addCookies([{ name: 'fixture-session-marker', value: 'recorded-only', url: 'http://127.0.0.1:4173', httpOnly: true, sameSite: 'Strict' }]);
      await page.route('**/api/**', async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        requests.push(url);
        expect(request.method()).toBe('GET');
        expect(await request.headerValue('cookie')).toContain('fixture-session-marker=recorded-only');
        expect(url.origin).toBe('http://127.0.0.1:4173');
        expect([endpoint, `${endpoint}/export`]).toContain(url.pathname);
        if (url.pathname.endsWith('/export')) {
          await route.fulfill({ contentType: 'application/x-ndjson', body: events.map((event) => JSON.stringify(event)).join('\n') + '\n' });
        } else {
          const after = url.searchParams.get('after');
          expect(after === null || after === `Promotion/${id}`).toBe(true);
          await route.fulfill({ json: { project_id: 'audit-demo', events: [after === null ? events[0] : events[1]], total: 3, next_after: after === null ? `Promotion/${id}` : null } });
        }
      });
      await page.goto('/operational-fixtures.html?view=audit&state=session');
      await expect(page.locator('tbody tr')).toHaveCount(1);
      await expect(page.getByText('1 of 3 events', { exact: true })).toBeVisible();
      await expect(page.locator('tbody')).toContainText('Current name <not markup>');
      await expect(page.locator('tbody')).toContainText('Actor kind unavailable');
      await expect(page.locator('tbody a, tbody img')).toHaveCount(0);
      await expect(page.locator('tbody .danger-text')).toHaveCount(0);
      await page.getByRole('button', { name: 'Show 50 more' }).click();
      await expect(page.locator('tbody tr')).toHaveCount(2);
      await expect(page.getByText('2 of 3 events', { exact: true })).toBeVisible();
      await expect(page.locator('tbody .danger-text')).toHaveText('Paid operation refused.');
      await expect(page.getByRole('button', { name: 'Show 50 more' })).toHaveCount(0);
      await expect(page.locator('tbody')).not.toContainText(events[2].sentence);
      const downloaded = page.waitForEvent('download');
      await page.getByRole('button', { name: 'Export JSON lines' }).click();
      const file = await downloaded;
      expect(file.suggestedFilename()).toBe('audit.jsonl');
      const path = await file.path();
      expect(path).not.toBeNull();
      const exported = await readFile(path, 'utf8');
      expect(exported).toBe(events.map((event) => JSON.stringify(event)).join('\n') + '\n');
      await expect(page.getByRole('status')).toHaveText('Export downloaded.');
      expect(requests).toHaveLength(3);
      expect(requests[0].searchParams.getAll('kind')).toEqual(['Review', 'Promotion', 'Budget']);
      expect(requests[0].searchParams.has('actor_kind')).toBe(false);
      expect(requests[1].searchParams.get('after')).toBe(`Promotion/${id}`);
      expect(requests[2].searchParams.has('after')).toBe(false);
      expect(requests[2].searchParams.has('limit')).toBe(false);
      expect(errors).toEqual([]);
    });

    test('filters reach the server and export without client role or cursor grants', async ({ page }) => {
      const requests: URL[] = [];
      await page.route('**/api/**', async (route) => {
        const url = new URL(route.request().url());
        requests.push(url);
        await route.fulfill(url.pathname.endsWith('/export')
          ? { contentType: 'application/x-ndjson', body: '' }
          : { json: { project_id: 'audit-demo', events: [], total: 0, next_after: null } });
      });
      await page.goto('/operational-fixtures.html?view=audit&state=session');
      await expect(page.getByText('0 of 0 events', { exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Services', exact: true }).click();
      await expect(page.getByText('0 of 0 events', { exact: true })).toBeVisible();
      await page.getByLabel('Image, run, model or person').fill('actor_%');
      await page.getByLabel('From (UTC)').fill('2026-11-07');
      await page.getByLabel('Through (UTC)').fill('2026-11-08');
      await expect(page.getByText('0 of 0 events', { exact: true })).toBeVisible();
      const downloaded = page.waitForEvent('download');
      await page.getByRole('button', { name: 'Export JSON lines' }).click();
      await downloaded;
      const exported = requests.at(-1)!;
      expect(exported.pathname).toBe(`${endpoint}/export`);
      expect(exported.searchParams.getAll('actor_kind')).toEqual(['human']);
      expect(exported.searchParams.get('search')).toBe('actor_%');
      expect(exported.searchParams.get('since')).toBe('2026-11-07T00:00:00Z');
      expect(exported.searchParams.get('through')).toBe('2026-11-08T23:59:59.999999Z');
      const lastRead = requests.at(-2)!;
      const readFilters = new URLSearchParams(lastRead.searchParams);
      readFilters.delete('limit');
      expect(exported.searchParams.toString()).toBe(readFilters.toString());
      for (const request of requests) {
        expect([...request.searchParams.keys()].every((key) => ['kind', 'actor_kind', 'search', 'since', 'through', 'limit'].includes(key))).toBe(true);
      }
    });
  });
}

for (const failure of ['expired', 'foreign-scope', 'malformed-export'] as const) {
  test(`session audit ${failure} fails closed without retry or private error output`, async ({ page }) => {
    let requests = 0;
    const downloads: string[] = [];
    const errors: string[] = [];
    page.on('download', (item) => downloads.push(item.suggestedFilename()));
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/api/**', async (route) => {
      requests += 1;
      if (failure === 'expired') await route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"private fixture diagnostic"}' });
      else if (new URL(route.request().url()).pathname.endsWith('/export')) await route.fulfill({ contentType: 'application/x-ndjson', body: '{"detail":"private fixture diagnostic"}\n' });
      else await route.fulfill({ json: { project_id: failure === 'foreign-scope' ? 'another-project' : 'audit-demo', events: [events[0]], total: 1, next_after: null } });
    });
    await page.goto('/operational-fixtures.html?view=audit&state=session');
    if (failure === 'malformed-export') {
      await expect(page.locator('tbody tr')).toHaveCount(1);
      await page.getByRole('button', { name: 'Export JSON lines' }).click();
      await expect(page.getByRole('status')).toHaveText('Export could not be loaded. Try again.');
      await expect(page.getByRole('button', { name: 'Export JSON lines' })).toBeEnabled();
      expect(requests).toBe(2);
    } else {
      await expect(page.getByText('Records could not be loaded. Try again.', { exact: true })).toBeVisible();
      await expect(page.locator('tbody tr')).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Export JSON lines' })).toBeDisabled();
      expect(requests).toBe(1);
    }
    await expect(page.locator('body')).not.toContainText('private fixture diagnostic');
    expect(downloads).toEqual([]);
    expect(errors).toEqual([]);
  });
}
