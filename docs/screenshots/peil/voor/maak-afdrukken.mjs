// Peil-migratie MSO — schermafdrukken en nulmeting van de HUIDIGE web-UI ("voor"), tegen een draaiende teststack.
//
// Alleen-lezen hulpscript voor docs/ontwerp/PEIL_MIGRATIE.md; het hoort niet bij de app en wordt niet gebouwd.
// Het verandert niets aan de stack behalve wat een kijker ook doet (inloggen, pagina's openen, dialogen openen en
// annuleren). Toestanden die de testdata niet vanzelf heeft (fout, offline, laden, niet ingesteld, gepauzeerd,
// beheermodus, eerste start) worden nagebootst door API-antwoorden in de browser te onderscheppen; in metingen.json
// staat per afdruk welke onderschepping is gebruikt ("nagebootst").
//
// Gebruik (stack voorbereid zoals `make e2e`: MSO_STACK_UP_ONLY=1 MSO_STACK_E2E_PREPARE=1 tests/stack/run.sh):
//   MSO_E2E_BASE_URL=http://127.0.0.1:48081 MSO_E2E_USER=stackadmin MSO_E2E_PASSWORD=… \
//   PEIL_PLAYWRIGHT=/pad/naar/node_modules/playwright/index.mjs PEIL_AXE=/pad/naar/axe-core/axe.min.js \
//   node docs/screenshots/peil/voor/maak-afdrukken.mjs [schermen|tabs|toestanden|alles]
// Wachtwoorden komen alleen uit de omgeving en worden nergens weggeschreven.
import { mkdirSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.MSO_E2E_BASE_URL ?? "http://127.0.0.1:48081";
const USER = process.env.MSO_E2E_USER ?? "";
const PASSWORD = process.env.MSO_E2E_PASSWORD ?? "";
const AXE = process.env.PEIL_AXE ?? "";
const WHAT = process.argv[2] ?? "alles";
const ONLY = process.env.PEIL_ALLEEN ? new RegExp(process.env.PEIL_ALLEEN) : null; // optioneel: alleen slugs die hierop passen
if (!USER || !PASSWORD) throw new Error("MSO_E2E_USER en MSO_E2E_PASSWORD zijn verplicht");
const { chromium, request } = await import(process.env.PEIL_PLAYWRIGHT ?? "playwright");

const THEMES = [["licht", "light"], ["donker", "dark"]];
const ALL_WIDTHS = [360, 390, 768, 1440];
const PAIR_WIDTHS = [390, 1440];
const DEVICE = {
  360: { viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true },
  390: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
  768: { viewport: { width: 768, height: 1024 }, isMobile: false, hasTouch: true },
  1440: { viewport: { width: 1440, height: 900 }, isMobile: false, hasTouch: false },
};

// dezelfde 16 routes als apps/web/e2e/screens.spec.ts (+ inloggen)
const SCREENS = [
  ["overzicht", "/"], ["zoeken", "/zoeken"], ["usenet-providers", "/usenet-providers"], ["nzb-indexers", "/nzb-indexers"],
  ["torrent-indexers", "/torrent-indexers"], ["torrentclient", "/torrentclient"], ["downloads", "/downloads"],
  ["ontbrekend", "/ontbrekend"], ["mediaguard", "/mediaguard"], ["opslag", "/opslag"], ["kosten", "/kosten"],
  ["automatisering", "/automatisering"], ["voetafdruk", "/automatisering/voetafdruk"], ["logs", "/logs"],
  ["instellingen", "/instellingen"], ["systeem", "/systeem"],
];
const TABS = [
  ...["wijzigingen", "controle", "veiligheid", "samenvatting", "automatisch", "ai"].map((t) => [`automatisering-${t}`, `/automatisering?tab=${t}`]),
  ["downloads-torrent", "/downloads?tab=torrent"],
  ...["verbindingen", "gebruikers", "usenet", "torrent", "automatisering"].map((t) => [`instellingen-${t}`, `/instellingen?tab=${t}`]),
];

const json = (status, body) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const ERR500 = json(500, { detail: "Interne fout; zie logboek met correlation-id", code: "internal", correlation_id: "3f9c2a7d41b8" });
const NOT_CONFIGURED = json(409, { detail: "SABnzbd is niet ingesteld (Instellingen → Verbindingen).", code: "connector_not_configured", correlation_id: "8d1e0b6c5a24" });

// toestanden: [slug, route, breedtes, opties]
const STATES = [
  ["inloggen", "/login", PAIR_WIDTHS, { anonymous: true }],
  ["eerste-start", "/setup", PAIR_WIDTHS, { anonymous: true, nagebootst: "GET /setup/status → setup_required, geen beheerder",
    routes: [["**/api/v1/setup/status", json(200, { setup_required: true, admin_exists: false })]] }],
  ["niet-gevonden", "/bestaat-niet", PAIR_WIDTHS, {}],
  ["laden", "/", PAIR_WIDTHS, { nagebootst: "GET /overview en /overview/goals 20 s vertraagd", delay: ["**/api/v1/overview**"], noSettle: true }],
  ["fout", "/kosten", PAIR_WIDTHS, { nagebootst: "GET /costs/* → 500 (echte foutvorm van de core)", routes: [["**/api/v1/costs/**", ERR500]] }],
  ["niet-ingesteld", "/downloads", PAIR_WIDTHS, { nagebootst: "GET /usenet/sab/* → 409 connector_not_configured", routes: [["**/api/v1/usenet/sab/**", NOT_CONFIGURED]] }],
  ["offline", "/", PAIR_WIDTHS, { nagebootst: "GET /auth/me → 503 (core onbereikbaar)", routes: [["**/api/v1/auth/me", json(503, { detail: "Service Unavailable", code: "error" })]], noSettle: true }],
  ["gepauzeerd", "/", PAIR_WIDTHS, { nagebootst: "GET /system/mode → autopilot, gepauzeerd",
    routes: [["**/api/v1/system/mode", json(200, { mode: "autopilot", paused: true, autopilot: false, automation_allowed: false })]] }],
  ["beheermodus", "/", PAIR_WIDTHS, { nagebootst: "GET /system/mode → manage",
    routes: [["**/api/v1/system/mode", json(200, { mode: "manage", paused: false, autopilot: false, automation_allowed: true })]] }],
  ["verouderd", "/downloads?tab=torrent", PAIR_WIDTHS, { nagebootst: "eerst echte data, daarna GET /torrent/torrents → 503 (verversen elke 30 s mislukt)", stale: /\/api\/v1\/torrent\/torrents(\?|$)/, staleMark: true }],
  ["verouderd-leeg", "/downloads", PAIR_WIDTHS, { nagebootst: "lege SABnzbd-wachtrij, daarna GET /usenet/sab/queue → 503 (verversen elke 15 s mislukt); de lege lijst toont dan GEEN verouderd-markering", stale: "**/api/v1/usenet/sab/queue**", staleWait: 20_000 }],
  ["verouderd-overzicht", "/", PAIR_WIDTHS, { nagebootst: "eerst echte data, daarna GET /overview → 503 (verversen elke 30 s mislukt); Overzicht toont dan GEEN verouderd-markering", stale: "**/api/v1/overview", staleWait: 35_000 }],
  ["zoekresultaten", "/zoeken?q=Expanse", PAIR_WIDTHS, {}],
  ["vergelijken", "/zoeken?q=Expanse", PAIR_WIDTHS, { action: "compare", viewportOnly: true }],
  ["pauze-dialoog", "/automatisering", PAIR_WIDTHS, { action: "pause", viewportOnly: true }],
  ["terugzet-dialoog", "/systeem", PAIR_WIDTHS, { action: "restore", viewportOnly: true }],
  ["sneltoetsen", "/", [1440], { action: "help", viewportOnly: true }],
  ["profielmenu", "/", PAIR_WIDTHS, { action: "profile", viewportOnly: true }],
  ["meer-lade", "/", [360, 390], { action: "more", viewportOnly: true }],
];

const results = existsSync(path.join(HERE, "metingen.json")) ? JSON.parse(readFileSync(path.join(HERE, "metingen.json"), "utf8")) : {};

async function storageState() {
  const ctx = await request.newContext({ baseURL: BASE });
  const r = await ctx.post("/api/v1/auth/login", { data: { username: USER, password: PASSWORD } });
  if (r.status() !== 200) throw new Error(`inloggen mislukt: HTTP ${r.status()}`);
  const state = await ctx.storageState();
  await ctx.dispose();
  return state;
}

async function settle(page) {
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
  const end = Date.now() + 15_000;
  while (Date.now() < end && (await page.locator(".skeleton").count()) > 0) await page.waitForTimeout(250);
  await page.waitForTimeout(400);
}

/** Nulmeting in de pagina: doelen < 44 px, tekst < 14 px, overloop, toon-gebruik, inline styles, koppen. */
function meetInPagina(apparaatBreedte) {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none" && Number(cs.opacity) > 0;
  };
  const describe = (el) => {
    const name = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("title") || "").trim().replace(/\s+/g, " ").slice(0, 40);
    const cls = typeof el.className === "string" ? el.className.split(/\s+/).filter(Boolean).slice(0, 2).join(".") : "";
    return `${el.tagName.toLowerCase()}${cls ? "." + cls : ""} "${name}"`;
  };
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=switch], [role=option], [tabindex]:not([tabindex="-1"])';
  const klein = [];
  let doelen = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (!vis(el) || el.closest(".sr-only") || el.classList.contains("skip-link") || el.tagName === "MAIN") continue;
    const cs = getComputedStyle(el);
    if (el.tagName === "A" && cs.display === "inline") continue; // link in lopende tekst: uitzondering WCAG 2.5.8
    let target = el;
    if ((el.type === "checkbox" || el.type === "radio") && el.closest("label")) target = el.closest("label");
    const r = target.getBoundingClientRect();
    doelen++;
    if (r.width < 43.5 || r.height < 43.5) klein.push(`${describe(el)} ${Math.round(r.width)}×${Math.round(r.height)}`);
  }
  const hist = {};
  let kleineTekst = 0;
  let minPx = Infinity;
  const kleineVoorbeelden = [];
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.textContent.trim()) continue;
    const el = node.parentElement;
    if (!el || seen.has(el) || !vis(el) || el.closest(".sr-only, script, style")) continue;
    seen.add(el);
    const px = parseFloat(getComputedStyle(el).fontSize);
    if (px < 13.95) {
      kleineTekst++;
      const k = String(Math.round(px * 10) / 10);
      hist[k] = (hist[k] ?? 0) + 1;
      minPx = Math.min(minPx, px);
      if (kleineVoorbeelden.length < 6) kleineVoorbeelden.push(`${describe(el)} ${px}px`);
    }
  }
  const count = (s) => document.querySelectorAll(s).length;
  return {
    // Let op: met isMobile (360/390) rekt Chromium het layout-viewport op tot de breedste inhoud, zoals een telefoon
    // die uitzoomt. scrollWidth − innerWidth is dan altijd 0; daarom ook de overloop t.o.v. de apparaatbreedte.
    overloop_px: document.documentElement.scrollWidth - window.innerWidth,
    layout_breedte_px: Math.max(document.documentElement.scrollWidth, window.innerWidth),
    overloop_apparaat_px: Math.max(document.documentElement.scrollWidth, window.innerWidth) - apparaatBreedte,
    pagina_hoogte: document.documentElement.scrollHeight,
    h1: count("h1"),
    doelen,
    doelen_kleiner_dan_44: klein.length,
    voorbeelden_doelen: klein.slice(0, 6),
    tekst_kleiner_dan_14: kleineTekst,
    kleinste_tekst_px: Number.isFinite(minPx) ? minPx : null,
    tekst_histogram_px: hist,
    voorbeelden_tekst: kleineVoorbeelden,
    inline_styles: count("[style]"),
    tonen: {
      chip_success: count(".chip--success"), chip_warning: count(".chip--warning"), chip_danger: count(".chip--danger"),
      chip_info: count(".chip--info"), chip_accent: count(".chip--accent"), chip_neutraal: count(".chip:not([class*='chip--success']):not([class*='chip--warning']):not([class*='chip--danger']):not([class*='chip--info']):not([class*='chip--accent'])"),
      kpi_success: count(".kpi--success"), kpi_warning: count(".kpi--warning"), kpi_danger: count(".kpi--danger"),
      dot_success: count(".dot--success"), t_success: count(".t-success"), stale_mark: count(".stale-mark"),
    },
    grafieken: { svg_niet_icoon: count("svg:not(.lucide):not(.ico)"), role_img: count("[role=img]"), stackbar: count(".stackbar"), progress: count(".progress") },
    toestanden: [...document.querySelectorAll("[data-state]")].map((e) => e.getAttribute("data-state")),
  };
}

