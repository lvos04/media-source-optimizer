// Peil-review MSO (stap 5, onafhankelijke ontwerpreview) — meet de ECHTE app tegen Peil 1.1.0.
//
// Los van de "na"-scripts van de bouwstap geschreven, naar het voorbeeld van de Peil-controle
// (.lokaal/ontwerptaal/definitief/bouw/controle.cjs). Per scherm × breedte × thema:
//   - axe-core 4.14, tags wcag2a/2aa/21a/21aa/22aa, ALLE impacts (niet alleen serious/critical)
//   - contrast in de gerenderde DOM (achtergrondlagen, opacity, kleuren in background-image): tekst ≥ 4,5:1
//     (groot ≥ 3:1), plus de Peil-doelwaarde ≥ 7:1 als extra telling; veldranden, stippelranden, schakelaars en
//     selectie-indicatoren ≥ 3:1
//   - horizontale overloop t.o.v. de apparaatbreedte + welke elementen buiten beeld steken
//   - aanraakdoelen < 44 px (inline links in lopende tekst uitgezonderd; checkbox/radio: het label telt)
//   - tekst < 14 px, ellips op inhoud, één h1, kopsprongen, dubbele id's, kapotte aria-verwijzingen
//   - Peil-semantiek: groen (healthy-tokens of elke groenachtige tint) buiten een gemeten "Gezond"/grafiek;
//     statusbadges zonder icoon of woord; verboden woorden uit de Peil-woordenlijst (§11.1)
// Toetsenbord (390/1440): volledige Tab-doorloop; ring aanwezig, ≥ 3 px, contrast ≥ 3:1, niet bedekt.
//
// Alleen-lezen hulpscript (hoort niet bij de app). Wachtwoorden alleen uit de omgeving.
//   MSO_E2E_BASE_URL=http://127.0.0.1:48081 MSO_E2E_USER=stackadmin MSO_E2E_PASSWORD=… \
//   node docs/screenshots/peil/review/review.mjs [schermen|tabs|toetsenbord|toestanden|beheer|alles] [uitmap]
// Standaard: playwright 1.65 en axe-core 4.14 uit <WERKMAP>/.lokaal, waarbij <WERKMAP> de map is waarin deze repo
// staat (PEIL_PLAYWRIGHT / PEIL_AXE overschrijven dat).
import { mkdirSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WERKMAP = path.resolve(HERE, "../../../../.."); // <WERKMAP>: de map boven de repo
const BASE = process.env.MSO_E2E_BASE_URL ?? "http://127.0.0.1:48081";
const USER = process.env.MSO_E2E_USER ?? "";
const PASSWORD = process.env.MSO_E2E_PASSWORD ?? "";
const AXE = process.env.PEIL_AXE ?? path.join(WERKMAP, ".lokaal/ontwerptaal/.review-tools/node_modules/axe-core/axe.min.js");
const WHAT = process.argv[2] ?? "alles";
const OUT = path.resolve(process.argv[3] ?? HERE);
const ONLY = process.env.PEIL_ALLEEN ? new RegExp(process.env.PEIL_ALLEEN) : null;
const SHOTS = process.env.PEIL_GEEN_AFDRUK ? false : true;
if (!USER || !PASSWORD) throw new Error("MSO_E2E_USER en MSO_E2E_PASSWORD zijn verplicht");
const { chromium, request } = await import(process.env.PEIL_PLAYWRIGHT ?? path.join(WERKMAP, ".lokaal/ontwerp-tools/node_modules/playwright/index.mjs"));

const THEMES = [["licht", "light"], ["donker", "dark"]];
const WIDTHS = [320, 360, 390, 768, 1024, 1440];
const SHOT_WIDTHS = new Set([360, 390, 768, 1440]); // 320 en 1024: alleen meten
const DEVICE = (w) => ({ viewport: { width: w, height: w < 768 ? (w === 390 ? 844 : 800) : w === 768 ? 1024 : 900 }, isMobile: w < 768, hasTouch: w < 1024 });

const SCREENS = [
  ["overzicht", "/"], ["zoeken", "/zoeken"], ["usenet-providers", "/usenet-providers"], ["nzb-indexers", "/nzb-indexers"],
  ["torrent-indexers", "/torrent-indexers"], ["torrentclient", "/torrentclient"], ["downloads", "/downloads"],
  ["ontbrekend", "/ontbrekend"], ["mediaguard", "/mediaguard"], ["opslag", "/opslag"], ["kosten", "/kosten"],
  ["automatisering", "/automatisering"], ["voetafdruk", "/automatisering/voetafdruk"], ["logs", "/logs"],
  ["instellingen", "/instellingen"], ["systeem", "/systeem"],
];
// tabbladen van Automatisering, Downloads en Instellingen (eigen URL per tab)
const TABS = [
  ...["wijzigingen", "controle", "veiligheid", "samenvatting", "automatisch", "ai"].map((t) => [`automatisering-${t}`, `/automatisering?tab=${t}`]),
  ["downloads-torrent", "/downloads?tab=torrent"],
  ...["verbindingen", "gebruikers", "usenet", "torrent", "automatisering"].map((t) => [`instellingen-${t}`, `/instellingen?tab=${t}`]),
];
const json = (status, body) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const MANAGE = ["**/api/v1/system/mode", json(200, { mode: "manage", paused: false, autopilot: false, automation_allowed: true })];
const HUB = "http://hub.thuis.test:30080"; // gereserveerde .test-naam, geen echt adres
const STATES = [
  ["inloggen", "/login", { anonymous: true }],
  ["eerste-start", "/setup", { anonymous: true, routes: [["**/api/v1/setup/status", json(200, { setup_required: true, admin_exists: false })]] }],
  ["niet-gevonden", "/bestaat-niet", {}],
  ["laden", "/", { delay: ["**/api/v1/overview**"], noSettle: true }],
  ["fout", "/kosten", { routes: [["**/api/v1/costs/**", json(500, { detail: "Interne fout; zie logboek met correlation-id", code: "internal", correlation_id: "3f9c2a7d41b8" })]] }],
  ["niet-ingesteld", "/downloads", { routes: [["**/api/v1/usenet/sab/**", json(409, { detail: "SABnzbd is niet ingesteld (Instellingen → Verbindingen).", code: "connector_not_configured", correlation_id: "8d1e0b6c5a24" })]] }],
  ["offline", "/", { routes: [["**/api/v1/auth/me", json(503, { detail: "Service Unavailable", code: "error" })]], noSettle: true }],
  ["gepauzeerd", "/", { routes: [["**/api/v1/system/mode", json(200, { mode: "autopilot", paused: true, autopilot: false, automation_allowed: false })]] }],
  ["verouderd", "/downloads?tab=torrent", { stale: /\/api\/v1\/torrent\/torrents(\?|$)/, staleMark: true }],
  ["verouderd-overzicht", "/", { stale: "**/api/v1/overview", staleWait: 35_000 }],
  ["zoekresultaten", "/zoeken?q=Expanse", {}],
  ["vergelijken", "/zoeken?q=Expanse", { action: "compare" }],
  ["pauze-dialoog", "/automatisering", { action: "pause", routes: [MANAGE] }],
  ["terugzet-dialoog", "/systeem", { action: "restore", routes: [MANAGE] }],
  ["sneltoetsen", "/", { action: "help", widths: [1440] }],
  ["profielmenu", "/", { action: "profile" }],
  ["meer-lade", "/", { action: "more", widths: [390] }],
  ["hub-link", "/", { hub: HUB }],
  ["hub-link-meer", "/", { hub: HUB, action: "more", widths: [390] }],
  ["grafiek-kosten-tabel", "/kosten", { action: "tables" }],
  ["grafiek-ontbrekend-tabel", "/ontbrekend", { action: "tables" }],
  ["grafiek-usenet-tabel", "/usenet-providers", { action: "tables" }],
];

const resultsFile = path.join(OUT, "metingen.json");
const results = existsSync(resultsFile) ? JSON.parse(readFileSync(resultsFile, "utf8")) : {};

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
  await page.waitForTimeout(500);
}

