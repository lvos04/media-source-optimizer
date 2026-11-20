import { expect, test, type Cookie, type Locator, type Page, type Route } from "@playwright/test";

const USER = process.env.MSO_E2E_USER ?? "";
const PASSWORD = process.env.MSO_E2E_PASSWORD ?? "";
const WIDTHS = [360, 390, 768, 1440];
const ITEMS_EMPTY = { items: [] };
const ERROR = { detail: "De proefbron geeft een storing.", code: "internal" };

interface Screen {
  name: string;
  path: string;
  endpoint: string;
  region?: string;
  emptyText: string;
  empty: object | ((route: Route) => Promise<object>);
}

// Behoud de velden die buiten de lege lijst zichtbaar horen te blijven.
async function emptyOverview(route: Route) {
  const response = await route.fetch();
  expect(response.ok()).toBe(true);
  const overview = await response.json();
  return { ...overview, failures: { ...overview.failures, available: true, by_location: [] } };
}

const SCREENS: Screen[] = [
  { name: "Overzicht", path: "/", endpoint: "/overview", empty: emptyOverview,
    emptyText: "Geen mislukte downloads in de afgelopen 7 dagen" },
  { name: "Zoeken", path: "/zoeken?q=Peilproef", endpoint: "/search", region: "Resultaten",
    empty: { items: [], sources: [], trace: [], trace_id: "peilproef", total: 0, offset: 0, limit: 50 }, emptyText: "Geen resultaten" },
  { name: "Usenet-providers", path: "/usenet-providers", endpoint: "/usenet/providers", region: "Providers en blokaccounts",
    empty: ITEMS_EMPTY, emptyText: "Nog geen Usenet-providers" },
  { name: "NZB-indexers", path: "/nzb-indexers", endpoint: "/indexers", region: "Indexers",
    empty: ITEMS_EMPTY, emptyText: "Nog geen indexers" },
  { name: "Torrent-indexers", path: "/torrent-indexers", endpoint: "/indexers", region: "Indexers",
    empty: ITEMS_EMPTY, emptyText: "Nog geen indexers" },
  { name: "Torrentclient", path: "/torrentclient", endpoint: "/torrent/tracker-rules", region: "Seedregels per tracker/categorie",
    empty: { items: [], known_trackers: [] }, emptyText: "Nog geen seedregels" },
  { name: "Downloads", path: "/downloads", endpoint: "/usenet/sab/queue", region: "Wachtrij (SABnzbd)",
    empty: ITEMS_EMPTY, emptyText: "Wachtrij is leeg" },
  { name: "Ontbrekend", path: "/ontbrekend", endpoint: "/recovery/wanted", region: "Ontbrekende items",
    empty: { items: [], total: 0, mode: "standalone" }, emptyText: "Geen ontbrekende items" },
  { name: "MediaGuard", path: "/mediaguard", endpoint: "/mediaguard/search-requests", region: "Zoekverzoeken aan MediaGuard",
    empty: ITEMS_EMPTY, emptyText: "Nog geen zoekverzoeken" },
  { name: "Opslag", path: "/opslag", endpoint: "/torrent/storage", region: "Gebruik per categorie",
    empty: {
      client: { available: true, updated_at: null, error: null }, disk: null, categories: [], obligations: [],
      obligations_summary: { total: 0, met: 0, open: 0, private_without_rule: 0 }, media: null, hardlinks: null,
      hardlinks_scanned_at: null, never: [],
    }, emptyText: "Geen torrents in qBittorrent" },
  { name: "Kosten", path: "/kosten", endpoint: "/costs/services", region: "Diensten",
    empty: ITEMS_EMPTY, emptyText: "Nog geen diensten" },
  { name: "Automatisering", path: "/automatisering", endpoint: "/automation/recommendations", region: "Aanbevelingen",
    empty: ITEMS_EMPTY, emptyText: "Geen aanbevelingen met dit filter" },
  { name: "Voetafdruk", path: "/automatisering/voetafdruk", endpoint: "/automation/footprint",
    empty: {
      totals: { actief: 0, gewijzigd: 0, onbereikbaar: 0, verdwenen: 0 }, last_run: null, systems: [],
      irreversible: [], overrides: [], override_days: 0, mode: "read_only", restore_allowed: false,
    }, emptyText: "MSO heeft niets veranderd dat nu nog actief is" },
  { name: "Logs", path: "/logs", endpoint: "/audit", region: "Audittrail",
    empty: { items: [], next_before_id: null }, emptyText: "Geen auditregels gevonden" },
  { name: "Instellingen", path: "/instellingen", endpoint: "/settings",
    empty: { definitions: [], values: {} }, emptyText: "Geen instellingen in deze categorie" },
  { name: "Systeem", path: "/systeem", endpoint: "/backups", region: "Back-ups",
    empty: ITEMS_EMPTY, emptyText: "Nog geen back-ups" },
];

