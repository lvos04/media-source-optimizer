import { mkdirSync } from "node:fs";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";

import AxeBuilder from "@axe-core/playwright";
import { expect, test as base, type Page } from "@playwright/test";

// De schermmatrix gebruikt één bron en één gebruiker. Houd tussen schermen ruimte
// onder de standaardgrens van 600/min, ook als een snelle browser veel pagina's opent.
const test = base.extend({
  page: async ({ page }, runTest) => {
    const start = performance.now();
    let apiRequests = 0;
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/api/")) apiRequests++;
    });
    await runTest(page);
    const rest = apiRequests * (60_000 / 400) - (performance.now() - start);
    if (rest > 0) await delay(rest);
  },
});

/**
 * Full-app browser test against a running stack with mocks. `make e2e` (repo root) starts a fresh stack, prepares it
 * (MSO_STACK_UP_ONLY=1 MSO_STACK_E2E_PREPARE=1 tests/stack/run.sh: admin stackadmin, connectors against the mocks,
 * one backup, first-run wizard completed) and runs every spec strictly (MSO_E2E_STRICT=1: a skip fails the run).
 * Against a stack prepared that way and kept running:
 *   MSO_E2E_BASE_URL=http://127.0.0.1:48081 MSO_E2E_USER=stackadmin MSO_E2E_PASSWORD=… npx playwright test e2e/screens.spec.ts
 * (A stack from plain tests/stack/run.sh with MSO_STACK_KEEP=1 is NOT enough: its wizard is not completed.)
 *
 * Per screen × theme (dark/light) × viewport (project 1440 / 390):
 *  - renders real backend data (no error state, h1 present)
 *  - no horizontal page scroll, measured against the DEVICE width (ADR-0006 punt 7: with isMobile Chromium widens the
 *    layout viewport to the widest content, so scrollWidth − innerWidth is always 0 and hides real overflow)
 *  - axe-core with the Peil tag set wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa: zero violations of any impact
 *  - every enabled button has an accessible name; every disabled button explains why (title / aria-describedby / text)
 *  - screenshot into docs/screenshots/ for the visual comparison with MediaGuard
 */
const USER = process.env.MSO_E2E_USER ?? "";
const PASSWORD = process.env.MSO_E2E_PASSWORD ?? "";
const SHOT_DIR = process.env.MSO_STACK_RESULTS_ROOT
  ? path.join(process.env.MSO_STACK_RESULTS_ROOT, "screenshots")
  : path.resolve(__dirname, "../../../docs/screenshots");

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

test("Systeem toont de buildgegevens uit de core-image", async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  const fields = [
    { name: "version", label: "Versie", value: process.env.MSO_BUILD_VERSION },
    { name: "git_sha", label: "Commit", value: process.env.MSO_BUILD_GIT_SHA },
    // De API en het datetime-attribuut gebruiken de canonieke UTC-notatie van contract §5.
    { name: "build_time", label: "Bouwtijd", value: process.env.MSO_BUILD_TIME?.trim().replace(/\+00:00$/, "Z") },
    { name: "channel", label: "Kanaal", value: process.env.MSO_BUILD_CHANNEL },
  ];
  await login(page);
  const response = await page.request.get("/api/v1/system/info");
  expect(response.ok()).toBe(true);
  const body = await response.json();
  await page.goto("/systeem");
  const card = page.getByRole("region", { name: "MSO-core" });
  await expect(card.getByText("Buildgegevens uit de core-image")).toBeVisible();
  const unknownReason = card.getByText("Bij onbekende waarden ontbreekt het buildargument of is het ongeldig.");
  if (fields.some(({ value }) => !value?.trim() || value.trim() === "onbekend")) {
    await expect(unknownReason).toBeVisible();
  } else {
    await expect(unknownReason).toHaveCount(0);
  }

  for (const { name, label, value } of fields) {
    const expected = value?.trim() || "onbekend";
    expect(body[name], name).toBe(expected);
    const cell = card.locator(`dt:text-is("${label}") + dd`);
    if (expected === "onbekend") {
      await expect(cell.locator('[data-status="unknown"]')).toHaveText("Onbekend");
      await expect(cell.locator("time")).toHaveCount(0);
    } else if (name === "build_time") {
      const display = new Date(expected).toLocaleString("nl-NL", {
        dateStyle: "medium", timeStyle: "medium", timeZone: "UTC",
      });
      await expect(cell.locator("time")).toHaveAttribute("datetime", expected);
      await expect(cell).toHaveText(`${display} UTC`);
    } else if (name === "channel") {
      const labels = new Map([["development", "Ontwikkeling"], ["release", "Release"]]);
      await expect(cell).toHaveText(labels.get(expected) ?? expected);
    } else {
      await expect(cell).toHaveText(expected);
    }
  }
});