/** Alles wat in de pagina gemeten wordt (één evaluate). */
function inPage(deviceWidth) {
  const cvs = document.createElement("canvas"); cvs.width = cvs.height = 1;
  const ctx = cvs.getContext("2d", { willReadFrequently: true });
  const cache = new Map();
  const rgba = (s) => { if (cache.has(s)) return cache.get(s); ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = "#000"; ctx.fillStyle = s; ctx.fillRect(0, 0, 1, 1); const d = ctx.getImageData(0, 0, 1, 1).data; const v = [d[0], d[1], d[2], d[3] / 255]; cache.set(s, v); return v; };
  const over = (t, b) => { const a = t[3] + b[3] * (1 - t[3]); if (a === 0) return [0, 0, 0, 0]; return [0, 1, 2].map((i) => (t[i] * t[3] + b[i] * b[3] * (1 - t[3])) / a).concat([a]); };
  const mix = (a, b, t) => [0, 1, 2].map((i) => a[i] * (1 - t) + b[i] * t).concat([1]);
  const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const hex = (c) => "#" + c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
  const oklch = (c) => { // sRGB → OKLab → LCh
    const [r, g, b] = c.slice(0, 3).map(lin);
    const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b), m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b), s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
    return { L, C: Math.hypot(A, B), h: ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360 };
  };
  const colorsIn = (img) => img.match(/(rgba?\([^)]*\)|oklch\([^)]*\)|oklab\([^)]*\)|color\([^)]*\)|#[0-9a-f]{3,8})/gi) || [];
  const chain = (el) => { const c = []; for (let e = el; e && e.nodeType === 1; e = e.parentElement) c.push(e); return c; };
  function paint(el, fg) {
    const ch = chain(el); const alts = [];
    function rec(k, backdrop) {
      const e = ch[k]; const cs = getComputedStyle(e);
      const own = rgba(cs.backgroundColor);
      if (own[3] >= 0.99) alts.length = 0; // een dekkende eigen achtergrond verbergt verlopen/arcering van voorouders
      const inside = over(own, backdrop);
      if (cs.backgroundImage && cs.backgroundImage !== "none") for (const s of colorsIn(cs.backgroundImage)) { const c = rgba(s); if (c[3] > 0.05) alts.push(over(c, inside)); }
      let Bin, Tin; if (k === 0) { Bin = inside; Tin = over(fg, inside); } else [Bin, Tin] = rec(k - 1, inside);
      const o = parseFloat(cs.opacity); if (o < 1) return [mix(backdrop, Bin, o), mix(backdrop, Tin, o)];
      return [Bin, Tin];
    }
    const [B, T] = rec(ch.length - 1, [255, 255, 255, 1]); return { B, T, alts };
  }
  const isSrOnly = (el) => { for (const e of chain(el)) { const cs = getComputedStyle(e); if (cs.clip === "rect(0px, 0px, 0px, 0px)" || (cs.position === "absolute" && parseFloat(cs.width) <= 1 && parseFloat(cs.height) <= 1 && cs.overflow === "hidden") || cs.clipPath === "inset(50%)") return true; } return false; };
  const visible = (el) => { const r = el.getBoundingClientRect(); if (r.width === 0 || r.height === 0) return false; const cs = getComputedStyle(el); if (cs.visibility === "hidden" || cs.display === "none") return false; return !isSrOnly(el); };
  const desc = (el) => { const id = el.id ? "#" + el.id : ""; const cls = (typeof el.className === "string" ? el.className : el.getAttribute("class") || "").split(/\s+/).filter(Boolean).slice(0, 3).join("."); const tid = el.getAttribute("data-testid"); return el.tagName.toLowerCase() + id + (cls ? "." + cls : "") + (tid ? `[data-testid=${tid}]` : ""); };
  const own = (el) => [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").replace(/\s+/g, " ").trim();
  const name = (el) => (el.getAttribute("aria-label") || el.innerText || el.textContent || el.getAttribute("title") || "").replace(/\s+/g, " ").trim().slice(0, 50);
  const inChart = (el) => !!el.closest('figure, [data-testid^="grafiek-"], svg:not(.lucide):not(.ico), .stackbar, .progress, [role=meter], [role=progressbar]');
  const inHealthy = (el) => !!el.closest('[data-status="healthy"], .chip--healthy, [data-tone="healthy"]');

  // ---- tekstcontrast
  const tekst = []; let minFont = 99;
  for (const el of document.querySelectorAll("body *")) {
    if (["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"].includes(el.tagName)) continue;
    const t = own(el); if (!t || !visible(el)) continue;
    const cs = getComputedStyle(el); const fgS = el instanceof SVGElement ? cs.fill : cs.color;
    if (!fgS || fgS === "none" || fgS.startsWith("url")) continue;
    const fg = rgba(fgS); const { B, T, alts } = paint(el, fg);
    const fs = parseFloat(cs.fontSize), fw = parseInt(cs.fontWeight, 10);
    const groot = fs >= 24 || (fs >= 18.66 && fw >= 700); const eis = groot ? 3 : 4.5;
    const r = ratio(T, B); let rAlt = r; for (const a of alts) rAlt = Math.min(rAlt, ratio(over(fg, a), a));
    if (fs < minFont) minFont = fs;
    tekst.push({ el: desc(el), t: t.slice(0, 50), fs, groot, eis, r: +r.toFixed(2), rAlt: +rAlt.toFixed(2), fg: hex(T), bg: hex(B), dis: !!el.closest(':disabled,[aria-disabled="true"]'), stale: !!el.closest(".stale-mark,[data-stale],.is-stale") });
  }
  for (const el of document.querySelectorAll("input[placeholder],textarea[placeholder]")) {
    if (!visible(el)) continue;
    const fg = rgba(getComputedStyle(el, "::placeholder").color); const { B, T } = paint(el, fg);
    tekst.push({ el: desc(el) + "::placeholder", t: el.placeholder.slice(0, 50), fs: parseFloat(getComputedStyle(el).fontSize), eis: 4.5, r: +ratio(T, B).toFixed(2), rAlt: +ratio(T, B).toFixed(2), fg: hex(T), bg: hex(B) });
  }
  const tekstFout = tekst.filter((x) => !x.dis && (x.r < x.eis || x.rAlt < x.eis));
  const tekstOnder7 = tekst.filter((x) => !x.dis && !x.stale && Math.min(x.r, x.rAlt) < 7);
  const tekstMin = tekst.filter((x) => !x.dis).reduce((m, x) => Math.min(m, x.r, x.rAlt), 99);

  // ---- UI-grenzen (WCAG 1.4.11): velden, schakelaars, checkbox/radio, stippelranden, actieve tab
  const ui = [];
  const parentBg = (el) => paint(el.parentElement, [0, 0, 0, 0]).B;
  for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]),select,textarea')) {
    if (!visible(el)) continue; const cs = getComputedStyle(el); const par = parentBg(el); const vlak = paint(el, [0, 0, 0, 0]).B;
    const rand = over(rgba(cs.borderBottomColor), vlak); const bw = parseFloat(cs.borderBottomWidth);
    let r = Math.max(bw > 0 ? ratio(rand, par) : 0, ratio(vlak, par)); let via = "";
    if (r < 3) { // veld zonder eigen rand: de omhullende rand (bijv. zoekveld met icoon) telt
      for (let p = el.parentElement, k = 0; p && k < 3; p = p.parentElement, k++) {
        const pcs = getComputedStyle(p); const pw = parseFloat(pcs.borderBottomWidth);
        if (pw > 0 && pcs.borderBottomStyle !== "none") { const pb = parentBg(p); const pr = ratio(over(rgba(pcs.borderBottomColor), pb), pb); if (pr > r) { r = pr; via = desc(p); } break; }
      }
    }
    ui.push({ soort: "veld", el: desc(el), r: +r.toFixed(2), kleur: hex(rand), bw, via });
  }
  for (const el of document.querySelectorAll('input[type=checkbox],input[type=radio]')) {
    if (!visible(el)) continue; const cs = getComputedStyle(el);
    if (cs.appearance === "none") { const par = parentBg(el); const rand = over(rgba(cs.borderTopColor), par); ui.push({ soort: el.type, el: desc(el), r: +ratio(rand, par).toFixed(2), kleur: hex(rand), bw: parseFloat(cs.borderTopWidth) }); }
    else ui.push({ soort: el.type + " (native)", el: desc(el), r: null, accent: cs.accentColor });
  }
  for (const el of document.querySelectorAll('[role=switch]')) {
    if (!visible(el)) continue; const cs = getComputedStyle(el); const par = parentBg(el); const vlak = paint(el, [0, 0, 0, 0]).B;
    const rand = over(rgba(cs.borderTopColor), par); let r = Math.max(parseFloat(cs.borderTopWidth) > 0 ? ratio(rand, par) : 0, ratio(vlak, par));
    let spoor = hex(vlak);
    for (const c of el.querySelectorAll("*")) { // spoor/knop van de schakelaar zitten in kindelementen
      if (!visible(c) || c.tagName === "svg" || c.closest("svg")) continue; const ccs = getComputedStyle(c); const cv = paint(c, [0, 0, 0, 0]).B;
      const cands = [ratio(cv, par)]; if (parseFloat(ccs.borderTopWidth) > 0 && ccs.borderTopStyle !== "none") cands.push(ratio(over(rgba(ccs.borderTopColor), par), par));
      const best = Math.max(...cands); if (best > r) { r = best; spoor = hex(cv); }
    }
    ui.push({ soort: "schakelaar", el: desc(el), r: +r.toFixed(2), kleur: spoor, aan: el.getAttribute("aria-checked") });
  }
  for (const el of document.querySelectorAll("body *")) {
    const cs = getComputedStyle(el); const side = ["Top", "Left", "Bottom"].find((s) => ["dashed", "dotted"].includes(cs["border" + s + "Style"]) && parseFloat(cs["border" + s + "Width"]) > 0);
    if (!side || !visible(el)) continue; const par = parentBg(el); const bc = over(rgba(cs["border" + side + "Color"]), par);
    ui.push({ soort: "stippelrand", el: desc(el), t: name(el).slice(0, 30), r: +ratio(bc, par).toFixed(2), kleur: hex(bc) });
  }
  for (const el of document.querySelectorAll('[role=tab][aria-selected=true]')) {
    if (!visible(el)) continue; const cs = getComputedStyle(el); const par = parentBg(el); const vlak = paint(el, [0, 0, 0, 0]).B;
    const kandidaten = [ratio(vlak, par)];
    for (const pseudo of ["::after", "::before"]) { const ps = getComputedStyle(el, pseudo); if (ps.content !== "none" && parseFloat(ps.height) >= 2) { const c = rgba(ps.backgroundColor); if (c[3] > 0.2) kandidaten.push(ratio(over(c, vlak), vlak)); } }
    for (const s of ["Bottom", "Top", "Left"]) if (parseFloat(cs["border" + s + "Width"]) >= 2) kandidaten.push(ratio(over(rgba(cs["border" + s + "Color"]), par), par));
    const sh = cs.boxShadow; if (sh && sh !== "none") for (const c of colorsIn(sh)) kandidaten.push(ratio(over(rgba(c), vlak), vlak));
    ui.push({ soort: "actieve tab", el: desc(el), t: name(el).slice(0, 30), r: +Math.max(...kandidaten).toFixed(2) });
  }
  const uiFout = ui.filter((x) => x.r !== null && x.r < 3);

  // ---- doelen < 44 px
  const doelen = []; let aantalDoelen = 0;
  for (const el of document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=tab],[role=link],[role=switch],[role=option],[tabindex]:not([tabindex="-1"])')) {
    if (!visible(el) || el.tagName === "MAIN" || el.classList.contains("skip-link")) continue;
    const cs = getComputedStyle(el);
    if (el.tagName === "A" && cs.display === "inline" && el.parentElement && own(el.parentElement).length > 0) continue; // link in lopende tekst
    let target = el; if ((el.type === "checkbox" || el.type === "radio") && el.closest("label")) target = el.closest("label");
    const r = target.getBoundingClientRect(); aantalDoelen++;
    if (r.width < 43.5 || r.height < 43.5) doelen.push(`${desc(el)} "${name(el).slice(0, 30)}" ${Math.round(r.width)}×${Math.round(r.height)}`);
  }

  // ---- tekst < 14 px en ellips
  const klein = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); const seen = new Set();
  while (walker.nextNode()) { const n = walker.currentNode; if (!n.textContent.trim()) continue; const el = n.parentElement; if (!el || seen.has(el) || !visible(el) || el.closest("script,style")) continue; seen.add(el); const px = parseFloat(getComputedStyle(el).fontSize); if (px < 13.95) klein.push(`${desc(el)} "${n.textContent.trim().slice(0, 30)}" ${px}px`); }
  const ellips = [];
  for (const el of document.querySelectorAll("body *")) { const cs = getComputedStyle(el); if (cs.textOverflow === "ellipsis" && visible(el) && el.scrollWidth > el.clientWidth + 1) ellips.push(`${desc(el)} "${(el.textContent || "").trim().slice(0, 40)}"`); }

  // ---- overloop t.o.v. de apparaatbreedte
  const se = document.scrollingElement;
  const layoutBreedte = Math.max(se.scrollWidth, window.innerWidth);
  const scrollable = (el) => { for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) { const cs = getComputedStyle(p); if (["auto", "scroll", "hidden", "clip"].includes(cs.overflowX)) return true; } return false; };
  const buiten = [];
  for (const el of document.querySelectorAll("body *")) { if (!visible(el)) continue; const r = el.getBoundingClientRect(); if (r.right > deviceWidth + 1 && !scrollable(el) && getComputedStyle(el).position !== "fixed") buiten.push(`${desc(el)} rechts=${Math.round(r.right)}`); }
  const scrollers = [];
  for (const el of document.querySelectorAll("body *")) { const cs = getComputedStyle(el); if (["auto", "scroll"].includes(cs.overflowX) && el.scrollWidth > el.clientWidth + 1 && visible(el)) scrollers.push(`${desc(el)} ${el.clientWidth}→${el.scrollWidth}`); }

  // ---- Peil-semantiek: groen alleen voor gemeten gezondheid
  const root = getComputedStyle(document.documentElement);
  const tok = (n) => { const v = root.getPropertyValue(n).trim(); return v ? hex(rgba(v)) : null; };
  const healthyTokens = new Set(["--healthy", "--healthy-bg", "--healthy-ink"].map(tok).filter(Boolean));
  const chartTokens = new Set(["--chart-1", "--chart-2", "--chart-3", "--chart-4", "--chart-5", "--chart-other"].map(tok).filter(Boolean));
  const groen = [];
  for (const el of document.querySelectorAll("body *")) {
    if (!visible(el) || inChart(el)) continue;
    const cs = getComputedStyle(el); const props = [["tekst", el instanceof SVGElement ? cs.stroke : cs.color], ["vlak", cs.backgroundColor]];
    if (parseFloat(cs.borderTopWidth) > 0 && cs.borderTopStyle !== "none") props.push(["rand", cs.borderTopColor]);
    if (el instanceof SVGElement && cs.fill && cs.fill !== "none") props.push(["vulling", cs.fill]);
    for (const [k, v] of props) {
      if (!v || v === "none" || v.startsWith("url")) continue; const c = rgba(v); if (c[3] < 0.2) continue;
      const h = hex(c); const o = oklch(c);
      if (chartTokens.has(h) && el.closest('[aria-hidden="true"]')) continue; // legendablokje van een grafiek (reekskleur, decoratief)
      const isToken = healthyTokens.has(h); const isGreenish = o.C > 0.045 && o.h > 120 && o.h < 175;
      if ((isToken || isGreenish) && !inHealthy(el)) groen.push(`${desc(el)} ${k} ${h}${isToken ? " (healthy-token)" : ""} "${name(el).slice(0, 30)}"`);
    }
  }
  const statusZonderIcoon = []; const statusZonderWoord = []; const statussen = {};
  for (const el of document.querySelectorAll("[data-status]")) {
    if (!visible(el)) continue; const s = el.getAttribute("data-status"); statussen[s] = (statussen[s] ?? 0) + 1;
    if (!el.querySelector("svg")) statusZonderIcoon.push(`${s}: "${name(el)}"`);
    if (!(el.textContent || "").trim() && !el.getAttribute("aria-label")) statusZonderWoord.push(`${s}: ${desc(el)}`);
  }
  const onbekendGroen = [...document.querySelectorAll('[data-status="unknown"], .stale-mark, [data-stale]')].filter((el) => visible(el)).filter((el) => { const c = rgba(getComputedStyle(el).color); const o = oklch(c); return o.C > 0.045 && o.h > 120 && o.h < 175; }).map((el) => `${desc(el)} "${name(el)}"`);
  const pauzeBijOnbekend = document.querySelectorAll('[data-status="unknown"] .lucide-pause, [data-status="unknown"] .lucide-circle-pause').length;
  const stippen = [...document.querySelectorAll(".dot")].filter(visible).filter((d) => ![d.parentElement, d.parentElement?.parentElement].some((p) => p && (p.innerText || "").trim())).map((d) => desc(d));

  // ---- woordenlijst (Peil §11.1/§11.2): zichtbare tekst
  const text = document.body.innerText;
  const VERBODEN = [
    [/\bVerstoord\b/g, "Verstoord → Beperkt"], [/\bVerminderd\b/g, "Verminderd → Beperkt"], [/\bIn orde\b/g, "In orde → Gezond"],
    [/\bActueel\b/g, "Actueel (versheid is nooit een badge)"], [/Laden(…|\.\.\.)/g, "Laden… → skelet/Onbekend"], [/\bOffline\b/g, "Offline (status) → Storing"],
    [/\bNiet gezond\b/g, "Niet gezond → Storing"], [/Gelukt!/g, "Gelukt! → Uitgevoerd en gecontroleerd"], [/\bAutomatisch veilig\b(?! toepassen)/g, "Automatisch veilig (risicowoord) → R1 · Omkeerbaar"],
    [/\bOK\b/g, "OK (knop)"], [/\bDoorgaan\b/g, "Doorgaan (knop)"], [/Weet je het zeker/gi, "Weet je het zeker?"], [/snooze/gi, "snoozen → uitstellen"],
    [/safe mode/gi, "safe mode → veilige modus"], [/blast radius/gi, "blast radius → reikwijdte"], [/\brunbook/gi, "runbook → stappenplan"],
    [/\bhealth ?check/gi, "healthcheck → gezondheidscontrole"], [/\ballowlist/gi, "allowlist → lijst met toegestane acties"], [/\bstale\b/gi, "stale → Verouderd"],
    [/\S!(?=\s|$)/g, "uitroepteken"], [/\b(Loading|Error|Cancel|Unknown|Healthy|Degraded|Retry|Close)\b/g, "Engels woord"],
  ];
  const woorden = [];
  for (const [re, uitleg] of VERBODEN) for (const m of text.matchAll(re)) woorden.push(`${uitleg}: "…${text.slice(Math.max(0, m.index - 40), m.index + 40).replace(/\s+/g, " ")}…"`);

  // ---- structuur
  const hs = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")].filter((e) => e.getClientRects().length || e.closest(".sr-only")).map((e) => +e.tagName[1]);
  const sprong = []; for (let k = 1; k < hs.length; k++) if (hs[k] > hs[k - 1] + 1) sprong.push(`h${hs[k - 1]}→h${hs[k]}`);
  const ids = {}; for (const e of document.querySelectorAll("[id]")) ids[e.id] = (ids[e.id] || 0) + 1;
  const kapot = []; for (const e of document.querySelectorAll("[aria-controls],[aria-describedby],[aria-labelledby]")) for (const a of ["aria-controls", "aria-describedby", "aria-labelledby"]) for (const id of (e.getAttribute(a) || "").split(/\s+/).filter(Boolean)) if (!document.getElementById(id)) kapot.push(`${a}=${id}`);

  return {
    overloop_px: layoutBreedte - deviceWidth, buiten_beeld: buiten.slice(0, 8), scrollgebieden: scrollers.slice(0, 8),
    tekst_aantal: tekst.length, tekst_fout: tekstFout.slice(0, 12), tekst_fout_aantal: tekstFout.length, tekst_min: +tekstMin.toFixed(2),
    tekst_onder_7_aantal: tekstOnder7.length, tekst_onder_7: tekstOnder7.slice(0, 6).map((x) => `${x.el} "${x.t}" ${Math.min(x.r, x.rAlt)} ${x.fg}/${x.bg}`),
    kleinste_letter_px: minFont, tekst_kleiner_dan_14: klein.length, voorbeelden_klein: klein.slice(0, 6), ellips: ellips.slice(0, 6),
    ui_aantal: ui.length, ui_fout: uiFout.slice(0, 12), ui_fout_aantal: uiFout.length, ui_min: ui.filter((x) => x.r !== null).reduce((m, x) => Math.min(m, x.r), 99),
    native_vinkjes: ui.filter((x) => x.r === null).length,
    doelen: aantalDoelen, doelen_kleiner_dan_44: doelen.length, voorbeelden_doelen: doelen.slice(0, 8),
    groen_buiten_gezond: groen.slice(0, 10), groen_buiten_gezond_aantal: groen.length, onbekend_of_verouderd_groen: onbekendGroen,
    status_zonder_icoon: statusZonderIcoon, status_zonder_woord: statusZonderWoord, statussen, pauze_icoon_bij_onbekend: pauzeBijOnbekend, losse_stippen: stippen,
    woorden, h1: document.querySelectorAll("h1").length, kopsprongen: sprong, dubbele_ids: Object.keys(ids).filter((k) => ids[k] > 1), kapotte_aria: kapot,
    hub_link: document.querySelectorAll('[data-testid="hub-link"]').length,
    grafieken: { kaders: document.querySelectorAll('figure[data-testid^="grafiek-"]').length, tabellen: document.querySelectorAll('[data-testid^="grafiek-"][data-testid$="-tabel"]').length },
  };
}

