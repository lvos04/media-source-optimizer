import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Dot, ErnstLabel, InlineState, StatusChip, TONE_ICON, type Tone } from "@/components/mg/primitives";
import { RiskChip } from "@/components/mso/risk-chip";
import { I18nProvider } from "@/lib/i18n";

const html = (node: React.ReactNode) => renderToStaticMarkup(<I18nProvider locale="nl-NL">{node}</I18nProvider>);

describe("StatusChip (Peil StatusBadge)", () => {
  it("keeps the .chip hook and marks the health axis", () => {
    const out = html(<StatusChip tone="healthy">Gezond</StatusChip>);
    expect(out).toContain("chip");
    expect(out).toContain('data-status="healthy"');
    expect(out).toContain("lucide-circle-check");
  });
  it("never renders the pause icon for unknown/neutral (Peil §7)", () => {
    for (const tone of ["unknown", "neutral", "unlinked"] as Tone[]) {
      const out = html(<StatusChip tone={tone}>x</StatusChip>);
      expect(out).not.toMatch(/lucide-(circle-)?pause|lucide-pause/);
    }
    expect(TONE_ICON.neutral).toBeNull();
    expect(html(<StatusChip tone="unknown">Onbekend</StatusChip>)).toContain("border-dashed");
  });
  it("a non-health success is a neutral label with a check, never the healthy shape", () => {
    const out = html(<StatusChip tone="success">Aan</StatusChip>);
    expect(out).toContain("chip--done");
    expect(out).not.toContain("chip--healthy");
    expect(out).not.toContain("data-status");
  });
  it("Storing uses octagon-x", () => {
    expect(html(<StatusChip tone="danger">Storing</StatusChip>)).toContain("lucide-octagon-x");
  });
});

describe("RiskChip (Peil RisicoBadge)", () => {
  it.each([
    ["advies", "R0", "Alleen lezen", 1],
    ["automatisch_veilig", "R1", "Omkeerbaar", 2],
    ["toestemming_nodig", "R2", "Goedkeuring nodig", 3],
  ] as const)("%s → %s · %s with %i steps", (risk, code, word, steps) => {
    const out = html(<RiskChip risk={risk} />);
    expect(out).toContain(`data-risico="${code}"`);
    expect(out).toContain(word);
    expect((out.match(/<i /g) ?? []).length).toBe(steps);
    expect(out).toContain("MSO-niveau");
  });
  it("renders nothing for an unknown level", () => {
    expect(html(<RiskChip risk={null} />)).toBe("");
  });
});

describe("InlineState", () => {
  it("success is neutral (check), not healthy", () => {
    const out = html(<InlineState tone="success">Klaar</InlineState>);
    expect(out).toContain("lucide-check");
    expect(out).not.toContain("healthy");
  });
});

// Review stap 5 (docs/ontwerp/PEIL_REVIEW.md): amber alleen voor gezondheid, ernst een eigen vorm, geen statusstip.
describe("InlineState — onbekend en verouderd zijn nooit amber", () => {
  it("unknown: stippel + circle-help", () => {
    const out = html(<InlineState tone="unknown">Prijs ontbreekt</InlineState>);
    expect(out).toContain("border-dashed");
    expect(out).toContain("lucide-circle-question-mark");
    expect(out).not.toContain("degraded");
  });
  it("stale: stippel + clock-alert", () => {
    const out = html(<InlineState tone="stale">Verouderd</InlineState>);
    expect(out).toContain("border-dashed");
    expect(out).toContain("lucide-clock-alert");
    expect(out).not.toContain("degraded");
  });
});

describe("ErnstLabel (T-20)", () => {
  it.each([["hoog", "Hoog"], ["middel", "Middel"], ["info", "Info"]] as const)("%s → %s met linkerbalk, geen pil", (ernst, woord) => {
    const out = html(<ErnstLabel ernst={ernst} />);
    expect(out).toContain(woord);
    expect(out).toContain("border-l-[6px]");
    expect(out).toContain("Ernst");
    expect(out).not.toContain("rounded-full");
    expect(out).not.toContain("data-status");
  });
});

describe("Dot — status nooit alleen kleur", () => {
  it("gezondheidstonen krijgen het Peil-icoon", () => {
    expect(html(<Dot tone="healthy" />)).toContain("lucide-circle-check");
    expect(html(<Dot tone="warning" />)).toContain("lucide-triangle-alert");
    expect(html(<Dot tone="danger" />)).toContain("lucide-octagon-x");
    expect(html(<Dot tone="unknown" />)).toContain("lucide-circle-question-mark");
  });
  it("een neutrale stip is nooit groen", () => {
    expect(html(<Dot tone="success" />)).not.toContain("healthy");
  });
});

describe("StatusChip stale (Verouderd)", () => {
  it("stippel + arcering + clock-alert, nooit amber", () => {
    const out = html(<StatusChip tone="stale">Verouderd</StatusChip>);
    expect(out).toContain('data-status="stale"');
    expect(out).toContain("border-dashed");
    expect(out).toContain("arcering");
    expect(out).toContain("lucide-clock-alert");
    expect(out).not.toContain("degraded");
  });
});
