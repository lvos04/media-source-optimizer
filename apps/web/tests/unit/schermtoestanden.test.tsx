// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import CostsPage from "@/app/(app)/kosten/page";
import LogsPage from "@/app/(app)/logs/page";
import SystemPage from "@/app/(app)/systeem/page";
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
let uitval: "geen" | "offline" | "serverfout" | "kostenbron" | "dienstenbron";
let mislukteVerzoeken: string[];
let bronGepauzeerd: boolean;
let maandbedragInCenten: number;
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
    switch (endpoint) {
      case "/setup/status": return Response.json({ setup_required: false, admin_exists: true });
      case "/auth/me": return Response.json({ user: { username: "proef", role: "admin" } });
      case "/system/health": return Response.json({ status: "healthy", components: [{ name: "Proefbron", status: "healthy" }] });
      case "/system/mode": return Response.json({ mode: "autopilot", paused: bronGepauzeerd });
      case "/audit": return Response.json({ items: [], next_before_id: null });
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