function focusInfo() {
  const el = document.activeElement; if (!el || el === document.body) return null;
  const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
  const d = el.tagName.toLowerCase() + (el.getAttribute("data-testid") ? `[${el.getAttribute("data-testid")}]` : "") + ' "' + ((el.getAttribute("aria-label") || el.innerText || "").replace(/\s+/g, " ").trim().slice(0, 30)) + '"';
  const pts = [[r.left + r.width / 2, r.top + r.height / 2], [r.left + 2, r.top + 2], [r.right - 2, r.bottom - 2], [r.left + 2, r.bottom - 2], [r.right - 2, r.top + 2]];
  let zicht = 0, inVp = 0;
  for (const [x, y] of pts) { if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) continue; inVp++; const top = document.elementFromPoint(x, y); if (top && (top === el || el.contains(top) || top.contains(el))) zicht++; }
  const cvs = document.createElement("canvas"); cvs.width = cvs.height = 1; const ctx = cvs.getContext("2d");
  const rgba = (s) => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = "#000"; ctx.fillStyle = s; ctx.fillRect(0, 0, 1, 1); const x = ctx.getImageData(0, 0, 1, 1).data; return [x[0], x[1], x[2], x[3] / 255]; };
  const over = (t, b) => { const a = t[3] + b[3] * (1 - t[3]); return [0, 1, 2].map((i) => (t[i] * t[3] + b[i] * b[3] * (1 - t[3])) / (a || 1)).concat([a]); };
  const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const bgOf = (e) => { const layers = []; for (let p = e; p && p.nodeType === 1; p = p.parentElement) layers.push(rgba(getComputedStyle(p).backgroundColor)); let c = [255, 255, 255, 1]; for (const l of layers.reverse()) c = over(l, c); return c; };
  const par = bgOf(el.parentElement); const ow = parseFloat(cs.outlineWidth); const has = cs.outlineStyle !== "none" && ow > 0;
  return { d, ow, ringR: has ? +ratio(over(rgba(cs.outlineColor), par), par).toFixed(2) : 0, bs: cs.boxShadow !== "none", inVp, zicht, top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height) };
}

