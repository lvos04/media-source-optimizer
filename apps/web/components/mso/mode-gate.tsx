"use client";

import { Eye, Settings } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { InlineState } from "@/components/mg/primitives";
import type { Role } from "@/lib/api/contracts";
import { useI18n } from "@/lib/i18n";
import { useWriteGate, type WriteGate } from "@/lib/mode";

export { useMode, useWriteGate } from "@/lib/mode";

/**
 * Gate a block of mutating UI on role + operating mode.
 *
 * Render-prop form (preferred — keeps the control visible, disabled with a reason):
 *   <ModeGate>{(gate) => <Button disabled={!gate.allowed} title={gate.reason}>Toepassen</Button>}</ModeGate>
 *
 * Children form: renders children when allowed, otherwise an explanatory InlineState
 * (or `fallback` when given).
 */
export function ModeGate({ children, minRole = "operator", requireManage = true, fallback }: {
  children: ReactNode | ((gate: WriteGate) => ReactNode); minRole?: Role; requireManage?: boolean; fallback?: ReactNode;
}) {
  const gate = useWriteGate(minRole, requireManage);
  const { t } = useI18n();
  if (typeof children === "function") return <>{children(gate)}</>;
  if (gate.allowed) return <>{children}</>;
  if (fallback !== undefined) return <>{fallback}</>;
  return (
    <InlineState tone="info" icon={Eye}>
      <strong>{t("mode.gateTitle")}</strong> {gate.reason}{" "}
      <Link href="/instellingen?tab=systeem#modus" className="link-more" style={{ display: "inline-flex" }}><Settings className="ico ico-sm" aria-hidden="true" />{t("mode.gateLink")}</Link>
    </InlineState>
  );
}
