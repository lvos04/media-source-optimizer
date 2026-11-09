// Peil-migratie MSO — nulmeting toetsenbord (Tab-doorloop) NA de omzetting naar Peil, tegen een draaiende teststack.
// Alleen-lezen hulpscript bij docs/ontwerp/PEIL_MIGRATIE.md (zelfde omgeving als maak-afdrukken.mjs).
// Per scherm × breedte (390 telefoon, 1440 desktop) × thema: de eerste 40 Tab-stops; per stop de focusstijl
// (outline-breedte, box-shadow) en of het element bedekt is door de vaste kopbalk of ondernavigatie (WCAG 2.4.11).
// Uitvoer: toetsenbord.json naast dit script.
import { writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.MSO_E2E_BASE_URL ?? "http://127.0.0.1:48081";
const USER = process.env.MSO_E2E_USER ?? "";
const PASSWORD = process.env.MSO_E2E_PASSWORD ?? "";
if (!USER || !PASSWORD) throw new Error("MSO_E2E_USER en MSO_E2E_PASSWORD zijn verplicht");
const { chromium, request } = await import(process.env.PEIL_PLAYWRIGHT ?? "playwright");

const ROUTES = [["overzicht", "/"], ["downloads", "/downloads"], ["instellingen", "/instellingen"], ["ontbrekend", "/ontbrekend"]];
const DEVICES = {
  390: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
  1440: { viewport: { width: 1440, height: 900 } },
};
const STOPS = 40;

const api = await request.newContext({ baseURL: BASE });
const login = await api.post("/api/v1/auth/login", { data: { username: USER, password: PASSWORD } });
if (login.status() !== 200) throw new Error(`inloggen mislukt: HTTP ${login.status()}`);
const state = await api.storageState();
await api.dispose();

const browser = await chromium.launch();
const out = {};
for (const theme of ["light", "dark"]) {
  for (const [w, dev] of Object.entries(DEVICES)) {
    for (const [slug, route] of ROUTES) {
      const ctx = await browser.newContext({ ...dev, baseURL: BASE, storageState: state, locale: "nl-NL", colorScheme: theme });
      await ctx.addInitScript((t) => { try { localStorage.setItem("mso.theme", t); } catch { /* geblokkeerd */ } }, theme);
      const page = await ctx.newPage();
      await page.goto(route);
      await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
      await page.waitForTimeout(800);
      const stops = [];
      for (let i = 0; i < STOPS; i++) {
        await page.keyboard.press("Tab");
        stops.push(await page.evaluate(() => {
          const el = document.activeElement;
          if (!el || el === document.body) return null;
          const cs = getComputedStyle(el);
          const r = el.getBoundingClientRect();
          const cx = Math.min(Math.max(r.left + r.width / 2, 0), window.innerWidth - 1);
          const cy = Math.min(Math.max(r.top + r.height / 2, 0), window.innerHeight - 1);
          const hit = document.elementFromPoint(cx, cy);
          const bar = hit?.closest(".topbar, .bottomnav");
          const covered = Boolean(bar && !bar.contains(el));
          const name = (el.getAttribute("aria-label") || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 40);
          return { tag: el.tagName.toLowerCase(), naam: name, outline: `${cs.outlineStyle} ${cs.outlineWidth}`, schaduw: cs.boxShadow !== "none", bedekt: covered };
        }));
      }
      const real = stops.filter(Boolean);
      out[`${slug}-${theme === "light" ? "licht" : "donker"}-${w}`] = {
        tabstops: real.length,
        eerste: real[0] ?? null,
        bedekt_door_vaste_balk: real.filter((s) => s.bedekt).map((s) => `${s.tag} "${s.naam}"`),
        focusstijlen: [...new Set(real.map((s) => `${s.outline}${s.schaduw ? " + box-shadow" : ""}`))],
      };
      console.log(`ok   ${slug}-${theme}-${w}`);
      await ctx.close();
    }
  }
}
await browser.close();
writeFileSync(path.join(HERE, "toetsenbord.json"), `${JSON.stringify(out, null, 1)}\n`);
