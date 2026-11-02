import { expect, test } from "@playwright/test";

/**
 * Smoke test: the login page renders (works with or without a reachable core — the form is static,
 * the setup-status probe only redirects when the core says setup is required).
 * Run against a running web server: MSO_E2E_BASE_URL=http://localhost:3000 npm run e2e
 */
test("inlogpagina wordt getoond", async ({ page }) => {
  await page.goto("/login");
  await expect(page).toHaveTitle(/MSO|Media Source Optimizer/);
  if (page.url().endsWith("/setup")) {
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    return;
  }
  await expect(page.getByRole("heading", { name: "Inloggen", level: 1 })).toBeVisible();
  await expect(page.getByLabel("Gebruikersnaam")).toBeVisible();
  await expect(page.getByLabel("Wachtwoord")).toBeVisible();
  await expect(page.getByRole("button", { name: "Inloggen" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "page must not scroll horizontally").toBeLessThanOrEqual(1);
});
