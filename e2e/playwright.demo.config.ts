import { defineConfig, devices } from "@playwright/test";

// Not part of the test suite: regenerates the screenshots and recording in docs/demo
// from the fake-provider stack (`docker compose up`).   npm run demo
export default defineConfig({
  testDir: "./demo",
  workers: 1,
  timeout: 60_000,
  reporter: "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8000",
    ...devices["Desktop Chrome"],
    viewport: { width: 1100, height: 780 },
  },
});
