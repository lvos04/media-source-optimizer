import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Peil-testmatrix (ONTWERPTAAL §12, PEIL_MIGRATIE §10.2) against a stack prepared by `make e2e`. Runs only in the
 * desktop project (playwright.config.ts: the mobile project ignores this file) and sets its own viewports:
 *  - main screens × 320 / 768 / 1024 (light): no horizontal overflow against the device width, axe (wcag2a, 2aa, 21a,
 *    21aa, 22aa) = 0 violations of any impact (Peil §12; review stap 5), exactly one h1, no duplicate ids, every control in <main> ≥ 44 px
 *  - axe on the shell's dialogs and sheets (Meer, pauze-dialoog, profielmenu) in light and dark
 *  - status shapes: an unknown status never carries the pause icon; only a health badge is green
 *  - keyboard: every tab stop has the Peil ring (≥ 3 px) and is never hidden behind the top bar or bottom navigation
 *  - charts: table twin + keyboard read-out; "Terug naar de Hub" only for a valid systeem.hub_url
 *  - costs (B05-008): services without a price ⇒ "Deels onbekend" (unknown shape) on Kosten and Overzicht, no savings promise
 */
const USER = process.env.MSO_E2E_USER ?? "";
const PASSWORD = process.env.MSO_E2E_PASSWORD ?? "";
const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

const MAIN: Array<{ route: string; slug: string }> = [
  { route: "/", slug: "overzicht" },
  { route: "/zoeken", slug: "zoeken" },
  { route: "/downloads", slug: "downloads" },
  { route: "/ontbrekend", slug: "ontbrekend" },
  { route: "/usenet-providers", slug: "usenet-providers" },
  { route: "/automatisering", slug: "automatisering" },
  { route: "/instellingen", slug: "instellingen" },
  { route: "/systeem", slug: "systeem" },
];

let sessionCookies: Array<{ name: string; value: string; domain: string; path: string }> = [];
async function login(page: Page): Promise<void> {
  if (!sessionCookies.length) {
    const r = await page.request.post("/api/v1/auth/login", { data: { username: USER, password: PASSWORD } });
    expect(r.status(), "login via API").toBe(200);
    sessionCookies = (await page.context().cookies()).filter((c) => c.name === "mso_session");
    return;
  }
  await page.context().addCookies(sessionCookies);
}

async function open(page: Page, route: string, theme: "light" | "dark" = "light"): Promise<void> {
  await page.addInitScript((t) => { try { localStorage.setItem("mso.theme", t); } catch { /* ignore */ } }, theme);
  await login(page);
  await page.goto(route);
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
  await expect.poll(async () => page.locator(".skeleton").count(), { timeout: 15_000 }).toBe(0);
}

