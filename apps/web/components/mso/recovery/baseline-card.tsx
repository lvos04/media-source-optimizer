"use client";

import { BarChart3 } from "lucide-react";
import { useState } from "react";

import { Confidence, DistributionBar } from "@/components/mg/domain";
import { Card, InlineState, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useResource } from "@/lib/api/hooks";
import type { Baseline, BaselineCounts } from "@/lib/api/recovery";
import { useI18n } from "@/lib/i18n";

/** Fixed colour per cause so the same cause has the same colour in every bar; 'unknown' stays neutral. */
const COLORS: Record<string, string> = {
  no_indexer_hit: "var(--mg-viz-1)", false_positive: "var(--mg-viz-2)", rejected_quality: "var(--mg-viz-3)",
  no_retention: "var(--mg-viz-4)", incomplete: "var(--mg-viz-5)", client_failure: "var(--mg-viz-6)",
  import_failure: "var(--mg-danger)", other: "var(--mg-text-3)", unknown: "var(--mg-border)",
};
const CATEGORY_KEY = { tv: "rec.cat.tv", "tv-anime": "rec.cat.tvAnime", movies: "rec.cat.movies", "movies-anime": "rec.cat.moviesAnime", onbekend: "rec.cat.unknown" } as const;

/** Dutch label for tv | tv-anime | movies | movies-anime. */
export function useCategoryLabel(): (cat: string | null | undefined) => string {
  const { t } = useI18n();
  return (cat) => t(CATEGORY_KEY[(cat ?? "onbekend") as keyof typeof CATEGORY_KEY] ?? CATEGORY_KEY.onbekend);
}

function segments(b: Baseline, counts: BaselineCounts) {
  return b.buckets.map((x) => ({ key: x.key, label: x.label, value: counts[x.key] ?? 0, color: COLORS[x.key] }))
    .filter((s) => s.value > 0);
}

export function BaselineCard({ className = "span-12" }: { className?: string }) {
  const { t, num, pct } = useI18n();
  const categoryLabel = useCategoryLabel();
  const [days, setDays] = useState<"7" | "30" | "90">("30");
  const baseline = useResource<Baseline>("/recovery/baseline", { days }, 120_000);
  const total = (c: BaselineCounts) => Object.values(c).reduce((a, b) => a + b, 0);
  return (
    <Card className={className} title={t("rec.baseline.title")} icon={BarChart3} sub={t("rec.baseline.sub")}
      actions={<Segmented label={t("rec.baseline.window")} value={days} onChange={setDays}
        options={[{ value: "7", label: t("rec.days", { n: 7 }) }, { value: "30", label: t("rec.days", { n: 30 }) }, { value: "90", label: t("rec.days", { n: 90 }) }]} />}>
      <ResourceView resource={baseline} endpoint="GET /api/v1/recovery/baseline"
        isEmpty={(d) => total(d.totals) === 0}
        empty={<StateView kind="empty" title={t("rec.baseline.empty")} body={t("rec.baseline.emptyBody")} />}>
        {(d) => (
          <div className="stack">
            <div className="row wrap" style={{ gap: 8 }}>
              <StatusChip tone={d.coverage.confidence === "hoog" ? "success" : d.coverage.confidence === "middel" ? "info" : "warning"}>
                {t("rec.baseline.confidence", { level: d.coverage.confidence })}
              </StatusChip>
              <span className="hint" style={{ margin: 0 }}>
                {t("rec.baseline.coverage", {
                  known: d.coverage.known_share != null ? pct(d.coverage.known_share * 100) : "—",
                  downloads: num(d.coverage.failed_downloads), items: num(d.coverage.mediaguard_items),
                })}
              </span>
              {d.coverage.known_share != null ? <Confidence value={d.coverage.known_share} tone={d.coverage.known_share >= 0.5 ? "success" : "warning"} /> : null}
            </div>
            <DistributionBar segments={segments(d, d.totals)} />
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
              <table className="table table--stack">
                <thead><tr><th>{t("rec.baseline.category")}</th><th>{t("rec.baseline.total")}</th><th style={{ width: "60%" }}>{t("rec.baseline.causes")}</th></tr></thead>
                <tbody>
                  {Object.entries(d.by_category).map(([cat, counts]) => (
                    <tr key={cat}>
                      <td data-label={t("rec.baseline.category")}>{categoryLabel(cat)}</td>
                      <td data-label={t("rec.baseline.total")} className="num">{num(total(counts))}</td>
                      <td data-label={t("rec.baseline.causes")}>{total(counts) ? <DistributionBar segments={segments(d, counts)} /> : <span className="t-3">{t("rec.baseline.none")}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {d.by_source.length ? (
              <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
                <table className="table table--stack">
                  <thead><tr><th>{t("rec.baseline.source")}</th><th>{t("rec.baseline.total")}</th><th style={{ width: "60%" }}>{t("rec.baseline.causes")}</th></tr></thead>
                  <tbody>
                    {d.by_source.slice(0, 15).map((s) => (
                      <tr key={s.source_id}>
                        <td data-label={t("rec.baseline.source")}>{s.label}<div className="hint mono">{s.source_id}</div></td>
                        <td data-label={t("rec.baseline.total")} className="num">{num(s.total)}</td>
                        <td data-label={t("rec.baseline.causes")}><DistributionBar segments={segments(d, s.counts)} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            {Object.keys(d.excluded_mediaguard).length ? (
              <p className="hint">{t("rec.baseline.excluded", { list: Object.entries(d.excluded_mediaguard).map(([k, v]) => `${k} (${num(v)})`).join(", ") })}</p>
            ) : null}
            <InlineState tone="info">{d.note}</InlineState>
          </div>
        )}
      </ResourceView>
    </Card>
  );
}
