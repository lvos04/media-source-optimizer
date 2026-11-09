import { mkdirSync } from "node:fs";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Full-app browser test against a running stack with mocks (tests/stack/run.sh with MSO_STACK_KEEP=1):
 *   MSO_E2E_BASE_URL=http://127.0.0.1:48081 MSO_E2E_USER=stackadmin MSO_E2E_PASSWORD=… npx playwright test e2e/screens.spec.ts
 *
 * Per screen × theme (dark/light) × viewport (project 1440 / 390):
 *  - renders real backend data (no error state, h1 present)
 *  - no horizontal page scroll
 *  - axe-core WCAG 2.x A/AA: zero serious/critical violations
 *  - every enabled button has an accessible name; every disabled button explains why (title / aria-describedby / text)
 *  - screenshot into docs/screenshots/ for the visual comparison with MediaGuard
 */
const USER = process.env.MSO_E2E_USER ?? "";
const PASSWORD = process.env.MSO_E2E_PASSWORD ?? "";
const SHOT_DIR = path.resolve(__dirname, "../../../docs/screenshots");

const SCREENS: Array<{ route: string; slug: string; title: RegExp }> = [
  { route: "/", slug: "overzicht", title: /Overzicht/ },
  { route: "/zoeken", slug: "zoeken", title: /Zoeken/ },
  { route: "/usenet-providers", slug: "usenet-providers", title: /Usenet/ },
  { route: "/nzb-indexers", slug: "nzb-indexers", title: /NZB/ },
  { route: "/torrent-indexers", slug: "torrent-indexers", title: /Torrent/ },
  { route: "/torrentclient", slug: "torrentclient", title: /Torrent/ },
  { route: "/downloads", slug: "downloads", title: /Downloads/ },
  { route: "/ontbrekend", slug: "ontbrekend", title: /Ontbrekend/ },
  { route: "/mediaguard", slug: "mediaguard", title: /MediaGuard/ },
  { route: "/opslag", slug: "opslag", title: /Opslag/ },
  { route: "/kosten", slug: "kosten", title: /Kosten/ },
  { route: "/automatisering", slug: "automatisering", title: /Automatisering/ },
  { route: "/automatisering/voetafdruk", slug: "voetafdruk", title: /Voetafdruk/ },
  { route: "/logs", slug: "logs", title: /Logs|Audit/ },
  { route: "/instellingen", slug: "instellingen", title: /Instellingen/ },
  { route: "/systeem", slug: "systeem", title: /Back-up|Systeem/ },
];

// One login per run (the app limits login attempts to 10/min per user — brute-force protection);
// every test reuses the session cookie.
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

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
  // skeletons gone (data rendered or an explicit state shown)
  await expect.poll(async () => page.locator(".skeleton").count(), { timeout: 15_000 }).toBe(0);
}

test.describe.configure({ mode: "default" });

test.beforeAll(() => {
  mkdirSync(SHOT_DIR, { recursive: true });
});

test("setup-scherm verschijnt niet meer en login werkt via formulier", async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  await page.goto("/login");
  await page.getByLabel("Gebruikersnaam").fill(USER);
  await page.getByLabel("Wachtwoord", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Inloggen" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});

for (const theme of ["dark", "light"] as const) {
  for (const s of SCREENS) {
    test(`${s.slug} · ${theme}`, async ({ page }, info) => {
      test.skip(!USER, "MSO_E2E_USER niet gezet");
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
      await page.addInitScript((t) => { try { localStorage.setItem("mso.theme", t); } catch { /* ignore */ } }, theme);
      await login(page);
      await page.goto(s.route);
      await settle(page);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.getByRole("heading", { level: 1 })).toHaveText(s.title);
      await expect(page.locator(".state--error"), "geen foutstaat").toHaveCount(0);

      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, "geen horizontale paginascroll").toBeLessThanOrEqual(1);

      const unnamed = await page.$$eval("button:not([disabled])", (bs) =>
        bs.filter((b) => !(b.getAttribute("aria-label") || b.textContent?.trim() || b.getAttribute("title"))).length);
      expect(unnamed, "knoppen zonder toegankelijke naam").toBe(0);
      const unexplained = await page.$$eval("button[disabled]", (bs) =>
        bs.filter((b) => !(b.getAttribute("title") || b.getAttribute("aria-describedby") || b.getAttribute("aria-busy") === "true"
          || b.closest("[data-disabled-reason]") || b.textContent?.trim())).map((b) => b.outerHTML.slice(0, 120)));
      expect(unexplained, "uitgeschakelde knoppen zonder uitleg").toEqual([]);

      const axe = await new AxeBuilder({ page: page as unknown as ConstructorParameters<typeof AxeBuilder>[0]["page"] }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
      const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(serious.map((v) => `${v.id}: ${v.nodes.length}× ${v.nodes[0]?.target.join(" ")}`), "axe serious/critical").toEqual([]);

      const width = info.project.name.includes("390") ? 390 : 1440;
      await page.screenshot({ path: path.join(SHOT_DIR, `${s.slug}-${theme}-${width}.png`), fullPage: width === 1440 });
      expect(errors, "geen JavaScript-fouten").toEqual([]);
    });
  }
}

test("toetsenbord: skip-link en zichtbare focus", async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  await login(page);
  await page.goto("/");
  await settle(page);
  await page.keyboard.press("Tab");
  const first = await page.evaluate(() => ({ text: document.activeElement?.textContent?.trim(), href: (document.activeElement as HTMLAnchorElement | null)?.getAttribute("href") }));
  expect(first.href, "eerste tabstop is de skip-link").toBe("#main");
  await page.keyboard.press("Tab");
  const outline = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el) return "none";
    const cs = getComputedStyle(el);
    return `${cs.outlineStyle} ${cs.outlineWidth} ${cs.boxShadow}`;
  });
  expect(outline, "focus is zichtbaar").not.toBe("none 0px none");
});

test("alleen-lezen modus blokkeert schrijfknoppen met uitleg", async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  await login(page);
  const mode = await (await page.request.get("/api/v1/system/mode")).json();
  test.skip(mode.mode !== "read_only", "stack staat in beheermodus");
  await page.goto("/automatisering");
  await settle(page);
  const apply = page.getByRole("button", { name: /Toepassen/ });
  if (await apply.count()) {
    await expect(apply.first()).toBeDisabled();
    await expect(apply.first()).toHaveAttribute("title", /alleen-lezen|beheermodus/i);
  }
});