async function runAxe(page) {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }, resultTypes: ["violations", "incomplete"] });
    return {
      overtredingen: r.violations.map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length, voorbeeld: v.nodes.slice(0, 3).map((x) => x.target.join(" ") + " :: " + (x.failureSummary || "").split("\n").slice(1, 2).join("")) })),
      na_te_kijken: r.incomplete.map((v) => ({ id: v.id, n: v.nodes.length })),
    };
  });
}

async function act(page, action) {
  if (action === "pause") {
    const b = page.getByTestId("pause-button"); await b.waitFor({ state: "visible" });
    await page.waitForFunction(() => { const x = document.querySelector('[data-testid="pause-button"]'); return x && !x.hasAttribute("disabled") && x.getAttribute("aria-disabled") !== "true"; }, null, { timeout: 15_000 });
    await b.click(); await page.getByRole("alertdialog").waitFor();
  } else if (action === "restore") {
    const b = page.getByRole("button", { name: "Terugzetten" }).first(); await b.waitFor({ state: "visible", timeout: 15_000 });
    await b.click(); await page.getByRole("alertdialog").waitFor();
  } else if (action === "help") {
    await page.locator("body").click({ position: { x: 5, y: 5 } }).catch(() => undefined);
    await page.keyboard.press("?"); await page.getByRole("dialog").waitFor();
  } else if (action === "profile") {
    await page.locator("button.avatar").first().click();
    await page.getByRole("dialog").first().waitFor();
  } else if (action === "more") {
    await page.locator("nav.bottomnav button").click(); await page.getByRole("dialog").first().waitFor();
  } else if (action === "compare") {
    const boxes = page.getByRole("checkbox", { name: /vergelijking/i }); await boxes.first().waitFor({ timeout: 15_000 });
    await boxes.nth(0).check(); await boxes.nth(1).check();
    await page.getByRole("button", { name: /Vergelijk \(2\)/ }).click(); await page.getByRole("dialog").waitFor();
  } else if (action === "tables") {
    for (const s of await page.locator('[data-testid^="grafiek-"][data-testid$="-tabel"] > summary').all()) { await s.scrollIntoViewIfNeeded(); await s.click(); }
  }
  await page.waitForTimeout(500);
}

