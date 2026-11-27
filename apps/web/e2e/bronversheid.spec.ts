import { expect, test, type Page } from "@playwright/test";

const MODE = "/system/mode";
const USENET = "/usenet/autopilot";
const AUTOMATION = "/automation/status";
const SUMMARY = "/overview/autopilot";
const MG = "/mediaguard/status";

function bron(page: Page, path: string, value: () => object | "offline" | "storing") {
  return page.route((url) => url.pathname === `/api/v1${path}`, (route) => {
    const data = value();
    if (data === "offline") return route.abort("internetdisconnected");
    if (data === "storing") return route.fulfill({ status: 500, json: { detail: "Storing in de proefbron" } });
    return route.fulfill({ json: data });
  });
}

function usenet(autopilot: boolean) {
  return {
    autopilot, auto_disable_after_h: 4, test_interval_h: 6, servers: {},
    retry: { enabled: true, counts: {}, quota: { hour_used: 0, hour_max: 4, day_used: 0, day_max: 8 }, items: [] },
  };
}

function automation(actief = true) {
  return {
    mode: "autopilot", paused: false, autopilot: actief, automation_allowed: actief, max_per_hour: 4, max_age_h: 12,
    auto_rollback: true, applied_last_hour: 0, risk_explanation: {}, log: [], jobs: [], evaluators: [],
    areas: [{
      area: "usenet", label: "Usenet", auto_apply_safe: false, setting_key: "proef", pending_safe_changes: 0,
      effective: actief, via_autopilot: actief,
    }],
  };
}

test.beforeEach(async ({ page }) => {
  await page.clock.install();
  await page.route("**/api/v1/**", (route) => route.fulfill({ status: 404, json: { detail: "Geen proefbron" } }));
  await bron(page, "/setup/status", () => ({ setup_required: false, admin_exists: true }));
  await bron(page, "/auth/me", () => ({ user: { username: "proef", role: "admin" } }));
  await bron(page, "/settings", () => ({ definitions: [], values: {} }));
  await bron(page, "/system/health", () => ({ status: "healthy", components: [] }));
  await bron(page, MODE, () => ({ mode: "autopilot", paused: false }));
});

