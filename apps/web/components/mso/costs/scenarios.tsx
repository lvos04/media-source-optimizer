"use client";

import { GitCompare } from "lucide-react";

import { Card, InlineState, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useResource } from "@/lib/api/hooks";
import type { ScenarioList } from "@/lib/api/insights";
import { useI18n } from "@/lib/i18n";

/** "Twee providers vs alternatieve backbone vs blockaccount" — GET /costs/scenarios (inactive services = candidates). */
export function Scenarios({ periodDays }: { periodDays: number }) {
  const { t, num, money, pct } = useI18n();
  const res = useResource<ScenarioList>("/costs/scenarios", { period_days: periodDays });
  return (
    <Card className="span-12" title={t("costs.scenarios")} sub={t("costs.scenariosSub")} icon={GitCompare} flush>
      <ResourceView resource={res} endpoint="GET /api/v1/costs/scenarios" compact
        isEmpty={(d) => !d.available || d.items.length === 0}
        empty={<StateView kind="empty" compact title={t("costs.scen.none")} body={res.data?.reason ?? undefined} />}>
        {(d) => (
          <>
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
              <table className="table table--stack">
                <thead><tr><th>Scenario</th><th>{t("costs.scen.monthly")}</th><th>{t("costs.scen.backbones")}</th><th>{t("costs.scen.observed")}</th></tr></thead>
                <tbody>{d.items.map((s) => (
                  <tr key={s.label}>
                    <td className="cell-primary">
                      <div>{s.label}</div>
                      <div className="t-3" style={{ fontSize: 12 }}>{s.services.join(" + ") || "—"}</div>
                      {s.notes.map((n) => <div key={n} className="hint" style={{ marginTop: 4, whiteSpace: "normal" }}>{n}</div>)}
                    </td>
                    <td data-label={t("costs.scen.monthly")}>{money(s.monthly_cents / 100)}{!s.monthly_complete ? <> <StatusChip tone="warning">{t("costs.scen.incomplete")}</StatusChip></> : null}</td>
                    <td data-label={t("costs.scen.backbones")}>
                      <div>{t("costs.scen.backbonesValue", { known: num(s.distinct_known_backbones), unknown: num(s.unknown_backbones) })}</div>
                      <div className="t-3" style={{ fontSize: 12 }}>{s.backbones.map((b) => `${b.label}: ${b.backbone ?? t("costs.backboneUnknown")}`).join(" · ")}</div>
                    </td>
                    <td data-label={t("costs.scen.observed")}>
                      <div>{t("costs.scen.within", { n: num(s.observed.fully_within_set) })}</div>
                      {s.observed.needed_excluded_provider ? <div className="t-2">{t("costs.scen.needed", { n: num(s.observed.needed_excluded_provider), pct: s.observed.needed_excluded_pct == null ? "—" : pct(s.observed.needed_excluded_pct * 100) })}</div> : null}
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <div style={{ padding: 16 }}>
              <InlineState tone="info"><div className="stack-sm">{d.disclaimers.map((x) => <span key={x}>{x}</span>)}</div></InlineState>
            </div>
          </>
        )}
      </ResourceView>
    </Card>
  );
}
