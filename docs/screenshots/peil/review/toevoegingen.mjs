// Peil-review MSO (stap 5) — werken de twee toegestane toevoegingen in de ECHTE app?
//  1. "Terug naar de Hub": via het echte GET/PUT /api/v1/settings (geen onderschepping). Ongeldige adressen → 422 en
//     geen link; geldig adres → link in de zijbalk (1440) en als eerste in de Meer-sheet (390), 44 px, zelfde tabblad;
//     leeg → link weg. De oorspronkelijke waarde wordt aan het eind teruggezet.
//  2. "Toon als tabel" + toetsenbord bij elke grafiek die de testdata toont: tabel opent met rijen, knoptekst wisselt,
//     plotvak leest met End/Home/←/→ voor via aria-live, Esc wist de uitlezing.
// Alleen tegen een wegwerp-teststack (tests/stack/run.sh). Wachtwoorden alleen uit de omgeving.
//   MSO_E2E_BASE_URL=… MSO_E2E_USER=stackadmin MSO_E2E_PASSWORD=… node docs/screenshots/peil/review/toevoegingen.mjs [uitmap]
import { writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(process.argv[2] ?? HERE);
const BASE = process.env.MSO_E2E_BASE_URL ?? "http://127.0.0.1:48081";
const USER = process.env.MSO_E2E_USER ?? "";
const PASSWORD = process.env.MSO_E2E_PASSWORD ?? "";
if (!USER || !PASSWORD) throw new Error("MSO_E2E_USER en MSO_E2E_PASSWORD zijn verplicht");
const { chromium, request } = await import(process.env.PEIL_PLAYWRIGHT ?? "/home/transcode/truenas-v2/.lokaal/ontwerp-tools/node_modules/playwright/index.mjs");

const HUB = "http://hub.thuis.test:30080"; // gereserveerde .test-naam: nooit een echt adres
const ONGELDIG = ["http://localhost:30080", "http://127.0.0.1:30080", "ftp://hub.thuis.test", "http://hub.thuis.test:30080/pad", "http://gebruiker:geheim@hub.thuis.test", "geen-url"];
const res = { hub: {}, grafieken: [], controles: [] };
const ok = (naam, voorwaarde, detail = "") => { res.controles.push({ naam, ok: Boolean(voorwaarde), detail }); console.log(`${voorwaarde ? "ok  " : "FOUT"} ${naam}${detail ? ` — ${detail}` : ""}`); };

const api = await request.newContext({ baseURL: BASE });
const login = await api.post("/api/v1/auth/login", { data: { username: USER, password: PASSWORD } });
if (login.status() !== 200) throw new Error(`inloggen mislukt: HTTP ${login.status()}`);
const state = await api.storageState();
const csrf = (await login.json()).csrf_token ?? (await (await api.get("/api/v1/auth/me")).json()).csrf_token;
const headers = csrf ? { "X-CSRF-Token": csrf } : {};
const put = (v) => api.put("/api/v1/settings", { data: { values: { "systeem.hub_url": v } }, headers });
const before = (await (await api.get("/api/v1/settings")).json()).values?.["systeem.hub_url"] ?? "";
res.hub.oorspronkelijk = before;

const browser = await chromium.launch();
async function hubLink(width, openMore = false) {
  const ctx = await browser.newContext({ viewport: { width, height: width < 768 ? 844 : 900 }, isMobile: width < 768, hasTouch: width < 1024, baseURL: BASE, storageState: state, locale: "nl-NL" });
  const page = await ctx.newPage();
  await page.goto("/"); await page.waitForLoadState("networkidle").catch(() => undefined); await page.waitForTimeout(800);
  if (openMore) { await page.locator("nav.bottomnav button").click(); await page.getByRole("dialog").first().waitFor(); await page.waitForTimeout(300); }
  const info = await page.evaluate(() => {
    const links = [...document.querySelectorAll('[data-testid="hub-link"]')].filter((a) => a.getBoundingClientRect().width > 0);
    const a = links[0];
    if (!a) return { zichtbaar: 0 };
    const r = a.getBoundingClientRect();
    const dlg = a.closest('[role="dialog"]');
    const eersteInDialoog = dlg ? [...dlg.querySelectorAll('a[href], button')].filter((x) => x.getBoundingClientRect().width > 0 && !x.closest('[aria-label="Sluiten"]')).map((x) => x.textContent.trim())[1] : null;
    return { zichtbaar: links.length, tekst: a.textContent.trim(), href: a.getAttribute("href"), target: a.getAttribute("target"), b: Math.round(r.width), h: Math.round(r.height), eersteInDialoog };
  });
  await ctx.close();
  return info;
}

try {
  ok("zonder instelling: geen link (1440)", (await hubLink(1440)).zichtbaar === 0 || before !== "");
  if (before === "") ok("zonder instelling: geen link in de Meer-sheet (390)", (await hubLink(390, true)).zichtbaar === 0);
  res.hub.ongeldig = [];
  for (const v of ONGELDIG) {
    const r = await put(v);
    res.hub.ongeldig.push({ waarde: v, http: r.status() });
    ok(`ongeldig adres geweigerd: ${v}`, r.status() === 422, `HTTP ${r.status()}`);
  }
  ok("na ongeldige pogingen nog steeds geen link", (await hubLink(1440)).zichtbaar === 0 || before !== "");
  const r = await put(HUB);
  ok("geldig adres opgeslagen", r.status() === 200, `HTTP ${r.status()}`);
  const desk = await hubLink(1440);
  res.hub.desktop = desk;
  ok("link zichtbaar in de zijbalk (1440)", desk.zichtbaar === 1 && desk.tekst === "Terug naar de Hub", JSON.stringify(desk));
  ok("link wijst naar het ingestelde adres, zelfde tabblad", desk.href === HUB && !desk.target, `${desk.href} target=${desk.target}`);
  ok("link ≥ 44 px hoog (1440)", desk.h >= 44, `${desk.b}×${desk.h}`);
  const mob = await hubLink(390, true);
  res.hub.mobiel = mob;
  ok("link zichtbaar in de Meer-sheet (390)", mob.zichtbaar === 1, JSON.stringify(mob));
  ok("link ≥ 44 px hoog (390)", mob.h >= 44, `${mob.b}×${mob.h}`);
  const leeg = await put("");
  ok("leeg maken mag", leeg.status() === 200, `HTTP ${leeg.status()}`);
  ok("leeg: link weer weg", (await hubLink(1440)).zichtbaar === 0);
} finally {
  const terug = await put(before);
  res.hub.teruggezet = terug.status();
}

// ---- grafieken: tabel en toetsenbord
for (const route of ["/", "/kosten", "/ontbrekend", "/usenet-providers"]) {
  for (const width of [390, 1440]) {
    const ctx = await browser.newContext({ viewport: { width, height: 900 }, isMobile: width < 768, hasTouch: width < 1024, baseURL: BASE, storageState: state, locale: "nl-NL", reducedMotion: "reduce" });
    const page = await ctx.newPage();
    await page.goto(route); await page.waitForLoadState("networkidle").catch(() => undefined); await page.waitForTimeout(800);
    for (const det of await page.locator('details[data-testid^="grafiek-"][data-testid$="-tabel"]').all()) {
      const id = await det.getAttribute("data-testid");
      const summary = det.locator("summary");
      const voor = (await summary.textContent()).trim();
      await summary.focus(); await page.keyboard.press("Enter"); await page.waitForTimeout(200);
      const na = (await summary.textContent()).trim();
      const rijen = await det.locator("tbody tr").count();
      const kop = await det.locator("thead th[scope=col]").count();
      const caption = ((await det.locator("caption").textContent()) ?? "").trim();
      const g = { route, breedte: width, tabel: id, knop_voor: voor, knop_na: na, rijen, kolomkoppen: kop, caption };
      // plotvak (alleen bij ChartFrame): figure met dezelfde naam
      const fig = page.locator(`figure[data-testid="${id.replace(/-tabel$/, "")}"]`);
      if (await fig.count()) {
        const plot = fig.locator('[tabindex="0"][role="img"]');
        await plot.focus();
        const live = fig.locator("[aria-live]");
        await page.keyboard.press("End"); await page.waitForTimeout(80); g.end = (await live.textContent()).trim();
        await page.keyboard.press("Home"); await page.waitForTimeout(80); g.home = (await live.textContent()).trim();
        await page.keyboard.press("ArrowRight"); await page.waitForTimeout(80); g.rechts = (await live.textContent()).trim();
        await page.keyboard.press("Escape"); await page.waitForTimeout(80); g.esc = (await live.textContent()).trim();
        g.plot_label = (await plot.getAttribute("aria-label")) ?? "";
        ok(`${id} ${width}: toetsenbord leest voor`, g.end && g.home && g.rechts && g.end !== g.home && g.esc === "", `${g.home} | ${g.rechts} | ${g.end}`);
      }
      res.grafieken.push(g);
      ok(`${id} ${width}: tabel opent met rijen`, voor === "Toon als tabel" && na === "Verberg tabel" && rijen > 0 && kop > 1, `${rijen} rijen, ${kop} kolommen, "${caption}"`);
    }
    await ctx.close();
  }
}
await browser.close();
res.samenvatting = { controles: res.controles.length, mislukt: res.controles.filter((c) => !c.ok).length };
writeFileSync(path.join(OUT, "toevoegingen.json"), `${JSON.stringify(res, null, 1)}\n`);
console.log(`toevoegingen: ${res.samenvatting.controles} controles, ${res.samenvatting.mislukt} mislukt`);
