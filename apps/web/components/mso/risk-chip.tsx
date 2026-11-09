"use client";

import { riskVariants } from "@/components/ui/badge";
import type { Risk } from "@/lib/api/contracts";
import { useI18n, type MessageKey } from "@/lib/i18n";

/**
 * Peil RisicoBadge (T-04, ONTWERPTAAL §4.5 en §13.4): MSO's three levels shown as R-classes. Only the display
 * changes — the API field `risk` and its meaning stay (ADR-0006 punt 4):
 *   advies → R0 · Alleen lezen · automatisch_veilig → R1 · Omkeerbaar (neutral, not green) ·
 *   toestemming_nodig → R2 · Goedkeuring nodig (violet).
 */
export const RISK: Record<Risk, { level: 0 | 1 | 2; label: MessageKey; code: MessageKey; word: MessageKey; help: MessageKey; mso: MessageKey }> = {
  advies: { level: 0, label: "risk.advies", code: "risk.code.advies", word: "risk.word.advies", help: "risk.adviesHelp", mso: "risk.mso.advies" },
  automatisch_veilig: { level: 1, label: "risk.automatisch_veilig", code: "risk.code.automatisch_veilig", word: "risk.word.automatisch_veilig", help: "risk.automatisch_veiligHelp", mso: "risk.mso.automatisch_veilig" },
  toestemming_nodig: { level: 2, label: "risk.toestemming_nodig", code: "risk.code.toestemming_nodig", word: "risk.word.toestemming_nodig", help: "risk.toestemming_nodigHelp", mso: "risk.mso.toestemming_nodig" },
};

/** Angular tag: code block with a step glyph (1–3 steps) + the fixed word; the MSO level stays as explanation. */
export function RiskChip({ risk, size }: { risk: Risk | null | undefined; size?: "lg" }) {
  const { t } = useI18n();
  if (!risk || !(risk in RISK)) return null;
  const r = RISK[risk];
  return (
    <span className={riskVariants({ level: r.level, size: size === "lg" ? "groot" : "standaard" })} data-risico={t(r.code)} title={`${t(r.mso)} — ${t(r.help)}`}>
      <span className="risk-code inline-flex items-center gap-1 px-1.5 py-0.5 font-mono">
        <span className="inline-flex h-3 items-end gap-px" aria-hidden="true">
          {Array.from({ length: r.level + 1 }, (_, i) => <i key={i} className="block w-[3px] bg-current" style={{ height: `${(i + 1) * 25}%` }} />)}
        </span>
        {t(r.code)}
      </span>
      <span className="sr-only"> · </span>
      <span className="risk-word inline-flex items-center px-2 py-0.5 break-words">{t(r.word)}</span>
    </span>
  );
}
