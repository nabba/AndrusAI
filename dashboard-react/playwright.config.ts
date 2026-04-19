import { defineConfig, devices } from '@playwright/test';

// Smoke tests run against the Vite dev server on port 5173 with base `/cp/`.
// They do not require the FastAPI gateway to be running — API calls will 502
// via the dev proxy; the tests assert that the UI shell still renders.
export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:5173/cp/',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173/cp/',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
});
