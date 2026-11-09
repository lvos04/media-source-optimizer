"use client";

import { BarChart3 } from "lucide-react";

import { Card, Segmented } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { ConfidenceChip } from "@/components/mso/costs/simulation-panel";
import { useResource } from "@/lib/api/hooks";
import type { Analysis } from "@/lib/api/insights";
import { useI18n } from "@/lib/i18n";

export const PERIODS = ["30", "90", "180", "365"] as const;
export type Period = (typeof PERIODS)[number];

export function PeriodPicker({ value, onChange }: { value: Period; onChange: (p: Period) => void }) {
  const { t } = useI18n();
  return <Segmented label={t("costs.period")} value={value} onChange={onChange} options={PERIODS.map((p) => ({ value: p, label: t("costs.days", { count: p }) }))} />;
}

export function ServiceAnalysis({ serviceId, label, periodDays, className = "span-7" }: { serviceId: number; label: string; periodDays: number; className?: string }) {
  const { t, num, money, bytes, date } = useI18n();
  const res = useResource<Analysis>(`/costs/services/${serviceId}/analysis`, { period_days: periodDays });
  return (
    <Card className={className} title={t("costs.analysis", { label })} icon={BarChart3}>
      <ResourceView resource={res} endpoint="GET /api/v1/costs/services/{id}/analysis" compact>
        {(a) => {
          const cur = a.service.currency;
          const c = (v: number | null | undefined) => (v == null ? "—" : money(v / 100, cur));
          return (
            <div className="stack">
              {a.reason ? <p className="hint" style={{ margin: 0 }}>{a.reason}</p> : null}
              <dl className="kv">
                <dt>{t("costs.a.perMonth")}</dt><dd>{c(a.cost.monthly_cents)} {a.cost.reason ? <span className="t-3">· {a.cost.reason}</span> : <span className="t-3">· {a.cost.method}</span>}</dd>
                <dt>{t("costs.a.perSuccess")}</dt><dd>{c(a.cost.per_success_cents)}</dd>
                <dt>{t("costs.a.perGb")}</dt><dd>{c(a.cost.per_gb_cents)}</dd>
                <dt>{t("costs.a.data")}</dt><dd>{bytes(a.usage.bytes)} <span className="t-3">· {a.usage.bytes_method}</span></dd>
                <dt>{t("costs.a.successes")}</dt><dd className="num">{num(a.usage.successful_downloads)}</dd>
                <dt>{t("costs.a.failures")}</dt><dd className="num">{num(a.usage.failed_downloads)}</dd>
                <dt>{t("costs.a.unique")}</dt>
                <dd>
                  <span className="num">{a.unique.value == null ? "—" : num(a.unique.value)}</span>
                  {a.unique.range ? <span className="t-3"> · {t("costs.a.uniqueRange", { low: num(a.unique.range.low), high: num(a.unique.range.high) })}</span> : null}{" "}
                  <ConfidenceChip value={a.unique.confidence} />
                  <div className="hint" style={{ marginTop: 4 }}>{a.unique.note}</div>
                </dd>
                {a.unique.articles ? (
                  <>
                    <dt>{t("costs.a.articles")}</dt>
                    <dd>
                      <span className="mono">{Object.entries(a.unique.articles).map(([k, v]) => `${k}: ${v === null ? "—" : String(v)}`).join(" · ")}</span>
                      {a.unique.articles_note ? <div className="hint" style={{ marginTop: 4 }}>{a.unique.articles_note}</div> : null}
                    </dd>
                  </>
                ) : null}
                <dt>{t("costs.a.latency")}</dt>
                <dd>{a.latency.available ? t("costs.a.latencyValue", { p50: num(a.latency.p50_ms), p95: num(a.latency.p95_ms), n: num(a.latency.n) }) : <span className="t-3">{a.latency.reason ?? "—"}</span>}
                  {a.latency.note ? <div className="hint" style={{ marginTop: 4 }}>{a.latency.note}</div> : null}</dd>
                <dt>{t("costs.a.recovered")}</dt><dd className="num">{a.recovered.available ? num(a.recovered.count) : "—"}</dd>
                {a.block ? (
                  <>
                    <dt>{t("costs.a.block")}</dt>
                    <dd>{a.block.remaining_gb == null ? "—" : t("costs.a.blockValue", { remaining: num(a.block.remaining_gb, 1), size: num(a.block.size_gb, 1) })}
                      {a.block.est_months_left != null ? <span className="t-3"> · {t("costs.a.monthsLeft", { months: num(a.block.est_months_left, 1) })}</span> : null}
                      <div className="hint" style={{ marginTop: 4 }}>{a.block.note}</div></dd>
                  </>
                ) : null}
                {a.renewal ? <><dt>{t("costs.a.renewal")}</dt><dd>{t("costs.a.renewalValue", { date: date(a.renewal.date), days: num(a.renewal.days_left) })}</dd></> : null}
              </dl>
              <div>
                <div className="label">{t("costs.a.failuresByReason")}</div>
                {a.failures_by_reason.length === 0 ? <p className="hint" style={{ margin: 0 }}>{t("costs.a.noFailures")}</p> : (
                  <ul style={{ margin: 0, paddingLeft: 18 }}>{a.failures_by_reason.map((f) => <li key={f.reason}>{f.label}: <strong className="num">{num(f.count)}</strong></li>)}</ul>
                )}
              </div>
            </div>
          );
        }}
      </ResourceView>
    </Card>
  );
}
