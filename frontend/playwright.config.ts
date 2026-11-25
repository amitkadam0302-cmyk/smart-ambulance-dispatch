import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 65000,
  expect: { timeout: 8000 },
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:3000',
    viewport: { width: 1460, height: 1000 },
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ?
      { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {},
    trace: 'retain-on-failure',
  },
})