async function axe(page: Page): Promise<string[]> {
  const res = await new AxeBuilder({ page: page as unknown as ConstructorParameters<typeof AxeBuilder>[0]["page"] }).withTags(AXE_TAGS).analyze();
  // Peil §12: axe = 0, ongeacht de impact (tot stap 5 alleen serious/critical; de review mat 0 op alle impacts)
  return res.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length}× ${v.nodes[0]?.target.join(" ")}`);
}

test.describe.configure({ mode: "default" });

for (const width of [320, 768, 1024]) {
  for (const s of MAIN) {
    test(`peil ${s.slug} · ${width}`, async ({ page }) => {
      test.skip(!USER, "MSO_E2E_USER niet gezet");
      await page.setViewportSize({ width, height: 900 });
      await open(page, s.route);
      const overflow = await page.evaluate((w) => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - w, width);
      expect(overflow, `geen horizontale overloop op ${width} px`).toBeLessThanOrEqual(1);
      expect(await axe(page), "axe (Peil-set), alle impacts").toEqual([]);
      expect(await page.locator("h1").count(), "precies één h1").toBe(1);
      const dupIds = await page.evaluate(() => {
        const seen = new Map<string, number>();
        for (const el of document.querySelectorAll("[id]")) seen.set(el.id, (seen.get(el.id) ?? 0) + 1);
        return [...seen].filter(([, n]) => n > 1).map(([id]) => id);
      });
      expect(dupIds, "geen dubbele id's").toEqual([]);
      // 44 px for every button, tab, switch, select and input in <main> (inline links in running text are exempt)
      const small = await page.evaluate(() => {
        const out: string[] = [];
        for (const el of document.querySelectorAll<HTMLElement>("main button, main [role=tab], main [role=switch], main select, main input:not([type=checkbox]):not([type=radio]):not([type=hidden]), main summary")) {
          const r = el.getBoundingClientRect();
          if (!r.width || !r.height || el.closest(".sr-only")) continue;
          if (r.height < 43.5) out.push(`${el.tagName.toLowerCase()} "${(el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 30)}" ${Math.round(r.height)}px`);
        }
        return out;
      });
      expect(small, "bedienbare elementen in main ≥ 44 px hoog").toEqual([]);
      // Peil §7: unknown is never shown with the pause icon; only measured health uses the green "healthy" badge
      const wrongIcons = await page.locator('[data-status="unknown"] .lucide-pause, [data-status="unknown"] .lucide-circle-pause').count();
      expect(wrongIcons, "Onbekend nooit met het pauze-icoon").toBe(0);
      const greenOutsideHealth = await page.locator('.chip--healthy:not([data-status="healthy"]), .chip--success').count();
      expect(greenOutsideHealth, "groen alleen voor gemeten gezondheid").toBe(0);
    });
  }
}

for (const theme of ["light", "dark"] as const) {
  test(`peil dialogen en lades zonder axe-fouten · ${theme}`, async ({ page }) => {
    test.skip(!USER, "MSO_E2E_USER niet gezet");
    // pause dialog and profile menu (desktop)
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, "/automatisering", theme);
    const pause = page.getByTestId("pause-button");
    await expect(pause).toBeEnabled({ timeout: 15_000 });
    await pause.click();
    await expect(page.getByRole("alertdialog")).toBeVisible();
    expect(await axe(page), "pauze-dialoog").toEqual([]);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("alertdialog")).toBeHidden();
    await expect(pause, "focus keert terug naar de knop").toBeFocused();
    await page.getByRole("button", { name: /profiel en thema/ }).click();
    await expect(page.getByRole("dialog", { name: "Profiel" })).toBeVisible();
    expect(await axe(page), "profielmenu").toEqual([]);
    // "Meer" sheet (phone width)
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page.getByRole("navigation", { name: /mobiel/i }).getByRole("button", { name: "Meer" }).click();
    const sheet = page.getByRole("dialog", { name: "Meer" });
    await expect(sheet).toBeVisible();
    expect(await axe(page), "Meer-sheet").toEqual([]);
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
  });
}

for (const width of [390, 1440]) {
  test(`peil toetsenbord: ring ≥ 3 px en nooit bedekt · ${width}`, async ({ page }) => {
    test.skip(!USER, "MSO_E2E_USER niet gezet");
    await page.setViewportSize({ width, height: width < 1024 ? 844 : 900 });
    await open(page, "/instellingen");
    const problems: string[] = [];
    for (let i = 0; i < 40; i++) {
      await page.keyboard.press("Tab");
      const r = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el || el === document.body) return null;
        const cs = getComputedStyle(el);
        const box = el.getBoundingClientRect();
        const name = (el.getAttribute("aria-label") || el.textContent || el.tagName).trim().slice(0, 30);
        const ring = cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) >= 3;
        // covered: the centre point is occupied by the sticky top bar or the bottom navigation
        const cx = Math.min(innerWidth - 1, Math.max(0, box.left + box.width / 2));
        const cy = Math.min(innerHeight - 1, Math.max(0, box.top + Math.min(box.height / 2, 10)));
        const hit = document.elementFromPoint(cx, cy);
        const bar = hit?.closest(".topbar, .bottomnav");
        const covered = Boolean(bar && !bar.contains(el));
        return { name, ring, covered, skip: el.classList.contains("skip-link") };
      });
      if (!r || r.skip) continue;
      if (!r.ring) problems.push(`geen Peil-ring: ${r.name}`);
      if (r.covered) problems.push(`bedekt door kop/ondernavigatie: ${r.name}`);
    }
    expect(problems).toEqual([]);
  });
}

test("peil grafiek: tabelweergave en toetsenbord bij 'Downloads in * per dag'", async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, "/usenet-providers");
  const chart = page.getByTestId("grafiek-ster-per-dag");
  await expect(chart).toBeVisible();
  const plot = chart.locator('[tabindex="0"]');
  await expect(plot).toHaveAttribute("aria-label", /pijltjestoetsen/);
  await plot.focus();
  await page.keyboard.press("End");
  const live = chart.locator("p[aria-live=polite]");
  await expect(live).toHaveText(/: \d+ van \d+ in '\*'/);
  await page.keyboard.press("Home");
  const firstReadout = await live.textContent();
  await page.keyboard.press("Escape");
  await expect(live).toHaveText("");
  const table = chart.getByTestId("grafiek-ster-per-dag-tabel");
  await table.getByText("Toon als tabel").click();
  await expect(table.getByText("Verberg tabel")).toBeVisible();
  const rows = table.locator("tbody tr");
  expect(await rows.count()).toBeGreaterThan(1);
  // the first table row is the first point of the chart
  const firstDay = (await rows.first().locator("th").textContent())?.trim() ?? "";
  expect(firstReadout).toContain(firstDay);
});

test("peil grafiek: tabel bij de oorzaakverdeling (Source Baseline)", async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, "/ontbrekend");
  const twin = page.getByTestId("grafiek-oorzaken-tabel");
  await expect(twin).toBeVisible();
  await twin.getByText("Toon als tabel").click();
  await expect(twin.locator("table caption")).toBeVisible();
  await expect(twin.locator("thead th").filter({ hasText: "Onbekend" })).toHaveCount(1);
});

// B05-008 (acceptatie: "Provider zonder stats toont unknown; geen gegarandeerde besparing claimen") in de Peil-weergave:
// diensten die MSO uit de mocks importeert hebben nog geen prijs. Dan is een totaal nooit stil compleet, maar
// "Deels onbekend" in de onbekend-vorm (stippel + circle-help), op Kosten én op het Overzicht, en nergens een besparingsbelofte.
test("peil kosten: dienst zonder prijs ⇒ 'Deels onbekend', geen besparingsbelofte (B05-008)", async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, "/kosten");
  // precondition of the prepared stack (GET imports the services idempotently; only the MSO database)
  const services = (await (await page.request.get("/api/v1/costs/services")).json()) as { price_missing: number };
  expect(services.price_missing, "voorbereide stack: diensten zonder prijs").toBeGreaterThan(0);
  const summary = (await (await page.request.get("/api/v1/costs/summary")).json()) as { total: { unknown: unknown[] } };
  expect(summary.total.unknown.length, "API: totaal met onbekende delen").toBeGreaterThan(0);
  await page.reload();
  await expect.poll(async () => page.locator(".skeleton").count(), { timeout: 15_000 }).toBe(0);
  const total = page.locator(".kpi").filter({ hasText: "Totaal per maand" });
  const partly = total.locator('[data-status="unknown"]').filter({ hasText: "Deels onbekend" });
  await expect(partly).toBeVisible();
  await expect(partly).toHaveClass(/border-dashed/);
  await expect(partly.locator(".lucide-circle-question-mark")).toHaveCount(1);
  await expect(total.locator(".chip--healthy, .chip--warning, .lucide-circle-check")).toHaveCount(0);
  // the "price missing" banner uses the unknown shape too (never amber)
  await expect(page.locator('.inline-state[data-tone="unknown"]').filter({ hasText: "zonder prijs" })).toBeVisible();
  const text = (await page.locator("main").innerText()).toLowerCase();
  expect(text).toContain("belooft geen besparing");
  expect(text).not.toMatch(/gegarandeerde? besparing|bespaar(t|ing)? gegarandeerd|garantie op besparing/);
  // Overzicht: the cost KPI is never a silently complete amount either
  await open(page, "/");
  const kpi = page.locator(".kpi").filter({ hasText: "Kosten per maand" });
  await expect(kpi.locator('[data-status="unknown"]').filter({ hasText: "Deels onbekend" })).toBeVisible();
});

test("peil Terug naar de Hub: alleen bij een geldig systeem.hub_url", async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, "/");
  const csrf = (await (await page.request.get("/api/v1/auth/me")).json()).csrf_token as string;
  const put = (value: string) => page.request.put("/api/v1/settings", { data: { values: { "systeem.hub_url": value } }, headers: { "X-CSRF-Token": csrf } });
  try {
    expect((await put("")).status()).toBe(200);
    await page.reload();
    await expect(page.getByTestId("sidebar-health")).toBeVisible();
    await expect(page.getByTestId("hub-link"), "leeg ⇒ geen link").toHaveCount(0);

    for (const bad of ["javascript:alert(1)", "http://nas.lan:30080/pad", "http://127.0.0.1:30080"]) {
      expect((await put(bad)).status(), `422 voor ${bad}`).toBe(422);
    }
    await page.reload();
    await expect(page.getByTestId("sidebar-health")).toBeVisible();
    await expect(page.getByTestId("hub-link"), "ongeldig ⇒ geen link").toHaveCount(0);

    expect((await put("http://hub.thuis.test:30080")).status()).toBe(200);
    await page.reload();
    const link = page.getByTestId("hub-link");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "http://hub.thuis.test:30080");
    await expect(link).toHaveText(/Terug naar de Hub/);
    // phone: first item of the "Meer" sheet
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("navigation", { name: /mobiel/i }).getByRole("button", { name: "Meer" }).click();
    await expect(page.getByRole("dialog", { name: "Meer" }).getByTestId("hub-link")).toBeVisible();
  } finally {
    await put("");
  }
});