async function measure(browser, state, group, slug, route, theme, width, opts = {}) {
  const key = `${group}/${slug}-${theme[0]}-${width}`;
  if (ONLY && !ONLY.test(key)) return;
  const ctx = await browser.newContext({ ...DEVICE(width), baseURL: BASE, deviceScaleFactor: 1, locale: "nl-NL", timezoneId: "Europe/Amsterdam", colorScheme: theme[1], reducedMotion: "reduce", storageState: opts.anonymous ? undefined : state });
  await ctx.addInitScript((t) => { try { localStorage.setItem("mso.theme", t); } catch { /* geblokkeerd */ } }, theme[1]);
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  for (const [pattern, reply] of opts.routes ?? []) await page.route(pattern, (r) => r.fulfill(reply));
  for (const pattern of opts.delay ?? []) await page.route(pattern, async (r) => { await new Promise((res) => setTimeout(res, 20_000)); await r.continue().catch(() => undefined); });
  if (opts.hub) await page.route("**/api/v1/settings", async (r) => {
    if (r.request().method() !== "GET") return r.continue();
    const res = await r.fetch(); const body = await res.json(); body.values = { ...body.values, "systeem.hub_url": opts.hub };
    await r.fulfill({ response: res, json: body });
  });
  try {
    await page.goto(route, { waitUntil: "domcontentloaded" });
    if (opts.noSettle) await page.waitForTimeout(2_500); else await settle(page);
    if (opts.stale) {
      await page.route(opts.stale, (r) => r.fulfill(json(503, { detail: "Service Unavailable", code: "error" })));
      if (opts.staleMark) await page.waitForSelector(".stale-mark", { timeout: 45_000 }); else await page.waitForTimeout(opts.staleWait ?? 35_000);
      await page.waitForTimeout(300);
    }
    if (opts.action) await act(page, opts.action);
    const m = await page.evaluate(inPage, width);
    const axe = await runAxe(page);
    let afdruk = null;
    if (SHOTS && !opts.noShot && (opts.forceShot || SHOT_WIDTHS.has(width))) {
      mkdirSync(path.join(OUT, group), { recursive: true });
      afdruk = `${group}/${slug}-${theme[0]}-${width}.png`;
      await page.screenshot({ path: path.join(OUT, afdruk), fullPage: !opts.action || opts.action === "tables", animations: "disabled" });
    }
    results[key] = { route, thema: theme[0], breedte: width, nagebootst: opts.routes || opts.hub || opts.stale || opts.delay ? true : undefined, afdruk, ...m, axe, js_fouten: errors };
    const fouten = axe.overtredingen.length + m.tekst_fout_aantal + m.ui_fout_aantal + m.doelen_kleiner_dan_44 + (m.overloop_px > 1 ? 1 : 0) + m.tekst_kleiner_dan_14 + m.groen_buiten_gezond_aantal + m.woorden.length;
    console.log(`${fouten ? "LET OP" : "ok    "} ${key} axe=${axe.overtredingen.map((v) => v.id).join(",") || 0} tekst=${m.tekst_fout_aantal} ui=${m.ui_fout_aantal} doel=${m.doelen_kleiner_dan_44} overloop=${m.overloop_px} groen=${m.groen_buiten_gezond_aantal} woorden=${m.woorden.length}`);
  } catch (e) {
    results[key] = { route, thema: theme[0], breedte: width, fout: String(e).slice(0, 300) };
    console.log(`FOUT   ${key}: ${String(e).split("\n")[0]}`);
  } finally { await ctx.close(); }
}

