// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/playwright.config.ts — internally versioned copy, see PROVENANCE.md
import { existsSync } from "node:fs";

import { defineConfig, devices } from "@playwright/test";

/**
 * E2E against a RUNNING MSO stack (no mocks). Start core + web yourself, then:
 *   MSO_E2E_BASE_URL=http://localhost:3000 npm run e2e
 * Default base URL: http://localhost:3000. Chromium comes from the preinstalled browsers in
 * /opt/pw-browsers when present (PLAYWRIGHT_BROWSERS_PATH), otherwise from Playwright's cache.
 */
const baseURL = process.env.MSO_E2E_BASE_URL ?? "http://localhost:3000";
if (!process.env.PLAYWRIGHT_BROWSERS_PATH && existsSync("/opt/pw-browsers")) process.env.PLAYWRIGHT_BROWSERS_PATH = "/opt/pw-browsers";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  reporter: [["list"]],
  outputDir: "./e2e/.results",
  use: { baseURL, trace: "retain-on-failure", locale: "nl-NL" },
  projects: [
    { name: "desktop-1440", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "mobile-390", use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
});
