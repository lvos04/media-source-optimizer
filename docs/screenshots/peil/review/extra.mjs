// Peil-review MSO (stap 5) — aanvullende controles in Chromium-EMULATIE (geen echte apparaten):
//  - 200 %-zoom: een venster van 1280 px op 200 % = 640 CSS-px breed (deviceScaleFactor 2) → overloop per scherm
//  - forced colors (Chromium `forcedColors: "active"`, ONTWERPTAAL §12 A-18): stippelranden blijven gestippeld,
//    statusbadges houden een zichtbare rand, de focusring is er nog; plus afdrukken van drie schermen
//  - prefers-reduced-motion: geen animatie of transitie > 20 ms
// Uitkomst: extra.json en afdrukken in forced-colors/. Dit is emulatie: een echt Windows-contrastthema, een echte
// telefoon en een schermlezer zijn hiermee NIET gevalideerd.
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(process.argv[2] ?? HERE);
const BASE = process.env.MSO_E2E_BASE_URL ?? "http://127.0.0.1:48081";
const USER = process.env.MSO_E2E_USER ?? "";
const PASSWORD = process.env.MSO_E2E_PASSWORD ?? "";
if (!USER || !PASSWORD) throw new Error("MSO_E2E_USER en MSO_E2E_PASSWORD zijn verplicht");
const { chromium, request } = await import(process.env.PEIL_PLAYWRIGHT ?? "/home/transcode/truenas-v2/.lokaal/ontwerp-tools/node_modules/playwright/index.mjs");

const SCREENS = [
  ["overzicht", "/"], ["zoeken", "/zoeken"], ["usenet-providers", "/usenet-providers"], ["nzb-indexers", "/nzb-indexers"],
  ["torrent-indexers", "/torrent-indexers"], ["torrentclient", "/torrentclient"], ["downloads", "/downloads"],
  ["ontbrekend", "/ontbrekend"], ["mediaguard", "/mediaguard"], ["opslag", "/opslag"], ["kosten", "/kosten"],
  ["automatisering", "/automatisering"], ["voetafdruk", "/automatisering/voetafdruk"], ["logs", "/logs"],
  ["instellingen", "/instellingen"], ["systeem", "/systeem"],
];
const api = await request.newContext({ baseURL: BASE });
const r = await api.post("/api/v1/auth/login", { data: { username: USER, password: PASSWORD } });
if (r.status() !== 200) throw new Error(`inloggen mislukt: HTTP ${r.status()}`);
const state = await api.storageState();
const browser = await chromium.launch();
const res = { zoom200: {}, forced: {}, beweging: {} };
const settle = async (page) => { await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined); await page.waitForTimeout(700); };

for (const [slug, route] of SCREENS) {
  const ctx = await browser.newContext({ viewport: { width: 640, height: 400 }, deviceScaleFactor: 2, baseURL: BASE, storageState: state, locale: "nl-NL" });
  const page = await ctx.newPage(); await page.goto(route); await settle(page);
  res.zoom200[slug] = await page.evaluate(() => ({ overloop_px: Math.max(document.documentElement.scrollWidth, innerWidth) - 640, h1: document.querySelectorAll("h1").length }));
  await ctx.close();
}

mkdirSync(path.join(OUT, "forced-colors"), { recursive: true });
for (const [slug, route] of [["overzicht", "/"], ["downloads", "/downloads?tab=torrent"], ["kosten", "/kosten"], ["ontbrekend", "/ontbrekend"]]) {
  for (const width of [390, 1440]) {
    const ctx = await browser.newContext({ viewport: { width, height: 900 }, isMobile: width < 768, baseURL: BASE, storageState: state, locale: "nl-NL", forcedColors: "active" });
    const page = await ctx.newPage(); await page.goto(route); await settle(page);
    const m = await page.evaluate(() => {
      const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      const chips = [...document.querySelectorAll("[data-status]")].filter(vis);
      const zonderRand = chips.filter((c) => { const cs = getComputedStyle(c); return parseFloat(cs.borderTopWidth) === 0 || cs.borderTopStyle === "none"; }).map((c) => c.textContent.trim().slice(0, 30));
      const onbekend = [...document.querySelectorAll('[data-status="unknown"], [data-status="stale"]')].filter(vis);
      const gestippeld = onbekend.filter((c) => ["dashed", "dotted"].includes(getComputedStyle(c).borderTopStyle)).length;
      return { forced: matchMedia("(forced-colors: active)").matches, statusbadges: chips.length, zonder_rand: zonderRand, onbekend_of_verouderd: onbekend.length, gestippeld };
    });
    await page.keyboard.press("Tab"); await page.keyboard.press("Tab");
    m.focus = await page.evaluate(() => { const e = document.activeElement; const cs = getComputedStyle(e); return { el: e.tagName.toLowerCase(), outline: `${cs.outlineStyle} ${cs.outlineWidth}` }; });
    await page.screenshot({ path: path.join(OUT, "forced-colors", `${slug}-${width}.png`), animations: "disabled" });
    res.forced[`${slug}-${width}`] = m;
    await ctx.close();
  }
}

for (const [slug, route] of [["overzicht", "/"], ["downloads", "/downloads"], ["automatisering", "/automatisering"]]) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, baseURL: BASE, storageState: state, reducedMotion: "reduce" });
  const page = await ctx.newPage(); await page.goto(route); await settle(page);
  await page.locator("nav.bottomnav button").click(); await page.waitForTimeout(300); // Meer-sheet open: ook de sheet zelf
  res.beweging[slug] = await page.evaluate(() => {
    const o = new Set();
    for (const el of document.querySelectorAll("*")) {
      const cs = getComputedStyle(el);
      if (cs.animationName !== "none" && Math.max(...cs.animationDuration.split(",").map(parseFloat)) > 0.02) o.add(`animatie ${cs.animationName}`);
      if (Math.max(...cs.transitionDuration.split(",").map(parseFloat)) > 0.02) o.add(`transitie ${cs.transitionDuration} (${el.tagName.toLowerCase()}.${String(el.className).split(" ")[0]})`);
    }
    return [...o];
  });
  await ctx.close();
}
await browser.close();
writeFileSync(path.join(OUT, "extra.json"), `${JSON.stringify(res, null, 1)}\n`);
const zoomFout = Object.entries(res.zoom200).filter(([, v]) => v.overloop_px > 1);
console.log(`200 %-zoom: ${Object.keys(res.zoom200).length} schermen, overloop bij ${zoomFout.length}`, zoomFout);
console.log("forced colors:", JSON.stringify(res.forced));
console.log("beweging bij reduce:", JSON.stringify(res.beweging));
