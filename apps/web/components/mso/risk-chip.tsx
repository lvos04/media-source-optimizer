"use client";

import { CircleCheck, Info, ShieldAlert } from "lucide-react";

import { StatusChip, type Tone } from "@/components/mg/primitives";
import type { Risk } from "@/lib/api/contracts";
import { useI18n, type MessageKey } from "@/lib/i18n";

export const RISK: Record<Risk, { tone: Tone; label: MessageKey; help: MessageKey; icon: typeof Info }> = {
  advies: { tone: "info", label: "risk.advies", help: "risk.adviesHelp", icon: Info },
  automatisch_veilig: { tone: "success", label: "risk.automatisch_veilig", help: "risk.automatisch_veiligHelp", icon: CircleCheck },
  toestemming_nodig: { tone: "warning", label: "risk.toestemming_nodig", help: "risk.toestemming_nodigHelp", icon: ShieldAlert },
};

/**
 * Risk level of a setting, advice or change set:
 * advies → info "Advies", automatisch_veilig → success "Automatisch veilig",
 * toestemming_nodig → warning "Toestemming nodig". Unknown/null renders nothing.
 */
export function RiskChip({ risk, size }: { risk: Risk | null | undefined; size?: "lg" }) {
  const { t } = useI18n();
  if (!risk || !(risk in RISK)) return null;
  const r = RISK[risk];
  return <StatusChip tone={r.tone} icon={r.icon} size={size} title={t(r.help)}>{t(r.label)}</StatusChip>;
}
