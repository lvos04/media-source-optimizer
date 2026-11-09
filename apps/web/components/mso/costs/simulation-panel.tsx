"use client";

import { ShieldQuestion } from "lucide-react";

import { Card, InlineState, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { TableToggle } from "@/components/mso/charts/chart-frame";
import { useResource } from "@/lib/api/hooks";
import type { ConfidenceLabel, Simulation } from "@/lib/api/insights";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { cn } from "@/lib/utils";

// certainty is not health (Peil T-24): neutral labels; "onbekend" uses the unknown shape
export const CONF_TONE: Record<ConfidenceLabel, Tone> = { onbekend: "unknown", laag: "neutral", middel: "neutral", hoog: "neutral" };
export function ConfidenceChip({ value }: { value: ConfidenceLabel | null | undefined }) {
  const { t } = useI18n();
  const v = value ?? "onbekend";
  return <StatusChip tone={CONF_TONE[v]}>{t("costs.confidence")}: {t(`costs.conf.${v}` as MessageKey)}</StatusChip>;
}

/**
 * Uncertainty bar (Peil §7): thin segment bar — certainly affected = chart-1, uncertain = chart-1 with the "geschat"
 * hatching, unaffected = meter track; no red/amber. The bar is aria-hidden: the legend and the table twin carry the
 * numbers. Never a single point estimate.
 */
export function UncertaintyBar({ low, high, total }: { low: number; high: number; total: number }) {
  const { t, num, pct } = useI18n();
  const w = (n: number) => (total ? `${(n / total) * 100}%` : "0%");
  const seg = [
    { key: "certain", n: low, cls: "bg-chart-1", label: t("costs.sim.legend.certain") },
    { key: "uncertain", n: Math.max(0, high - low), cls: "text-chart-1 arcering-geschat border border-chart-1", label: t("costs.sim.legend.uncertain") },
    { key: "rest", n: Math.max(0, total - high), cls: "bg-chart-meter-track", label: t("costs.sim.legend.unaffected") },
  ];
  return (
    <div>
      <p className="sr-only">{t("costs.sim.ariaBar", { low: num(low), high: num(high), total: num(total) })}</p>
      <div className="stackbar flex h-3 gap-0.5 overflow-hidden rounded-full" aria-hidden="true">
        {seg.map((s) => <span key={s.key} className={cn("h-full", s.cls)} style={{ width: w(s.n) }} />)}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3.5 gap-y-1.5 text-sm text-muted-foreground">
        {seg.map((s) => <span className="inline-flex items-center gap-1.5" key={s.key}><span className={cn("inline-block size-3 rounded-sm border border-border", s.cls)} aria-hidden="true" />{s.label} <strong className="cijfers text-foreground">{num(s.n)}</strong></span>)}
      </div>
      <TableToggle testid="grafiek-onzekerheid-tabel" table={{
        caption: t("costs.sim.tableCaption"),
        columns: [t("costs.sim.colPart"), t("costs.sim.colCount"), t("costs.sim.colShare")],
        numeric: [false, true, true],
        rows: seg.map((s) => ({ key: s.key, cells: [s.label, num(s.n), total ? pct((s.n / total) * 100, 0) : "—"] })),
      }} />
    </div>
  );
}

export function SimulationPanel({ serviceId, periodDays, className = "span-5" }: { serviceId: number; periodDays: number; className?: string }) {
  const { t, num, pct, money } = useI18n();
  const sim = useResource<Simulation>(`/costs/services/${serviceId}/simulate`, { period_days: periodDays });
  return (
    <Card className={className} title={t("costs.sim.title")} sub={t("costs.sim.sub")} icon={ShieldQuestion}>
      <ResourceView resource={sim} endpoint="GET /api/v1/costs/services/{id}/simulate" compact>
        {(s) => (
          <div className="stack">
            {!s.available ? (
              <StateView kind="unavailable" compact title={t("state.unavailableTitle")} body={s.reason} />
            ) : (
              <>
                <div className="row-between" style={{ flexWrap: "wrap" }}>
                  <strong style={{ fontSize: 14 }}>{t("costs.sim.affected", { low: num(s.affected_range?.low), high: num(s.affected_range?.high), total: num(s.observed_successes) })}</strong>
                  <ConfidenceChip value={s.confidence} />
                </div>
                <UncertaintyBar low={s.affected_range?.low ?? 0} high={s.affected_range?.high ?? 0} total={s.observed_successes ?? 0} />
                {s.coverage ? <p className="hint" style={{ margin: 0 }}>{t("costs.sim.coverage", { pct: s.coverage.ratio == null ? "—" : pct(s.coverage.ratio * 100), n: num(s.coverage.with_evidence), total: num(s.coverage.total) })}</p> : null}
                <div className="list" style={{ border: "1px solid var(--mg-border-soft)", borderRadius: 8 }}>
                  {(s.buckets ?? []).map((b) => (
                    <div className="list-row" key={b.key}>
                      <div className="list-main"><div className="list-title">{b.label}</div><div className="list-sub" style={{ whiteSpace: "normal" }}>{b.explanation}</div></div>
                      <strong className="num">{num(b.count)}</strong>
                    </div>
                  ))}
                </div>
                {s.examples?.length ? (
                  <details>
                    <summary className="t-2" style={{ cursor: "pointer" }}>{t("costs.sim.examples")} ({num(s.examples.length)})</summary>
                    <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                      {s.examples.map((e, i) => <li key={i}><span className="mono">{e.title}</span>{e.also_from?.length ? ` · ${e.also_from.join(", ")}` : ""}</li>)}
                    </ul>
                  </details>
                ) : null}
                {(s.method_notes ?? []).map((n) => <p key={n} className="hint" style={{ margin: 0 }}>{n}</p>)}
              </>
            )}
            {s.entered_cost ? (
              <dl className="kv"><dt>{t("costs.sim.cost")}</dt><dd>{s.entered_cost.monthly_cents == null ? "—" : `${money(s.entered_cost.monthly_cents / 100, s.entered_cost.currency)} / mnd`} <span className="t-3">· {s.entered_cost.note}</span></dd></dl>
            ) : null}
            <InlineState tone="info">
              <div className="stack-sm">
                {s.disclaimers.map((d) => <span key={d}>{d}</span>)}
                <strong>{t("costs.sim.noAutoCancel")}</strong>
              </div>
            </InlineState>
          </div>
        )}
      </ResourceView>
    </Card>
  );
}
