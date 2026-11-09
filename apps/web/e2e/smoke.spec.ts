import { expect, test } from "@playwright/test";

/**
 * Smoke test: the login page renders (works with or without a reachable core — the form is static,
 * the setup-status probe only redirects when the core says setup is required).
 * Run against a running web server: MSO_E2E_BASE_URL=http://localhost:3000 npm run e2e
 *
 * The redirect to /setup happens client-side, after the asynchronous GET /api/v1/setup/status of the login page.
 * Checking the URL right after goto() races that redirect, so the test first waits for the probe and decides on
 * its answer which page it must see.
 */
test("inlogpagina wordt getoond", async ({ page }) => {
  const probe = page
    .waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/setup/status", { timeout: 15_000 })
    .catch(() => null); // no core reachable → no answer → the static login form stays
  await page.goto("/login");
  await expect(page).toHaveTitle(/MSO|Media Source Optimizer/);
  const res = await probe;
  const status = res?.ok() ? ((await res.json()) as { setup_required?: boolean; admin_exists?: boolean }) : null;
  if (status?.setup_required && !status.admin_exists) {
    await expect(page).toHaveURL(/\/setup$/);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    return;
  }
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Inloggen", level: 1 })).toBeVisible();
  await expect(page.getByLabel("Gebruikersnaam")).toBeVisible();
  await expect(page.getByLabel("Wachtwoord", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Inloggen" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "page must not scroll horizontally").toBeLessThanOrEqual(1);
});
