import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// Alleen deze browserpagina onderschept de nieuwe beheer-API. De gedeelde
// stack houdt exact dezelfde sleutels, gebruikers en auditregels na deze test.
test("machine-sleutels: scopes, eenmalige weergave, intrekken en toegankelijkheid", async ({ page }, testInfo) => {
  test.setTimeout(150_000);
  const user = process.env.MSO_E2E_USER;
  const password = process.env.MSO_E2E_PASSWORD;
  expect(user, "De volledige suite levert testinloggegevens").toBeTruthy();
  const login = await page.request.post("/api/v1/auth/login", { data: { username: user, password } });
  expect(login.status()).toBe(200);
  type Key = { id: string; name: string; scopes: string[]; last_four: string; created_at: string; last_used_at: null; revoked_at: string | null };
  let items: Key[] = [];
  let listMode: "available" | "offline" | "restore" = "available";
  let listRequests = 0;
  const writes: unknown[] = [];
  const fixtureName = "Browserfixture".padEnd(80, "x");
  const fakeValue = "DIT-IS-GEEN-SLEUTEL".padEnd(90, "X");
  await page.route("**/api/v1/machine-keys**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET") {
      listRequests += 1;
      if (listMode === "offline") {
        await route.fulfill({ status: 502, json: { detail: "Testverbinding onderbroken", code: "upstream_unreachable" } });
      } else if (listMode === "restore") {
        await route.fulfill({ status: 409, json: { code: "machine_key_restore_pending", detail: "Machine-sleutels zijn tijdelijk gesloten zolang het back-upherstel niet is afgerond." } });
      } else {
        await route.fulfill({ json: { items } });
      }
    } else if (url.pathname.endsWith("/revoke")) {
      expect(request.headers()["idempotency-key"]).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
      items = items.map((item) => ({ ...item, revoked_at: "2026-11-18T12:00:00Z" }));
      await route.fulfill({ json: { item: items[0], already_revoked: false } });
    } else {
      const body = request.postDataJSON() as { name: string; scopes: string[] };
      writes.push(body);
      items = [{ id: "fixture", name: body.name, scopes: body.scopes, last_four: "UTEL", created_at: "2026-11-18T11:00:00Z", last_used_at: null, revoked_at: null }];
      await route.fulfill({ json: { item: items[0], key: fakeValue } });
    }
  });
  await page.goto("/instellingen?tab=gebruikers#machine-sleutels");
  const card = page.locator("#machine-sleutels");
  await expect(card.getByText("Nog geen machine-sleutels", { exact: true })).toBeVisible();
  const usersTab = page.getByRole("tab", { name: "Gebruikers" });
  const connectionsTab = page.getByRole("tab", { name: "Verbindingen" });
  await usersTab.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(connectionsTab).toBeFocused();
  await expect(connectionsTab).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("End");
  await expect(usersTab).toBeFocused();
  await expect(usersTab).toHaveAttribute("aria-selected", "true");
  await card.getByRole("button", { name: "Nieuwe machine-sleutel" }).click();
  const dialog = page.getByRole("dialog");
  const name = dialog.getByLabel("Naam (verplicht)");
  await expect(name).toBeFocused();
  await expect(dialog.getByRole("switch", { name: "Alleen lezen" })).toHaveAttribute("aria-checked", "false");
  await expect(dialog.getByRole("switch", { name: "Wijzigingen goedkeuren" })).toHaveAttribute("aria-checked", "false");
  await name.fill(fixtureName);
  await dialog.getByRole("button", { name: "Sleutel aanmaken", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("Kies minstens één recht");
  expect(writes).toEqual([]);
  await dialog.getByRole("switch", { name: "Alleen lezen" }).click();
  await page.emulateMedia({ forcedColors: "active", reducedMotion: "reduce" });
  const control = dialog.getByRole("switch", { name: "Alleen lezen" });
  await control.focus();
  expect(await control.evaluate((el) => {
    const style = getComputedStyle(el);
    return { outline: parseFloat(style.outlineWidth), transition: style.transitionDuration };
  })).toMatchObject({ transition: "0s" });
  const styles = await control.evaluate((el) => {
    const track = el.firstElementChild!;
    const knob = track.firstElementChild!;
    return { border: getComputedStyle(track).borderStyle, knob: getComputedStyle(knob).backgroundColor,
      track: getComputedStyle(track).backgroundColor, left: getComputedStyle(knob).left };
  });
  expect(styles.border).not.toBe("none");
  expect(styles.knob).not.toBe("rgba(0, 0, 0, 0)");
  expect(styles.knob).not.toBe(styles.track);
  await control.press("Space");
  const uncheckedLeft = await control.evaluate((el) => getComputedStyle(el.firstElementChild!.firstElementChild!).left);
  expect(uncheckedLeft).not.toBe(styles.left);
  await control.press("Space");
  await page.emulateMedia({ forcedColors: "none", reducedMotion: "reduce" });
  for (const theme of ["light", "dark"]) {
    await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await control.focus();
    const focus = await control.evaluate((el) => {
      const css = getComputedStyle(el);
      const canvas = document.createElement("canvas").getContext("2d")!;
      const rgb = (color: string) => {
        canvas.clearRect(0, 0, 1, 1); canvas.fillStyle = color; canvas.fillRect(0, 0, 1, 1);
        return [...canvas.getImageData(0, 0, 1, 1).data].slice(0, 3);
      };
      const lum = (color: string) => rgb(color).map((v) => v / 255).map((v) => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
        .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
      const ratio = (a: string, b: string) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
      let ancestor = el.parentElement!;
      while (ancestor.parentElement && ["rgba(0, 0, 0, 0)", "transparent"].includes(getComputedStyle(ancestor).backgroundColor)) ancestor = ancestor.parentElement;
      const background = getComputedStyle(ancestor).backgroundColor;
      const halo = css.boxShadow.match(/(?:rgb|rgba|color|oklch)\([^)]+\)/)?.[0] ?? background;
      return { width: parseFloat(css.outlineWidth), transition: css.transitionDuration,
        ringBackground: ratio(css.outlineColor, background), ringHalo: ratio(css.outlineColor, halo) };
    });
    expect(focus.width).toBeGreaterThanOrEqual(3);
    expect(focus.transition).toBe("0s");
    expect(focus.ringBackground).toBeGreaterThanOrEqual(3);
    expect(focus.ringHalo).toBeGreaterThanOrEqual(3);
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = "light"; });
  const checkAxe = async () => {
    const result = await new AxeBuilder({ page: page as unknown as ConstructorParameters<typeof AxeBuilder>[0]["page"] }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    expect(result.violations.map((v) => v.id)).toEqual([]);
  };
  const capture = async (state: string) => {
    if (testInfo.project.name.startsWith("mobile")) return;
    for (const font of ["systeem", "dejavu"]) {
      const override = font === "dejavu" ? await page.addStyleTag({ content: '* { font-family: "DejaVu Sans", sans-serif !important; }' }) : null;
      for (const theme of ["light", "dark"]) {
        await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
        for (const width of [320, 390, 768, 1440]) {
          await page.setViewportSize({ width, height: 900 });
          expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
          await page.screenshot({ path: testInfo.outputPath(`${state}-${font}-${width}-${theme}.png`), fullPage: true });
        }
      }
      await override?.evaluate((element) => element.parentNode?.removeChild(element));
    }
    await page.evaluate(() => { document.documentElement.dataset.theme = "light"; });
  };
  await checkAxe();
  await capture("aanmaken");
  await dialog.getByRole("button", { name: "Sleutel aanmaken", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "Machine-sleutel aangemaakt" })).toBeVisible();
  expect(writes).toEqual([{ name: fixtureName, scopes: ["read"] }]);
  await expect(dialog.getByText(fakeValue, { exact: true })).toBeVisible();
  await checkAxe();
  await capture("eenmalige-waarde");
  const stored = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, url: location.href }));
  expect(stored).not.toContain(fakeValue);
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true,
    value: { writeText: async () => { throw new Error("Testweigering klembord"); } } }));
  await dialog.getByRole("button", { name: "Kopiëren Machine-sleutel" }).click();
  expect(await page.evaluate(() => window.getSelection()?.toString())).toBe(fakeValue);
  await expect(dialog.getByText("Tekst geselecteerd. Kopieer via het menu van je browser.")).toBeVisible();
  await dialog.getByRole("button", { name: "Sluiten en waarde verbergen" }).click();
  await expect(page.getByText(fakeValue, { exact: true })).toHaveCount(0);
  await expect(card.getByText(fixtureName, { exact: true })).toBeVisible();
  await capture("lijst");
  for (const theme of ["light", "dark"]) {
    await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
    for (const width of [320, 360, 390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
      const table = card.getByRole("table", { name: "Machine-sleutels en hun rechten" });
      await expect(table.getByRole("columnheader")).toHaveCount(4);
      await expect(table.getByRole("cell")).toHaveCount(4);
      for (const code of await card.locator("tbody code").all()) {
        expect(await code.evaluate((element) => {
          const range = document.createRange(); range.selectNodeContents(element);
          return range.getClientRects().length;
        })).toBeLessThanOrEqual(1);
      }
    }
    await checkAxe();
  }
  await page.setViewportSize(testInfo.project.name.startsWith("mobile") ? { width: 390, height: 844 } : { width: 1440, height: 900 });
  listMode = "offline";
  const refresh = card.getByRole("button", { name: "Verversen", exact: true });
  await refresh.click();
  await expect(card.getByText(/Verouderd\. Laatst bekend/)).toBeVisible();
  await expect(refresh).toBeFocused();
  await expect(card.getByRole("button", { name: "Nieuwe machine-sleutel" })).toBeDisabled();
  listMode = "restore";
  await refresh.click();
  await expect(card.getByText(/Rond eerst het back-upherstel af/)).toBeVisible();
  await expect(card.getByText(fixtureName, { exact: true })).toBeVisible();
  await expect(card.getByRole("button", { name: "Nieuwe machine-sleutel" })).toBeDisabled();
  listMode = "available";
  await card.getByRole("button", { name: "Verversen", exact: true }).click();
  await expect(card.getByRole("button", { name: `Intrekken: ${fixtureName}` })).toBeEnabled();
  await card.getByRole("button", { name: `Intrekken: ${fixtureName}` }).click();
  const confirmation = page.getByRole("alertdialog");
  await expect(confirmation.getByRole("button", { name: "Annuleren" })).toBeFocused();
  await checkAxe();
  await capture("intrekken");
  await confirmation.getByRole("button", { name: "Sleutel definitief intrekken" }).click();
  await expect(card.getByText("Ingetrokken", { exact: true })).toBeVisible();
  await expect(card.getByRole("button", { name: `Intrekken: ${fixtureName}` })).toHaveCount(0);
  await page.reload();
  await expect(page.getByText(fakeValue, { exact: true })).toHaveCount(0);
  await checkAxe();
  const beforeViewer = listRequests;
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: { user: { id: 1, username: "fixture-kijker", role: "viewer" }, csrf_token: "fictief" } }));
  await page.reload();
  await expect(page.getByText("Alleen beheerders kunnen gebruikers beheren.")).toBeVisible();
  await expect(card).toHaveCount(0);
  expect(listRequests).toBe(beforeViewer);
});

