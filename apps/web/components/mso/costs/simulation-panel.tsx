"use client";

import { ShieldQuestion } from "lucide-react";

import { Card, InlineState, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useResource } from "@/lib/api/hooks";
import type { ConfidenceLabel, Simulation } from "@/lib/api/insights";
import { useI18n, type MessageKey } from "@/lib/i18n";

// certainty is not health (Peil T-24): neutral labels; "onbekend" uses the unknown shape
export const CONF_TONE: Record<ConfidenceLabel, Tone> = { onbekend: "unknown", laag: "neutral", middel: "neutral", hoog: "neutral" };
export function ConfidenceChip({ value }: { value: ConfidenceLabel | null | undefined }) {
  const { t } = useI18n();
  const v = value ?? "onbekend";
  return <StatusChip tone={CONF_TONE[v]}>{t("costs.confidence")}: {t(`costs.conf.${v}` as MessageKey)}</StatusChip>;
}

/** Three-part bar: lower bound (no alternative observed) · uncertainty · rest. Never a single point estimate. */
export function UncertaintyBar({ low, high, total }: { low: number; high: number; total: number }) {
  const { t, num } = useI18n();
  const w = (n: number) => (total ? `${(n / total) * 100}%` : "0%");
  const seg = [
    { key: "certain", n: low, bg: "var(--mg-danger)", label: t("costs.sim.legend.certain") },
    { key: "uncertain", n: Math.max(0, high - low), bg: "repeating-linear-gradient(45deg, var(--mg-warning) 0 6px, color-mix(in srgb, var(--mg-warning) 45%, transparent) 6px 12px)", label: t("costs.sim.legend.uncertain") },
    { key: "rest", n: Math.max(0, total - high), bg: "var(--mg-raised)", label: t("costs.sim.legend.unaffected") },
  ];
  return (
    <div>
      <div className="stackbar" role="img" aria-label={t("costs.sim.ariaBar", { low: num(low), high: num(high), total: num(total) })} style={{ height: 12 }}>
        {seg.map((s) => <span key={s.key} style={{ width: w(s.n), background: s.bg }} />)}
      </div>
      <div className="legend" style={{ marginTop: 8 }}>
        {seg.map((s) => <span className="legend-item" key={s.key}><span className="legend-swatch" style={{ background: s.bg }} />{s.label} <strong className="num">{num(s.n)}</strong></span>)}
      </div>
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
                    <summary className="t-2" style={{ cursor: "pointer", fontSize: 12.5 }}>{t("costs.sim.examples")} ({num(s.examples.length)})</summary>
                    <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 12.5 }}>
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
