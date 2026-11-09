import { expect, test, type Page } from "@playwright/test";

/**
 * Regression tests from the real-hardware report (2026-11-04), against a running stack prepared by `make e2e`
 * (tests/stack/run.sh with MSO_STACK_UP_ONLY=1 MSO_STACK_E2E_PREPARE=1: admin, connectors, one backup, wizard completed):
 *   MSO_E2E_BASE_URL=http://127.0.0.1:48081 MSO_E2E_USER=stackadmin MSO_E2E_PASSWORD=… npx playwright test e2e/dialogs.spec.ts
 *  - P5: typing in a DangerDialog's typed confirmation keeps focus in the field (no jump to "Annuleren")
 *  - K: a dialog opened from the topbar is fully visible, also on a low viewport
 *  - P1: the topbar mode chip opens Instellingen → Systeem → Bedrijfsmodus
 *  - QA Peil: without the exact typed name the (aria-disabled) confirm button restores nothing (click and Enter)
 * Nothing is confirmed: every dialog is cancelled.
 */
const USER = process.env.MSO_E2E_USER ?? "";
const PASSWORD = process.env.MSO_E2E_PASSWORD ?? "";

async function login(page: Page): Promise<void> {
  const r = await page.request.post("/api/v1/auth/login", { data: { username: USER, password: PASSWORD } });
  expect(r.status(), "login via API").toBe(200);
}

test("terugzetdialoog: typen houdt de focus in het bevestigingsveld", async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  await login(page);
  await page.goto("/systeem");
  const restore = page.getByRole("button", { name: "Terugzetten" }).first();
  // wait for the backup list to load (a check right after goto races the first fetch)
  const hasBackup = await restore.waitFor({ state: "visible", timeout: 15_000 }).then(() => true, () => false);
  test.skip(!hasBackup, "geen back-up om mee te testen");
  await restore.click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  const field = dialog.locator("#danger-confirm");
  await field.click();
  const word = "abc-def.dump";
  for (const ch of word) {
    await page.keyboard.type(ch);
    await page.waitForTimeout(150); // let polling/re-renders happen between keystrokes
    await expect(field).toBeFocused();
  }
  await expect(field).toHaveValue(word);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

// QA Peil-ronde: de bevestigknop werd van `disabled` naar `aria-disabled` (focusbaar, met reden) omgezet. Negatief pad:
// zolang de naam niet exact is getypt, zet een klik of Enter NIETS terug (geen enkele schrijfaanroep naar /backups).
test("terugzetdialoog: zonder de exacte bestandsnaam zet Terugzetten niets terug", async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  await login(page);
  const writes: string[] = [];
  page.on("request", (req) => {
    if (req.method() !== "GET" && /\/api\/v1\/backups/.test(req.url())) writes.push(`${req.method()} ${req.url()}`);
  });
  await page.goto("/systeem");
  const restore = page.getByRole("button", { name: "Terugzetten" }).first();
  const hasBackup = await restore.waitFor({ state: "visible", timeout: 15_000 }).then(() => true, () => false);
  test.skip(!hasBackup, "geen back-up om mee te testen");
  await restore.click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  const label = (await dialog.locator('label[for="danger-confirm"]').textContent()) ?? "";
  const word = /^Typ (.+) om te bevestigen/.exec(label.trim())?.[1] ?? "";
  expect(word, "bevestigwoord staat in het label").not.toBe("");
  const confirm = dialog.getByRole("button", { name: "Terugzetten" });
  const field = dialog.locator("#danger-confirm");
  const reason = dialog.locator("#danger-confirm-reason");
  for (const typed of ["", word.slice(0, -1), `${word}x`, word.toUpperCase()].filter((w) => w !== word)) {
    await field.fill(typed);
    await expect(confirm, `"${typed}" ⇒ knop onbruikbaar`).toHaveAttribute("aria-disabled", "true");
    await expect(reason).toBeVisible();
    await confirm.click({ force: true });
    await confirm.press("Enter");
  }
  await page.waitForTimeout(500);
  await expect(dialog, "dialoog blijft open").toBeVisible();
  expect(writes, "geen terugzet-aanroep").toEqual([]);
  // the exact name makes the button usable (not clicked: this test never restores anything)
  await field.fill(word);
  await expect(confirm).not.toHaveAttribute("aria-disabled", "true");
  await expect(reason).toHaveCount(0);
  await dialog.getByRole("button", { name: "Annuleren" }).click();
  await expect(dialog).toBeHidden();
  expect(writes).toEqual([]);
});

test("pauzeer-dialoog is volledig zichtbaar, ook bij een lage viewport", async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  await page.setViewportSize({ width: 1280, height: 420 });
  await login(page);
  await page.goto("/automatisering");
  const button = page.getByTestId("pause-button");
  // the button is disabled while the mode loads ("Modus wordt geladen"); wait for that before deciding
  const ready = await expect(button).toBeEnabled({ timeout: 15_000 }).then(() => true, () => false);
  test.skip(!ready, "pauzeknop niet beschikbaar voor deze gebruiker");
  await button.click();
  const dialog = page.getByRole("alertdialog"); // a confirmation: role alertdialog (not dialog)
  await expect(dialog).toBeVisible();
  const box = await dialog.boundingBox();
  expect(box, "dialog has a box").not.toBeNull();
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height).toBeLessThanOrEqual(420);
  await expect(dialog.getByRole("heading", { level: 2 })).toBeInViewport();
  await dialog.getByRole("button", { name: "Annuleren" }).click();
  await expect(dialog).toBeHidden();
});

test("moduschip opent de sectie Bedrijfsmodus", async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  await login(page);
  await page.goto("/");
  await page.getByTestId("mode-chip").click();
  await expect(page).toHaveURL(/\/instellingen\?tab=systeem#modus$/);
  await expect(page.getByRole("heading", { name: /Bedrijfsmodus/ })).toBeVisible();
  await expect(page.getByRole("group", { name: "Bedrijfsmodus kiezen" })).toBeVisible();
});
