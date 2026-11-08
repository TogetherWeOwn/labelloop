import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './src/operations',
  testMatch: '**/*.browser.ts',
  workers: 2,
  forbidOnly: true,
  retries: 0,
  reporter: [['list'], ['html', { outputFolder: 'dist/operations-report', open: 'never' }]],
  outputDir: 'dist/operations-evidence',
  use: { baseURL: 'http://127.0.0.1:4173', trace: 'retain-on-failure' },
  webServer: {
    command: 'pnpm exec vite preview --config vite.operations.config.ts --host 127.0.0.1 --port 4173 --strictPort',
    url: 'http://127.0.0.1:4173/operational-fixtures.html',
    reuseExistingServer: false,
  },
});