async function axe(page) {
  if (!AXE) return null;
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }, resultTypes: ["violations"] });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length }));
  });
}

async function act(page, action) {
  if (action === "pause") {
    const b = page.getByTestId("pause-button");
    await b.waitFor({ state: "visible" });
    await page.waitForFunction(() => !document.querySelector('[data-testid="pause-button"]')?.hasAttribute("disabled"), null, { timeout: 15_000 });
    await b.click();
    await page.getByRole("alertdialog").waitFor();
  } else if (action === "restore") {
    const b = page.getByRole("button", { name: "Terugzetten" }).first();
    await b.waitFor({ state: "visible", timeout: 15_000 });
    await b.click();
    await page.getByRole("alertdialog").waitFor();
  } else if (action === "help") {
    await page.locator("body").click({ position: { x: 5, y: 5 } }).catch(() => undefined);
    await page.keyboard.press("?");
    await page.getByRole("dialog").waitFor();
  } else if (action === "profile") {
    await page.locator("button.avatar").click();
    await page.locator(".popover[role=dialog]").waitFor();
  } else if (action === "more") {
    await page.locator("nav.bottomnav button").click();
    await page.locator(".drawer[role=dialog]").waitFor();
  } else if (action === "compare") {
    const boxes = page.getByRole("checkbox", { name: /vergelijking/i });
    await boxes.first().waitFor({ timeout: 15_000 });
    await boxes.nth(0).check();
    await boxes.nth(1).check();
    await page.getByRole("button", { name: /Vergelijk \(2\)/ }).click();
    await page.getByRole("dialog").waitFor();
  }
  await page.waitForTimeout(400);
}