for (const theme of ["dark", "light"] as const) {
  test(`Back-up maken heeft zichtbare toetsenbordfocus · ${theme}`, async ({ page }) => {
    test.skip(!USER, "MSO_E2E_USER niet gezet");
    await page.addInitScript((value) => localStorage.setItem("mso.theme", value), theme);
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await login(page);
    await page.goto("/systeem");
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);

    const button = page.getByTestId("backup-create");
    await expect(button).toBeEnabled();
    for (let stap = 0; stap < 80 && !(await button.evaluate((el) => el.matches(":focus-visible"))); stap += 1) {
      await page.keyboard.press("Tab");
    }
    await expect(button).toBeFocused();

    const focus = await button.evaluate((el) => {
      const style = getComputedStyle(el);
      const surface = el.closest("section")!;
      const canvas = document.createElement("canvas").getContext("2d")!;
      const luminance = (color: string) => {
        canvas.clearRect(0, 0, 1, 1);
        canvas.fillStyle = color;
        canvas.fillRect(0, 0, 1, 1);
        return [...canvas.getImageData(0, 0, 1, 1).data].slice(0, 3)
          .map((value) => value / 255)
          .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
          .reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
      };
      const ring = luminance(style.outlineColor);
      const background = luminance(getComputedStyle(surface).backgroundColor);
      return {
        width: style.outlineStyle === "none" ? 0 : parseFloat(style.outlineWidth),
        transition: style.transitionProperty,
        contrast: (Math.max(ring, background) + 0.05) / (Math.min(ring, background) + 0.05),
      };
    });
    expect.soft(focus.width).toBeGreaterThanOrEqual(3);
    expect.soft(focus.contrast).toBeGreaterThanOrEqual(3);
    expect.soft(focus.transition).toBe("none");
  });

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

      const deviceWidth = page.viewportSize()?.width ?? 1440;
      const overflow = await page.evaluate((w) => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - w, deviceWidth);
      expect(overflow, `geen horizontale paginascroll t.o.v. de apparaatbreedte (${deviceWidth} px)`).toBeLessThanOrEqual(1);

      const unnamed = await page.$$eval("button:not([disabled])", (bs) =>
        bs.filter((b) => !(b.getAttribute("aria-label") || b.textContent?.trim() || b.getAttribute("title"))).length);
      expect(unnamed, "knoppen zonder toegankelijke naam").toBe(0);
      const unexplained = await page.$$eval('button[disabled], button[aria-disabled="true"]', (bs) =>
        bs.filter((b) => !(b.getAttribute("title") || b.getAttribute("aria-describedby") || b.getAttribute("aria-busy") === "true"
          || b.closest("[data-disabled-reason]") || b.textContent?.trim())).map((b) => b.outerHTML.slice(0, 120)));
      expect(unexplained, "uitgeschakelde knoppen zonder uitleg").toEqual([]);

      const axe = await new AxeBuilder({ page: page as unknown as ConstructorParameters<typeof AxeBuilder>[0]["page"] }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
      expect(axe.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length}× ${v.nodes[0]?.target.join(" ")}`), "axe, alle impacts").toEqual([]);

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

test("Opslag en groei toont de laad-, lege, trend- en storingstoestanden", async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  await login(page);
  const response = await page.request.get("/api/v1/system/maintenance");
  expect(response.status()).toBe(200);
  const native = await response.json();

  const first = { day: "2026-10-19", observed_at: "2026-10-19T05:00:00Z", values: { database_bytes: 1000 } };
  const last = { day: "2026-10-21", observed_at: "2026-10-21T05:00:00Z", values: { database_bytes: 1040 } };
  const gap = { day: "2026-10-20", observed_at: null, values: null };
  let scenario = "empty";
  let releaseResponse!: () => void;
  const loading = new Promise<void>((resolve) => { releaseResponse = resolve; });

  await page.route("**/api/v1/system/maintenance", async (route) => {
    await loading;
    if (scenario === "error") {
      await route.fulfill({ status: 500, json: { detail: "De proefmeting is onbereikbaar." } });
      return;
    }
    const days = scenario === "empty" ? [] : scenario === "single" ? [last] : [last, gap, first];
    const databaseTrend = scenario === "trend"
      ? { value: 20, from_day: first.day, to_day: last.day, quality: "gemeten" }
      : { value: null, from_day: null, to_day: null, quality: "onbekend" };
    await route.fulfill({
      json: {
        ...native,
        growth: { latest: days[0] ?? null, days, per_day: { database_bytes: databaseTrend }, tables: [] },
        provenance: {
          database_bytes: { source: "PostgreSQL", quality: "gemeten", observed_at: last.observed_at },
        },
      },
    });
  });

  const section = page.getByRole("region", { name: "Opslag en groei" });
  await page.goto("/systeem");
  try {
    await expect(section.locator(".skeleton").first()).toBeVisible();
  } finally {
    releaseResponse();
  }
  await expect(section.getByText("Nog geen metingen")).toBeVisible();

  scenario = "single";
  await page.reload();
  await expect(section.getByText("Te weinig metingen voor een trend.")).toBeVisible();
  await expect(section.getByText("PostgreSQL · Gemeten")).toBeVisible();

  scenario = "trend";
  await page.reload();
  await expect(section.getByText("20 B per dag")).toBeVisible();
  await section.getByText("Dagmetingen (UTC)", { exact: true }).click();
  await expect(section.getByText("Geen meting", { exact: true })).toBeVisible();
  await expect(section.getByText("19 okt 2026", { exact: true })).toBeVisible();

  scenario = "error";
  await page.reload();
  const alert = section.getByRole("alert");
  await expect(alert.getByText("Ophalen is mislukt.")).toBeVisible();
  scenario = "empty";
  await alert.getByRole("button", { name: "Opnieuw proberen" }).click();
  await expect(section.getByText("Nog geen metingen")).toBeVisible();
});
