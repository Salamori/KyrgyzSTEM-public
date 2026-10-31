import { defineConfig, devices } from "@playwright/test";

// Expects the full stack running (docker compose up, or api + web dev servers).
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  use: { baseURL, trace: "on-first-retry" },
  projects: [
    { name: "mobile", use: { ...devices["Pixel 7"] } },
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
  ],
});