async function shoot(browser, state, group, slug, route, theme, width, opts = {}) {
  const ctx = await browser.newContext({
    ...DEVICE[width], baseURL: BASE, deviceScaleFactor: 1, locale: "nl-NL", timezoneId: "Europe/Amsterdam",
    colorScheme: theme[1], storageState: opts.anonymous ? undefined : state,
  });
  await ctx.addInitScript((t) => { try { localStorage.setItem("mso.theme", t); } catch { /* geblokkeerd */ } }, theme[1]);
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  for (const [pattern, reply] of opts.routes ?? []) await page.route(pattern, (r) => r.fulfill(reply));
  for (const pattern of opts.delay ?? []) await page.route(pattern, async (r) => { await new Promise((res) => setTimeout(res, 20_000)); await r.continue().catch(() => undefined); });
  const file = path.join(HERE, group, `${slug}-${theme[0]}-${width}.png`);
  const key = `${group}/${slug}-${theme[0]}-${width}`;
  if (ONLY && !ONLY.test(key)) { await ctx.close(); return; }
  try {
    await page.goto(route, { waitUntil: "domcontentloaded" });
    if (opts.noSettle) await page.waitForTimeout(2_500); else await settle(page);
    if (opts.stale) {
      await page.route(opts.stale, (r) => r.fulfill(json(503, { detail: "Service Unavailable", code: "error" })));
      if (opts.staleMark) await page.waitForSelector(".stale-mark", { timeout: 45_000 });
      else await page.waitForTimeout(opts.staleWait ?? 35_000);
      await page.waitForTimeout(300);
    }
    if (opts.action) await act(page, opts.action);
    const meting = await page.evaluate(meetInPagina, width);
    const axeResult = await axe(page);
    await page.screenshot({ path: file, fullPage: !opts.viewportOnly, animations: "disabled" });
    results[key] = { route, thema: theme[0], breedte: width, nagebootst: opts.nagebootst ?? null, afdruk: opts.viewportOnly ? "viewport" : "volledige pagina", ...meting, axe: axeResult, js_fouten: errors };
    console.log(`ok   ${key}`);
  } catch (e) {
    results[key] = { route, thema: theme[0], breedte: width, fout: String(e).slice(0, 300) };
    console.log(`FOUT ${key}: ${String(e).split("\n")[0]}`);
  } finally {
    await ctx.close();
  }
}

const browser = await chromium.launch();
const state = await storageState();
for (const group of ["schermen", "tabs", "toestanden"]) mkdirSync(path.join(HERE, group), { recursive: true });
if (WHAT === "schermen" || WHAT === "alles") {
  for (const theme of THEMES) for (const w of ALL_WIDTHS) for (const [slug, route] of SCREENS) await shoot(browser, state, "schermen", slug, route, theme, w);
}
if (WHAT === "tabs" || WHAT === "alles") {
  for (const theme of THEMES) for (const w of PAIR_WIDTHS) for (const [slug, route] of TABS) await shoot(browser, state, "tabs", slug, route, theme, w);
}
if (WHAT === "toestanden" || WHAT === "alles") {
  for (const [slug, route, widths, opts] of STATES) for (const theme of THEMES) for (const w of widths) await shoot(browser, state, "toestanden", slug, route, theme, w, opts);
}
await browser.close();
const sorted = Object.fromEntries(Object.entries(results).sort(([a], [b]) => a.localeCompare(b)));
writeFileSync(path.join(HERE, "metingen.json"), `${JSON.stringify(sorted, null, 1)}\n`);
console.log(`metingen: ${Object.keys(sorted).length} afdrukken`);