async function keyboard(browser, state, slug, route, theme, width, routes = []) {
  const key = `toetsenbord/${slug}-${theme[0]}-${width}`;
  if (ONLY && !ONLY.test(key)) return;
  const ctx = await browser.newContext({ ...DEVICE(width), baseURL: BASE, deviceScaleFactor: 1, locale: "nl-NL", timezoneId: "Europe/Amsterdam", colorScheme: theme[1], reducedMotion: "reduce", storageState: state });
  await ctx.addInitScript((t) => { try { localStorage.setItem("mso.theme", t); } catch { /* geblokkeerd */ } }, theme[1]);
  const page = await ctx.newPage();
  for (const [pattern, reply] of routes) await page.route(pattern, (r) => r.fulfill(reply));
  try {
    await page.goto(route, { waitUntil: "domcontentloaded" }); await settle(page);
    const stops = [];
    for (let i = 0; i < 300; i++) {
      await page.keyboard.press("Tab");
      let f = await page.evaluate(focusInfo);
      if (f && (f.ow < 2.5 || f.ringR < 3)) { await page.waitForTimeout(300); f = await page.evaluate(focusInfo); }
      if (!f) break; stops.push(f);
      if (stops.length > 3 && f.d === stops[0].d) break;
    }
    results[key] = {
      route, thema: theme[0], breedte: width, stops: stops.length, eerste: stops[0]?.d ?? null,
      zonder_ring: stops.filter((f) => f.ow < 2.5).map((f) => `${f.d} ${f.ow}px`),
      zwakke_ring: stops.filter((f) => f.ow >= 2.5 && f.ringR < 3).map((f) => `${f.d} ${f.ringR}`),
      bedekt: stops.filter((f) => f.inVp > 0 && f.zicht === 0).map((f) => `${f.d} top=${f.top} h=${f.h}`),
      midden_bedekt: stops.filter((f) => f.inVp > 0 && f.zicht > 0 && f.zicht < f.inVp).length,
      deels_bedekt: stops.filter((f) => f.inVp > 0 && f.zicht > 0 && f.zicht < f.inVp).slice(0, 6).map((f) => `${f.d} top=${f.top} h=${f.h} zicht=${f.zicht}/${f.inVp}`),
      ring_min: stops.filter((f) => f.ow > 0).reduce((m, f) => Math.min(m, f.ringR), 99),
    };
    const k = results[key];
    console.log(`${k.zonder_ring.length + k.zwakke_ring.length + k.bedekt.length ? "LET OP" : "ok    "} ${key} stops=${k.stops} zonderRing=${k.zonder_ring.length} zwak=${k.zwakke_ring.length} bedekt=${k.bedekt.length} ringMin=${k.ring_min}`);
  } catch (e) {
    results[key] = { route, fout: String(e).slice(0, 300) }; console.log(`FOUT   ${key}: ${String(e).split("\n")[0]}`);
  } finally { await ctx.close(); }
}

