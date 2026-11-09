import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { InlineState, StatusChip, TONE_ICON, type Tone } from "@/components/mg/primitives";
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
