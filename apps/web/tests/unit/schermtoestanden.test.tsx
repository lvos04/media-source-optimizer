// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import CostsPage from "@/app/(app)/kosten/page";
import LogsPage from "@/app/(app)/logs/page";
import MediaGuardPage from "@/app/(app)/mediaguard/page";
import WantedPage from "@/app/(app)/ontbrekend/page";
import SystemPage from "@/app/(app)/systeem/page";
import SearchPage from "@/app/(app)/zoeken/page";
import { AutopilotCard, WeeklyDigest } from "@/components/mso/automation/autopilot-summary";
import { ModeSettingsCard } from "@/components/mso/mode-settings";
import { UsenetDownloadsTab } from "@/components/mso/downloads/usenet-tab";
import { CompatTable } from "@/components/mso/mediaguard/parts";
import { HeaderIndexCard } from "@/components/mso/usenet/header-index";
import { AppShell } from "@/components/shell/app-shell";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => router, usePathname: () => window.location.pathname,
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: {href: string; children: ReactNode}) => <a href={href} {...rest}>{children}</a>,
}));

let root: Root;
let uitval: "geen" | "offline" | "serverfout" | "kostenbron" | "dienstenbron" | "modusbron";
let mislukteVerzoeken: string[];
let bronGepauzeerd: boolean;
let maandbedragInCenten: number;
let bestemmingGekoppeld: boolean;
let wachtrijGepauzeerd: boolean;
let zoekresultaten: number;
let uitvoeringActief: boolean;
const perTestId = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`);

function knop(container: ParentNode, label: string) {
  const button = [...container.querySelectorAll("button")].find((el) => el.textContent === label);
  if (!button) throw new Error(`Proefknop ontbreekt: ${label}`);
  return act(async () => { button.click(); });
}

beforeEach(() => {
  uitval = "geen";
  mislukteVerzoeken = [];
  bronGepauzeerd = true;
  maandbedragInCenten = 1715;
  bestemmingGekoppeld = false;
  wachtrijGepauzeerd = true;
  zoekresultaten = 17;
  uitvoeringActief = true;
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("fetch", async (url: string) => {
    const endpoint = url.split("?")[0].replace("/api/v1", "");
    if (uitval === "offline" || (uitval === "kostenbron" && endpoint === "/costs/summary")
      || (uitval === "dienstenbron" && endpoint === "/costs/services")) {
      mislukteVerzoeken.push(endpoint);
      throw new TypeError("Verbinding onderbroken in de proef");
    }
    if (uitval === "serverfout" && endpoint === "/system/health") {
      mislukteVerzoeken.push(endpoint);
      return Response.json({ detail: "Storing in de proefbron" }, { status: 500 });
    }
    if (uitval === "modusbron" && endpoint === "/system/mode") return Response.error();
    switch (endpoint) {
      case "/overview/autopilot": return Response.json({
        mode: "autopilot", paused: false, autopilot: uitvoeringActief, automation_allowed: uitvoeringActief,
        generated_at: new Date().toISOString(), actions_total: 0, by_day: [], waiting: {}, syncs: { failing_now: [] },
      });
      case "/automation/status": return Response.json({
        mode: "autopilot", paused: false, autopilot: uitvoeringActief, automation_allowed: uitvoeringActief,
        areas: [{ area: "usenet", label: "Usenet", auto_apply_safe: true, effective: uitvoeringActief,
          via_autopilot: uitvoeringActief }],
      });
      case "/setup/status": return Response.json({ setup_required: false, admin_exists: true });
      case "/auth/me": return Response.json({ user: { username: "proef", role: "admin" } });
      case "/system/health": return Response.json({ status: "healthy", components: [{ name: "Proefbron", status: "healthy" }] });
      case "/system/mode": return Response.json({ mode: "autopilot", paused: bronGepauzeerd });
      case "/audit": return Response.json({ items: [], next_before_id: null });
      case "/mediaguard/status": return Response.json({
        mode: bestemmingGekoppeld ? "degraded" : "connected", configured: true, auth_method: "apikey",
        warnings: [], compat: { reference: { repo: "proefbron", commit: "proef" }, routes: [], never_used: [] },
        search: { available: true },
      });
      case "/recovery/wanted": return Response.json({ items: [], total: 0, mode: bestemmingGekoppeld ? "connected" : "standalone" });
      case "/search": return Response.json({
        items: [], sources: [{ source_id: "prowlarr", label: "Prowlarr", status: "timeout", count: 0, latency_ms: null }],
        trace: [], trace_id: "zoekproef", total: zoekresultaten,
      });
      case "/usenet/sab/queue": return Response.json({ items: [], paused: wachtrijGepauzeerd, status: "Idle" });
      case "/usenet/headerindex": return Response.json({
        config: { enabled: false, provider: "", groups: [] }, usage: { headers: 0 }, runs: [], disclaimer: "",
      });
      case "/costs/summary": return Response.json({
        total: { monthly_cents: maandbedragInCenten, currency: "EUR", mixed_currencies: false, services: 1, unknown: [], by_kind: { other: maandbedragInCenten } },
        renewals: [], reminder_days: 30, disclaimers: [],
      });
      case "/costs/services": return Response.json({
        items: [{ id: 1, label: "Proefdienst", kind: "other", billing: "monthly", amount_cents: maandbedragInCenten, currency: "EUR",
          active: true, source_id: null, source_label: null, block_size_gb: null, block_used_gb: null,
          renewal_date: null, notes: "", backbone: null }],
        period_days: 30, price_missing: 0,
      });
      case "/costs/services/import": return Response.json({ created: 0, sab_providers_created: 0 });
      default: return Response.json({ detail: "Not Found" }, { status: 404 });
    }
  });
  const container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

async function toon(pad: string, pagina: ReactNode) {
  window.history.replaceState(null, "", pad);
  await act(async () => { root.render(<AppShell>{pagina}</AppShell>); });
}

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.replaceChildren();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("markeert de eerder bevestigde pauze als laatst bekend tot de modusbron herstelt", async () => {
  await toon("/systeem", <SystemPage />);
  expect(perTestId("sidebar-health")?.textContent).toContain("Alles gezond");
  const belofte = "Er worden geen nieuwe zoekopdrachten of automatische wijzigingen gestart.";
  expect(perTestId("pause-banner")?.textContent).toContain(belofte);
  uitval = "offline";
  bronGepauzeerd = false;
  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(mislukteVerzoeken).toEqual(expect.arrayContaining(["/system/mode", "/system/health"]));
  expect(perTestId("sidebar-health")?.textContent).toContain("Onbekend");
  const banner = perTestId("pause-banner");
  expect(banner?.querySelector('[data-status="stale"]')?.textContent).toMatch(/gepauzeerd.*laatst bekend/i);
  expect(banner?.textContent).not.toContain(belofte);
  expect(banner?.querySelector("button")).toBeNull();
  expect(perTestId("pause-button")).toHaveProperty("disabled", true);
  expect(perTestId("mode-chip")?.dataset.mode).toBe("unknown");
  uitval = "geen";
  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(perTestId("pause-banner")).toBeNull();
  expect(perTestId("pause-button")).toHaveProperty("disabled", false);
  expect(perTestId("mode-chip")?.dataset.mode).toBe("autopilot");
});

it("toont bij een HTTP 500 op Onderdelen de storingsmelding", async () => {
  await toon("/systeem", <SystemPage />);
  const kaart = Array.from(document.querySelectorAll("section")).find(
    (el) => el.querySelector("h2")?.textContent === "Onderdelen",
  );
  if (!kaart) throw new Error("Kaart Onderdelen ontbreekt");
  expect(kaart.textContent).toContain("Gezond");
  uitval = "serverfout";
  await knop(kaart, "Verversen");
  expect(mislukteVerzoeken).toContain("/system/health");
  expect(kaart.querySelector('[data-state="error"]')?.textContent).toContain("HTTP 500");
  expect(kaart.querySelector('[data-state="offline"]')).toBeNull();
  expect(kaart.textContent).not.toContain("Gezond");
  uitval = "offline";
  await knop(kaart, "Opnieuw proberen");
  expect(kaart.querySelector('[data-state="offline"]')?.textContent).toContain("Backend niet bereikbaar");
  uitval = "geen";
  await knop(kaart, "Opnieuw proberen");
  expect(kaart.textContent).toContain("Gezond");
});

it("toetst versheid per kostenbron en werkt alle vier kerncijfers bij na herstel", async () => {
  await toon("/kosten", <CostsPage />);
  const kerncijfers = [...document.querySelectorAll(".kpi-grid .kpi")];
  expect(kerncijfers).toHaveLength(4);
  const [totaal, onbekende, verlenging, diensten] = kerncijfers;
  const laatstBekend = (kaart: Element) => kaart.querySelector('[data-status="stale"]');
  expect(totaal.textContent).toContain("17,15");
  uitval = "kostenbron";
  maandbedragInCenten = 2250;
  const ververs = () => knop(document, "Providers en indexers ophalen");
  await ververs();
  expect(mislukteVerzoeken).toEqual(["/costs/summary"]);
  for (const kaart of [totaal, onbekende, verlenging]) {
    expect(laatstBekend(kaart)?.textContent).toContain("laatst bekend");
    expect(kaart.querySelector('.kpi-value [data-status="unknown"]')).not.toBeNull();
  }
  expect(totaal.textContent).not.toContain("17,15");
  expect(laatstBekend(diensten)).toBeNull();

  uitval = "dienstenbron";
  await ververs();
  expect(totaal.textContent).toContain("22,50");
  expect(laatstBekend(totaal)).toBeNull();
  expect(laatstBekend(diensten)?.textContent).toContain("laatst bekend");

  uitval = "geen";
  await ververs();
  for (const kaart of kerncijfers) expect(laatstBekend(kaart)).toBeNull();
  expect(diensten.querySelector(".kpi-value")?.textContent).toBe("1");
});

it("markeert een eerder lege auditlijst en trekt de markering in na herstel", async () => {
  await toon("/logs", <LogsPage />);
  const kaart = Array.from(document.querySelectorAll("section")).find((el) => el.querySelector("h2")?.textContent?.startsWith("Audittrail"));
  expect(kaart?.textContent).toContain("Geen auditregels gevonden");
  if (!kaart) throw new Error("Kaart Audittrail ontbreekt");
  uitval = "offline";
  await knop(kaart, "Verversen");
  expect(kaart?.querySelector('[data-status="stale"]')?.textContent).toContain("Verouderd");
  uitval = "geen";
  await knop(kaart, "Verversen");
  expect(kaart?.querySelector('[data-status="stale"]')).toBeNull();
  expect(kaart?.textContent).toContain("Geen auditregels gevonden");
});

it("toont een gepauzeerde downloadwachtrij als onbekend na uitval van de wachtrijbron", async () => {
  await toon("/downloads", <UsenetDownloadsTab />);
  const kop = [...document.querySelectorAll("main h2")].find((el) => el.textContent?.startsWith("Wachtrij (SABnzbd)"));
  expect(kop?.textContent).toContain("Gepauzeerd");
  uitval = "offline";
  wachtrijGepauzeerd = false;
  await act(async () => vi.advanceTimersByTimeAsync(15_000));
  expect(kop?.querySelector('[data-status="unknown"]')?.textContent).toBe("Onbekend");
  expect(kop?.textContent).not.toContain("Gepauzeerd");

  uitval = "geen";
  await act(async () => vi.advanceTimersByTimeAsync(15_000));
  expect(kop?.textContent).toContain("Inactief");
  expect(kop?.querySelector('[data-status="unknown"]')).toBeNull();
});

it("werkt de koppelstatus boven Ontbrekend bij na uitval en herstel van de bron", async () => {
  await toon("/ontbrekend", <WantedPage />);
  const kop = document.querySelector("main .page-header");
  expect(kop?.textContent).toContain("Zelfstandig");
  uitval = "offline";
  bestemmingGekoppeld = true;
  await act(async () => vi.advanceTimersByTimeAsync(120_000));
  expect(kop?.querySelector('[data-status="unknown"]')?.textContent).toBe("Onbekend");
  expect(kop?.textContent).not.toContain("Zelfstandig");

  uitval = "geen";
  await act(async () => vi.advanceTimersByTimeAsync(120_000));
  expect(kop?.textContent).toContain("Gekoppeld");
});

it("toont de status van de headerindex als onbekend als de eigen poll faalt", async () => {
  await toon("/usenet-providers", <HeaderIndexCard providers={[]} />);
  const kop = document.querySelector("main h2")?.parentElement;
  expect(kop?.textContent).toContain("Uit");
  uitval = "offline";
  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(kop?.querySelector('[data-status="unknown"]')?.textContent).toBe("Onbekend");

  uitval = "geen";
  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(kop?.textContent).toContain("Uit");
});

it("markeert de verbindingsstatus en ingestelde toegangsvorm op MediaGuard bij uitval van de statusbron", async () => {
  await toon("/mediaguard", <MediaGuardPage />);
  const kop = [...document.querySelectorAll("main h2")].find((el) => el.textContent === "Verbinding")?.parentElement;
  expect(kop?.textContent).toContain("Gekoppeld");
  uitval = "offline";
  bestemmingGekoppeld = true;
  await act(async () => vi.advanceTimersByTimeAsync(60_000));
  expect(kop?.querySelector('[data-status="unknown"]')?.textContent).toBe("Onbekend");
  expect(document.querySelector('main [data-status="stale"]')?.textContent).toContain("laatst bekend");

  uitval = "geen";
  await act(async () => vi.advanceTimersByTimeAsync(60_000));
  expect(kop?.textContent).toContain("Beperkt");
  expect(document.querySelector('main [data-status="stale"]')).toBeNull();
});

it("toont OpenAPI als alleen voor beheerder in de compatibiliteitstabel", async () => {
  await toon("/mediaguard", <CompatTable compat={{
    reference: { repo: "proefbron", commit: "proef" },
    live_source: "probe",
    checked_at: null,
    routes: [{
      id: "openapi", path: "/api/v1/openapi.json", method: "GET", label: "OpenAPI-document",
      role: "admin", access: "read", purpose: "OpenAPI-schema", ref: "proef", required: false,
      verified: true, live: "alleen_voor_beheerder",
    }],
    never_used: [],
  }} />);
  const status = document.querySelector('[data-label="Deze installatie"]');
  expect(status?.textContent).toBe("Alleen voor beheerder");
  expect(status?.querySelector('[data-status="down"]')).toBeNull();
});

it("markeert het aantal zoekresultaten en het bronrapport na een mislukte zoekverversing", async () => {
  await toon("/zoeken?q=Peilproef", <SearchPage />);
  const kop = [...document.querySelectorAll("main h2")].find((el) => el.textContent?.startsWith("Resultaten"));
  expect(kop?.textContent).toContain("17 gevonden");
  uitval = "offline";
  await knop(document, "Opnieuw proberen (ruimere time-out)");
  expect(kop?.querySelector('[data-status="stale"]')?.textContent).toContain("laatst bekend");
  expect(kop?.textContent).not.toContain("17 gevonden");
  const rapport = perTestId("source-prowlarr")?.closest("section")?.parentElement;
  expect(rapport?.querySelector('[data-status="stale"]')?.textContent).toContain("laatst bekend");

  uitval = "geen";
  zoekresultaten = 22;
  await knop(document, "Zoeken");
  expect(kop?.textContent).toContain("22 gevonden");
  expect(rapport?.querySelector('[data-status="stale"]')).toBeNull();
});

it("toont stilstand en hervatting op beide overzichten en per onderdeel bij uitval van de modusbron", async () => {
  uitval = "modusbron";
  uitvoeringActief = false;
  await toon("/", <><AutopilotCard /><WeeklyDigest /><ModeSettingsCard /></>);
  const kaart = () => perTestId("mode-auto-apply")?.textContent;
  expect(document.body.textContent).not.toContain("Autopilot actief");
  expect(document.body.textContent?.match(/Autopilot staat stil/g)).toHaveLength(2);
  expect(kaart()).toContain("Usenet: Uit");
  expect(kaart()).not.toContain("aan via Autopilot");

  uitvoeringActief = true;
  await act(async () => vi.advanceTimersByTimeAsync(120_000));
  expect(document.body.textContent?.match(/Autopilot actief/g)).toHaveLength(2);
  expect(kaart()).toContain("Usenet: aan via Autopilot");
});