let sessionCookies: Cookie[] = [];

test.beforeEach(async ({ page }) => {
  test.skip(!USER, "MSO_E2E_USER niet gezet");
  if (sessionCookies.length === 0) {
    const response = await page.request.post("/api/v1/auth/login", { data: { username: USER, password: PASSWORD } });
    expect(response.status(), "inloggen met de testgebruiker").toBe(200);
    sessionCookies = (await page.context().cookies()).filter((cookie) => cookie.name === "mso_session");
    expect(sessionCookies).toHaveLength(1);
  }
  await page.context().addCookies(sessionCookies);
  await page.setViewportSize({ width: 360, height: 800 });
});

function apiPath(endpoint: string) {
  return (url: URL) => url.pathname === `/api/v1${endpoint}`;
}

async function expectStateAtWidths(page: Page, state: string, message: Locator, retry?: Locator) {
  try {
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 800 });
      const description = `${state} op ${width} px`;
      await expect(message, `melding bij ${description}`).toBeVisible();
      if (retry) {
        await expect(retry, `herhaalactie bij ${description}`).toBeVisible();
        await expect(retry, `herhaalactie bij ${description}`).toBeEnabled();
      }
      const pageWidth = await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth));
      expect(pageWidth, `paginabreedte bij ${description}`).toBeLessThanOrEqual(width + 1);
    }
  } finally {
    await page.setViewportSize({ width: WIDTHS[0], height: 800 });
  }
}

test("de breedteproef weigert een melding en herhaalactie die op 768 px verdwijnen", async ({ page }) => {
  for (const [selector, failure] of [["p", "melding"], ["button", "herhaalactie"]]) {
    await page.setContent(`
      <style>@media (width: 768px) { ${selector} { visibility: hidden } }</style>
      <p>Proefstoring</p><button>Opnieuw proberen</button>
    `);
    const check = expectStateAtWidths(page, "storing", page.getByText("Proefstoring"), page.getByRole("button"));
    await expect(check).rejects.toThrow(`${failure} bij storing op 768 px`);
  }
});

for (const screen of SCREENS) {
  test(`${screen.name}: laden, storing, offline en leeg`, async ({ page }) => {
    let releaseLoading!: () => void;
    const pending = new Promise<void>((resolve) => { releaseLoading = resolve; });
    let state: "error" | "offline" | "empty" = "error";
    await page.route(apiPath(screen.endpoint), async (route) => {
      await pending;
      switch (state) {
        case "error": return route.fulfill({ status: 500, json: ERROR });
        case "offline": return route.abort("internetdisconnected");
        case "empty": return route.fulfill({ json: typeof screen.empty === "function" ? await screen.empty(route) : screen.empty });
      }
    });

    const main = page.getByRole("main");
    const region = screen.region ? main.getByRole("region", { name: screen.region }) : main;
    const empty = region.locator('[data-state="empty"]').getByText(screen.emptyText, { exact: true }).first();
    const error = region.getByRole("alert").filter({ hasText: "De proefbron geeft een storing." }).first();
    const offline = region.locator('[data-state="offline"]').filter({ hasText: "Backend niet bereikbaar" }).first();
    const errorRetry = error.getByRole("button", { name: "Opnieuw proberen" });
    const offlineRetry = offline.getByRole("button", { name: "Opnieuw proberen" });
    await page.goto(screen.path);
    try {
      await expect(main.getByRole("heading", { level: 1 })).toBeVisible();
      await expectStateAtWidths(page, "laden", region.getByText("Gegevens ophalen…", { exact: true }).first());
    } finally {
      releaseLoading();
    }

    await expect(empty).toHaveCount(0);
    await expectStateAtWidths(page, "storing", error.getByText("Ophalen is mislukt."), errorRetry);
    state = "offline";
    await errorRetry.click();
    await expect(empty).toHaveCount(0);
    await expectStateAtWidths(page, "offline", offline, offlineRetry);

    state = "empty";
    await offlineRetry.click();
    await expect(offline).toHaveCount(0);
    await expectStateAtWidths(page, "leeg", empty);
  });
}