// Foutpaden en late antwoorden blijven eveneens binnen deze ene browserpagina.
test("machine-sleutels: invoer behouden, wachttijd en laat antwoord", async ({ page }) => {
  test.setTimeout(90_000);
  const login = await page.request.post("/api/v1/auth/login", { data: {
    username: process.env.MSO_E2E_USER, password: process.env.MSO_E2E_PASSWORD,
  } });
  expect(login.status()).toBe(200);
  let mode: "duplicate" | "restore" | "limited" | "late" | "success" = "duplicate";
  let items: unknown[] = [];
  let count = 0;
  let release: (() => void) | undefined;
  const writes: unknown[] = [];
  const fakeValue = "DIT-IS-GEEN-SLEUTEL";
  await page.route("**/api/v1/machine-keys**", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { items } }); return;
    }
    count += 1;
    writes.push(route.request().postDataJSON());
    if (mode === "duplicate") {
      await route.fulfill({ status: 409, json: { code: "duplicate_name", detail: "Er bestaat al een machine-sleutel met deze naam. Kies een andere naam." } }); return;
    }
    if (mode === "restore") {
      await route.fulfill({ status: 409, json: { code: "machine_key_restore_pending", detail: "Machine-sleutels zijn tijdelijk gesloten zolang het back-upherstel niet is afgerond." } }); return;
    }
    if (mode === "limited") {
      await route.fulfill({ status: 429, headers: { "Retry-After": "2" }, json: { code: "rate_limited", detail: "Te veel mislukte pogingen. Wacht even en probeer het opnieuw." } }); return;
    }
    if (mode === "late") await new Promise<void>((resolve) => { release = resolve; });
    const body = route.request().postDataJSON();
    const item = { id: `fixture-${count}`, name: body.name, scopes: body.scopes, last_four: "UTEL",
      created_at: "2026-11-18T11:00:00Z", last_used_at: null, revoked_at: null };
    items = [item];
    await route.fulfill({ json: { item, key: fakeValue } });
  });
  await page.goto("/instellingen?tab=gebruikers#machine-sleutels");
  const card = page.locator("#machine-sleutels");
  await card.getByRole("button", { name: "Nieuwe machine-sleutel" }).click();
  const dialog = page.getByRole("dialog");
  const input = dialog.getByLabel("Naam (verplicht)");
  const submit = dialog.getByRole("button", { name: "Sleutel aanmaken", exact: true });
  await input.fill("Behouden invoer");
  await dialog.getByRole("switch", { name: "Alleen lezen" }).click();
  await submit.click();
  await expect(dialog.getByRole("alert")).toContainText("Er bestaat al een machine-sleutel");
  await expect(dialog.getByRole("alert")).toBeFocused();
  await expect(input).toHaveValue("Behouden invoer");
  await expect(dialog.getByRole("switch", { name: "Alleen lezen" })).toHaveAttribute("aria-checked", "true");
  mode = "restore";
  await submit.click();
  await expect(dialog.getByRole("alert")).toContainText("Rond eerst het back-upherstel af.");
  await expect(dialog.getByRole("alert")).toBeFocused();
  await expect(input).toHaveValue("Behouden invoer");
  mode = "limited";
  await submit.click();
  await expect(dialog.getByRole("alert")).toContainText("Te veel mislukte pogingen");
  await expect(submit).toBeDisabled();
  await expect(dialog.getByText(/Opnieuw proberen kan vanaf \d{2}:\d{2}:\d{2}/)).toBeInViewport();
  await expect(submit).toBeEnabled({ timeout: 5_000 });
  expect(count).toBe(3);
  mode = "late";
  await submit.click();
  await expect.poll(() => Boolean(release)).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  release!();
  await expect(card.getByText("Behouden invoer", { exact: true })).toBeVisible();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText(fakeValue, { exact: true })).toHaveCount(0);
  mode = "success";
  await card.getByRole("button", { name: "Nieuwe machine-sleutel" }).click();
  await expect(input).toHaveValue("");
  await expect(dialog.getByRole("switch", { name: "Alleen lezen" })).toHaveAttribute("aria-checked", "false");
  await expect(dialog.getByRole("switch", { name: "Wijzigingen goedkeuren" })).toHaveAttribute("aria-checked", "false");
  await input.fill("Alleen toekomstige goedkeuring");
  await dialog.getByRole("switch", { name: "Wijzigingen goedkeuren" }).click();
  await submit.click();
  await expect(dialog.getByText(fakeValue, { exact: true })).toBeVisible();
  expect(writes.at(-1)).toEqual({ name: "Alleen toekomstige goedkeuring", scopes: ["changes:approve"] });
  await page.goto("/");
  await page.goto("/instellingen?tab=gebruikers#machine-sleutels");
  await expect(card.getByText("Alleen toekomstige goedkeuring", { exact: true })).toBeVisible();
  await expect(page.getByText(fakeValue, { exact: true })).toHaveCount(0);
});