test("Usenet volgt de eigen Autopilot-bron bij afzonderlijke uitval en herstel", async ({ page }) => {
  let modeOffline = false;
  let usenetOffline = false;
  let active = true;
  await bron(page, MODE, () => modeOffline ? "offline" : { mode: "autopilot", paused: false });
  await bron(page, USENET, () => usenetOffline ? "offline" : usenet(active));
  await page.goto("/usenet-providers");
  const card = page.getByRole("region", { name: "Autopilot: servers en mislukte downloads" });
  await expect(card.getByText(/^Autopilot aan:/)).toBeVisible();

  modeOffline = true;
  const poll = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/v1${USENET}`);
  await page.clock.fastForward(60_000);
  await poll;
  await expect(page.getByTestId("mode-chip")).toHaveAttribute("data-mode", "unknown");
  await expect(card.getByText(/^Autopilot aan:/)).toBeVisible();
  await expect(card.getByText(/^Autopilot staat uit/)).toHaveCount(0);

  modeOffline = false;
  usenetOffline = true;
  await page.clock.fastForward(60_000);
  await expect(card.getByText(/laatst bekend/)).toBeVisible();
  await expect(card.getByText(/^Autopilot aan:/)).toHaveCount(0);
  await expect(card.getByText("Modus onbekend")).toBeVisible();
  await expect(page.getByTestId("mode-chip")).toHaveAttribute("data-mode", "autopilot");

  usenetOffline = false;
  active = false;
  await page.clock.fastForward(60_000);
  await expect(card.getByText(/^Autopilot staat uit/)).toBeVisible();
  await expect(card.getByText(/laatst bekend/)).toHaveCount(0);
});

test("Instellingen wist de zichtbare en toegankelijke modusselectie bij uitval", async ({ page }) => {
  let offline = false;
  let actief = true;
  await bron(page, MODE, () => offline ? "offline" : { mode: "autopilot", paused: false });
  await bron(page, AUTOMATION, () => automation(actief));
  await page.goto("/instellingen?tab=systeem");
  const group = page.getByRole("group", { name: "Bedrijfsmodus kiezen" });
  const selection = group.getByRole("button", { pressed: true });
  const uitvoering = page.getByTestId("mode-auto-apply");
  await expect(selection).toHaveText("Autopilot");

  offline = true;
  await page.clock.fastForward(60_000);
  await expect(page.locator("#modus").getByText("Modus onbekend")).toBeVisible();
  await expect(selection).toHaveCount(0);
  await expect(group.getByRole("button", { disabled: true })).toHaveCount(3);
  await expect(uitvoering.getByText(/Usenet: aan via Autopilot/)).toBeVisible();

  actief = false;
  await page.clock.fastForward(60_000);
  await expect(uitvoering.getByText("Usenet: Uit", { exact: true })).toBeVisible();
  await expect(uitvoering.getByText(/aan via Autopilot/)).toHaveCount(0);
  await expect(selection).toHaveCount(0);

  offline = false;
  actief = true;
  await page.clock.fastForward(60_000);
  await expect(selection).toHaveText("Autopilot");
  await expect(selection).toBeEnabled();
  await expect(uitvoering.getByText(/Usenet: aan via Autopilot/)).toBeVisible();
});

test("de schil markeert een laatst bekende pauze en herstelt na bevestiging", async ({ page }) => {
  let offline = false;
  let paused = true;
  await bron(page, MODE, () => offline ? "offline" : { mode: "autopilot", paused });
  await page.goto("/systeem");
  const banner = page.getByTestId("pause-banner");
  const button = page.getByTestId("pause-button");
  await expect(banner.getByRole("button", { name: "Hervatten" })).toBeVisible();
  await expect(button).toHaveAttribute("aria-pressed", "true");

  offline = true;
  paused = false;
  await page.clock.fastForward(30_000);
  await expect(banner.getByText(/laatst bekend/)).toBeVisible();
  await expect(banner.getByRole("button")).toHaveCount(0);
  await expect(button).toBeDisabled();
  await expect(button).not.toHaveAttribute("aria-pressed");

  offline = false;
  await page.clock.fastForward(30_000);
  await expect(banner).toHaveCount(0);
  await expect(button).toHaveAttribute("aria-pressed", "false");
  await expect(button).toBeEnabled();
});

test("Systeem toont een ontvangen HTTP 500 op Onderdelen als storing", async ({ page }) => {
  await bron(page, "/system/health", () => "storing");
  await page.goto("/systeem");
  const card = page.getByRole("region", { name: "Onderdelen" });
  await expect(card.getByRole("alert")).toContainText("Storing in de proefbron");
  await expect(card.locator('[data-state="offline"]')).toHaveCount(0);
});

test("Kosten toont een onafhankelijk maandbedrag voor en na uitval", async ({ page }) => {
  let offline = false;
  let monthly = 1715;
  await bron(page, "/costs/summary", () => offline ? "offline" : {
    total: { monthly_cents: monthly, currency: "EUR", services: 1, unknown: [], by_kind: { other: monthly } },
    renewals: [], reminder_days: 30, disclaimers: [],
  });
  await bron(page, "/costs/services", () => ({
    items: [{ id: 1, label: "Proefdienst", kind: "other", billing: "monthly", amount_cents: 1715, currency: "EUR", active: true }],
    period_days: 30, price_missing: 0,
  }));
  await bron(page, "/costs/services/import", () => ({ created: 0, sab_providers_created: 0 }));
  await page.goto("/kosten");
  const kpis = page.locator("main .kpi-grid .kpi");
  const monthlyCard = kpis.filter({ hasText: "Totaal per maand" });
  const services = kpis.filter({ has: page.getByText("Diensten", { exact: true }) });
  await expect(monthlyCard).toContainText("17,15");
  await expect(services.locator(".kpi-value")).toHaveText("1");

  offline = true;
  monthly = 2250;
  const importButton = page.getByRole("button", { name: "Providers en indexers ophalen" });
  await importButton.click();
  await expect(monthlyCard).toContainText("Onbekend");
  await expect(monthlyCard).toContainText(/laatst bekend/);
  await expect(monthlyCard).not.toContainText("17,15");
  await expect(services.locator(".kpi-value")).toHaveText("1");

  offline = false;
  await importButton.click();
  await expect(monthlyCard).toContainText("22,50");
  await expect(monthlyCard.getByText(/laatst bekend/)).toHaveCount(0);
});

test("Logs markeert een lege auditlijst tot de bron weer antwoordt", async ({ page }) => {
  let offline = false;
  await bron(page, "/audit", () => offline ? "offline" : { items: [], next_before_id: null });
  await page.goto("/logs");
  const audit = page.getByRole("region", { name: "Audittrail" });
  await expect(audit.getByText("Geen auditregels gevonden")).toBeVisible();
  offline = true;
  await audit.getByRole("button", { name: "Verversen" }).click();
  await expect(audit.getByText(/laatst bekend/)).toBeVisible();
  offline = false;
  await audit.getByRole("button", { name: "Verversen" }).click();
  await expect(audit.getByText(/laatst bekend/)).toHaveCount(0);
  await expect(audit.getByText("Geen auditregels gevonden")).toBeVisible();
});

test("Automatisering gebruikt haar eigen stand en weigert de ronde bij bronuitval", async ({ page }) => {
  let offline = false;
  await bron(page, MODE, () => ({ mode: "read_only", paused: false }));
  await bron(page, AUTOMATION, () => offline ? "offline" : automation());
  await page.goto("/automatisering?tab=automatisch");
  const button = page.getByRole("button", { name: "Veilige ronde nu uitvoeren" });
  const status = page.getByRole("region", { name: "Stand", exact: true });
  await expect(page.getByTestId("mode-chip")).toHaveAttribute("data-mode", "read_only");
  await expect(status.getByText(/^Autopilot actief:/)).toBeVisible();
  await expect(button).toBeEnabled();

  offline = true;
  await page.clock.fastForward(60_000);
  await expect(status.getByText(/laatst bekend/)).toBeVisible();
  await expect(button).toBeDisabled();

  offline = false;
  await page.clock.fastForward(60_000);
  await expect(status.getByText(/^Autopilot actief:/)).toBeVisible();
  await expect(button).toBeEnabled();
});

test("Weekoverzicht gebruikt zijn eigen modus en markeert gemiste peilingen", async ({ page }) => {
  let offline = false;
  let actief = true;
  await bron(page, MODE, () => ({ mode: "read_only", paused: false }));
  await bron(page, SUMMARY, () => offline ? "offline" : {
    mode: "autopilot", paused: false, autopilot: actief, automation_allowed: actief, actions_total: 0, by_day: [], categories: [],
    syncs: { failing_now: [], state: "idle", link: "/systeem" }, waiting: { decisions: 0 },
  });
  await page.goto("/automatisering?tab=samenvatting");
  const card = page.getByRole("region", { name: "Weekoverzicht" });
  await expect(card.getByText("Autopilot actief")).toBeVisible();
  actief = false;
  await page.clock.fastForward(120_000);
  await expect(card.getByText("Autopilot staat stil")).toBeVisible();
  await expect(card.getByText("Autopilot actief")).toHaveCount(0);
  actief = true;
  offline = true;
  await page.clock.fastForward(120_000);
  await expect(card.getByText("Modus onbekend")).toBeVisible();
  await expect(card.getByText(/laatst bekend/)).toBeVisible();
  offline = false;
  await page.clock.fastForward(120_000);
  await expect(card.getByText("Autopilot actief")).toBeVisible();
});

test("MediaGuard selecteert alleen een bevestigde authenticatiemethode", async ({ page }) => {
  let offline = true;
  let method = "apikey";
  await bron(page, MG, () => offline ? "offline" : {
    mode: "connected", configured: true, auth_method: method, warnings: [],
    compat: { reference: { repo: "proefbron", commit: "proef" }, routes: [], never_used: [] }, search: { available: true },
  });
  await page.goto("/mediaguard");
  const group = page.getByRole("group", { name: "Authenticatiemethode" });
  const selected = group.getByRole("button", { pressed: true });
  await expect(group.getByRole("button", { disabled: true })).toHaveCount(3);
  await expect(selected).toHaveCount(0);
  offline = false;
  await page.clock.fastForward(60_000);
  await expect(selected).toHaveText("API-sleutel");

  offline = true;
  await page.clock.fastForward(60_000);
  await expect(group.getByRole("button", { disabled: true })).toHaveCount(3);
  await expect(selected).toHaveCount(0);

  offline = false;
  method = "user";
  await page.clock.fastForward(60_000);
  await expect(selected).toHaveText("Eigen gebruiker");
});