test("Logs maakt een eerder lege lijst herkenbaar als verouderd wanneer verversen offline faalt", async ({ page }) => {
  let offline = false;
  await page.route(apiPath("/audit"), (route) => offline
    ? route.abort("internetdisconnected")
    : route.fulfill({ json: { items: [], next_before_id: null } }));
  await page.goto("/logs");
  const audit = page.getByRole("region", { name: "Audittrail" });
  await expect(audit.getByText("Geen auditregels gevonden")).toBeVisible();
  await expect(audit.getByText(/Verouderd · laatst bekend/)).toHaveCount(0);

  offline = true;
  await audit.getByRole("button", { name: "Verversen" }).click();
  await expect(audit.locator('[data-status="stale"]')).toHaveText(/Verouderd · laatst bekend.*verversen mislukt/);
  await expect(audit.getByText("Geen auditregels gevonden")).toBeVisible();
});

for (const { path, endpoint, interval } of [
  { path: "/", endpoint: "/overview", interval: 30_000 },
  { path: "/opslag", endpoint: "/torrent/storage", interval: 60_000 },
  { path: "/nzb-indexers", endpoint: "/indexers", interval: 60_000 },
  { path: "/torrent-indexers", endpoint: "/indexers", interval: 60_000 },
]) {
  test(`${path}: alle kerncijfers volgen de versheid van hun bron`, async ({ page }) => {
    let connected = true;
    await page.clock.install();
    await page.route(apiPath(endpoint), (route) => connected ? route.continue() : route.abort("internetdisconnected"));
    await page.goto(path);
    const kpis = page.locator("main .kpi-grid .kpi");
    const stale = kpis.locator('[data-status="stale"]');
    await expect(kpis).toHaveCount(4);
    await expect(kpis.locator(".skeleton")).toHaveCount(0);
    await expect(stale).toHaveCount(0);
    const subtitle = page.locator("main .page-header p");
    if (path === "/") await expect(subtitle).not.toContainText("Modus onbekend");

    connected = false;
    await page.clock.fastForward(interval);
    await expect(stale).toHaveCount(4);
    await expect(kpis.locator('.kpi-value [data-status="unknown"]')).toHaveCount(4);
    if (path === "/") await expect(subtitle).toContainText("Modus onbekend");

    connected = true;
    await page.clock.fastForward(interval);
    await expect(stale).toHaveCount(0);
    if (path === "/") await expect(subtitle).not.toContainText("Modus onbekend");
  });
}

test("Systeem onderscheidt een storing van uitval na een bevestigde meting en herstelt na opnieuw proberen", async ({ page }) => {
  let state: "healthy" | "error" | "offline" | "recovered" = "healthy";
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route(apiPath("/system/health"), (route) => {
    if (state === "offline") return route.abort("internetdisconnected");
    if (state === "error") return route.fulfill({ status: 500, json: ERROR });
    return route.fulfill({ json: state === "healthy" ? {
      status: "degraded",
      components: [{ name: "Proefcomponent", status: "healthy" }, { name: "Proefstoring", status: "offline" }],
    } : {
      status: "healthy",
      components: [{ name: "Herstelde proefcomponent", status: "healthy" }],
    } });
  });
  await page.goto("/systeem");
  const components = page.getByRole("region", { name: "Onderdelen" });
  const sidebarHealth = page.getByTestId("sidebar-health");
  await expect(components.getByText("Gezond", { exact: true })).toBeVisible();
  await expect(sidebarHealth.getByText(/Proefstoring/)).toBeVisible();

  state = "error";
  await components.getByRole("button", { name: "Verversen" }).click();
  const error = components.getByRole("alert");
  await expect(error.getByText("Ophalen is mislukt.")).toBeVisible();
  await expect(error.getByText(/HTTP 500/)).toBeVisible();
  await expect(components.getByText("Gezond", { exact: true })).toHaveCount(0);

  state = "offline";
  await error.getByRole("button", { name: "Opnieuw proberen" }).click();
  const offline = components.locator('[data-state="offline"]');
  await expect(offline.getByText("Backend niet bereikbaar")).toBeVisible();
  await expect(components.getByText("Gezond", { exact: true })).toHaveCount(0);
  await expect(sidebarHealth.getByText("Onbekend — MSO-core reageert niet")).toBeVisible();
  await expect(sidebarHealth.getByText(/Proefstoring/)).toHaveCount(0);

  state = "recovered";
  await offline.getByRole("button", { name: "Opnieuw proberen" }).click();
  await expect(components.getByText("Herstelde proefcomponent")).toBeVisible();
  await expect(sidebarHealth.getByText("Alles gezond")).toBeVisible();
});