const save = () => writeFileSync(resultsFile, `${JSON.stringify(Object.fromEntries(Object.entries(results).sort(([a], [b]) => a.localeCompare(b))), null, 1)}\n`);
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const state = await storageState();
if (WHAT === "schermen" || WHAT === "alles") {
  for (const theme of THEMES) for (const w of WIDTHS) { for (const [slug, route] of SCREENS) await measure(browser, state, "schermen", slug, route, theme, w); save(); }
}
if (WHAT === "tabs" || WHAT === "alles") {
  for (const theme of THEMES) for (const w of [320, 390, 1440]) { for (const [slug, route] of TABS) await measure(browser, state, "tabs", slug, route, theme, w, { noShot: true }); save(); }
}
if (WHAT === "beheer" || WHAT === "alles") { // zelfde schermen in Beheermodus (meer bedieningen actief); alleen meten
  for (const theme of THEMES) for (const w of [390, 1440]) { for (const [slug, route] of SCREENS) await measure(browser, state, "beheer", slug, route, theme, w, { routes: [MANAGE], noShot: true }); save(); }
}
if (WHAT === "toestanden" || WHAT === "alles") {
  for (const [slug, route, opts] of STATES) for (const theme of THEMES) for (const w of opts.widths ?? [390, 1440]) await measure(browser, state, "toestanden", slug, route, theme, w, { ...opts, forceShot: true });
  save();
}
if (WHAT === "toetsenbord" || WHAT === "alles") {
  for (const theme of THEMES) for (const w of [390, 1440]) { for (const [slug, route] of SCREENS) await keyboard(browser, state, slug, route, theme, w); save(); }
}
await browser.close();
save();
console.log(`metingen: ${Object.keys(results).length} → ${resultsFile}`);
