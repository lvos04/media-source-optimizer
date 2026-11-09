"use client";

import { Activity, Gauge } from "lucide-react";
import { useState } from "react";

import { Button, Card, InlineState, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { RiskChip } from "@/components/mso/risk-chip";
import { useResource } from "@/lib/api/hooks";
import type { ChangeEvaluation, EvaluationMeasure, StoredEvaluation } from "@/lib/api/insights";
import { useI18n, type Formatters } from "@/lib/i18n";

type Tone = "success" | "danger" | "warning" | "neutral" | "accent";
/** Outcome (incl. legacy values stored before 1.4.0) → tone + Dutch label. */
const OUTCOME: Record<string, { tone: Tone; label: string }> = {
  beter: { tone: "success", label: "Beter" },
  gelijk: { tone: "neutral", label: "Gelijk" },
  slechter: { tone: "danger", label: "Slechter" },
  onvoldoende: { tone: "warning", label: "Onvoldoende data" },
  niet_van_toepassing: { tone: "neutral", label: "Niet van toepassing" },
  keep: { tone: "neutral", label: "Behouden" },
  rollback: { tone: "danger", label: "Slechter" },
  unknown: { tone: "warning", label: "Onvoldoende data" },
  niet_beschikbaar: { tone: "neutral", label: "Niet beschikbaar" },
};
const ACTION: Record<string, string> = {
  behouden: "Wijziging blijft staan", teruggedraaid: "Automatisch teruggedraaid", voorgesteld: "Terugdraaien voorgesteld",
  geen: "Geen actie", terugdraaien_mislukt: "Terugdraaien mislukt", overgeslagen: "Niet teruggedraaid (buiten MSO gewijzigd)",
};

export function outcomeChip(result: string | undefined, interim = false) {
  const o = OUTCOME[result ?? ""] ?? { tone: "neutral" as Tone, label: result ?? "—" };
  return <StatusChip tone={o.tone}>{interim ? `Voorlopig: ${o.label.toLowerCase()}` : o.label}</StatusChip>;
}

function fmtValue(m: EvaluationMeasure, v: number | null, f: Formatters): string {
  if (v === null || v === undefined) return "—";
  if (m.unit === "status") return v >= 1 ? f.t("safety.eval.works") : f.t("safety.eval.fails");
  if (m.unit === "ratio") return f.pct(v * 100, 1);
  if (m.unit === "s") return f.duration(v);
  if (m.unit === "B/s") return `${f.bytes(v)}/s`;
  return f.num(v, Number.isInteger(v) ? 0 : 2);
}

/** Baseline vs now per measure, with sample counts and the hysteresis used. */
export function MeasuresTable({ measures }: { measures: EvaluationMeasure[] }) {
  const f = useI18n();
  if (!measures.length) return null;
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Metingen (horizontaal scrollbaar)">
      <table className="table table--compact table--stack">
        <thead><tr><th>Maatstaf</th><th>Basislijn</th><th>Nu</th><th>Verschil</th><th>Uitkomst</th></tr></thead>
        <tbody>
          {measures.map((m) => (
            <tr key={m.key}>
              <td className="cell-primary">
                {m.label}
                <div className="cell-sub">{m.higher_is_better ? "hoger is beter" : "lager is beter"}{m.scope ? ` · ${m.scope}` : ""}</div>
              </td>
              <td data-label="Basislijn" className="num">{fmtValue(m, m.baseline.value, f)} <span className="t-3">(n={f.num(m.baseline.n)})</span></td>
              <td data-label="Nu" className="num">{fmtValue(m, m.current.value, f)} <span className="t-3">(n={f.num(m.current.n)})</span></td>
              <td data-label="Verschil" className="num">{m.delta_pct === null ? "—" : `${m.delta_pct > 0 ? "+" : ""}${f.num(m.delta_pct, 1)}%`}{m.hysteresis_pct ? <div className="cell-sub">hysterese ±{f.num(m.hysteresis_pct)}%</div> : null}</td>
              <td data-label="Uitkomst">{outcomeChip(m.outcome ?? ({ better: "beter", same: "gelijk", worse: "slechter", insufficient: "onvoldoende" } as Record<string, string>)[m.verdict])}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Stored outcome, or the live interim comparison while the measurement period runs. */
export function EvaluationSummary({ item }: { item: ChangeEvaluation }) {
  const { t, dateTime, relative } = useI18n();
  const ev: StoredEvaluation | null = item.evaluation ?? item.interim;
  const interim = !item.evaluation && !!item.interim;
  // rolled back before it was measured: nothing left to measure (backend sends rolled_back; older backends send nothing)
  if (item.status === "rolled_back" && (!ev || ev.rolled_back)) {
    return (
      <div className="stack-sm">
        <div className="row wrap" style={{ gap: 8 }}>
          <StatusChip tone="neutral">n.v.t. (teruggedraaid)</StatusChip>
          {item.rolled_back_at ? <span className="t-3" style={{ fontSize: 12 }}>teruggedraaid {relative(item.rolled_back_at)}</span> : null}
        </div>
        <p className="t-2" style={{ margin: 0, fontSize: 13 }}>{ev?.message ?? "Deze wijziging is teruggedraaid voordat de meetperiode om was; er valt niets meer te meten."}</p>
      </div>
    );
  }
  if (!ev) return <InlineState tone="neutral">Nog niet gemeten.</InlineState>;
  return (
    <div className="stack-sm">
      <div className="row wrap" style={{ gap: 8 }}>
        {outcomeChip(ev.result, interim)}
        {ev.action ? <span className="t-2" style={{ fontSize: 12.5 }}>{ACTION[ev.action] ?? ev.action}</span> : null}
        {interim && item.evaluate_at ? <span className="t-3" style={{ fontSize: 12 }}>eindoordeel {relative(item.evaluate_at)} ({dateTime(item.evaluate_at)})</span> : null}
        {!interim && ev.at ? <span className="t-3" style={{ fontSize: 12 }}>gemeten {relative(ev.at)}</span> : null}
      </div>
      {ev.message ? <p className="t-2" style={{ margin: 0, fontSize: 13 }}>{ev.message}</p> : null}
      <MeasuresTable measures={ev.measures ?? []} />
      {item.applicable && item.status === "applied" && (item as ChangeEvaluation & { reversible?: boolean }).reversible === false ? (
        <p className="hint" style={{ margin: 0 }}>{t("safety.eval.notReversible")}</p>
      ) : item.applicable && item.status === "applied" && (ev.measures ?? []).some((m) => m.unit === "status") ? (
        <p className="hint" style={{ margin: 0 }}>{t("safety.eval.serverStatus")}</p>
      ) : item.applicable && item.status === "applied" && item.auto_rollback.reason ? (
        <p className="hint" style={{ margin: 0 }}>{t("safety.eval.neverAuto", { reason: item.auto_rollback.reason })}</p>
      ) : item.applicable && item.status === "applied" && ev.result !== "niet_van_toepassing" ? (
        <p className="hint" style={{ margin: 0 }}>
          Bij ‘slechter’: {item.auto_rollback.enabled ? "automatisch terugdraaien staat aan" : "automatisch terugdraaien staat uit — er komt een voorstel"} (<span className="mono">{item.auto_rollback.setting}</span>).
          {ev.evaluator === "generiek" ? " Gemeten met de algemene controle (downloaduitkomsten)." : ""}
        </p>
      ) : null}
    </div>
  );
}

const FILTERS = ["alle", "slechter", "lopend"] as const;
type Filter = (typeof FILTERS)[number];
const FILTER_LABEL: Record<Filter, string> = { alle: "Alle", lopend: "Lopend", slechter: "Slechter" };

/** Automatisering → Controle: per applied change the outcome with baseline vs now numbers. */
export function EvaluationsPanel({ onOpenChange }: { onOpenChange: (id: number) => void }) {
  const { relative } = useI18n();
  const [filter, setFilter] = useState<Filter>("alle");
  const list = useResource<{ items: ChangeEvaluation[] }>("/automation/evaluations", { limit: 50 }, 60_000);
  const visible = (items: ChangeEvaluation[]) => items.filter((i) =>
    filter === "alle" ? true
      : filter === "lopend" ? !i.evaluation && i.status === "applied"
        : (i.evaluation?.result ?? "") === "slechter" || i.evaluation?.result === "rollback");
  return (
    <Card className="span-12" title="Controle na een wijziging" icon={Gauge} flush
      sub="Na de meetperiode vergelijkt MSO met de basislijn (dezelfde periode vóór het toepassen)"
      actions={<Segmented label="Filter" value={filter} onChange={setFilter} options={FILTERS.map((v) => ({ value: v, label: FILTER_LABEL[v] }))} />}>
      <ResourceView resource={list} endpoint="GET /api/v1/automation/evaluations" isEmpty={(d) => visible(d.items).length === 0}
        empty={<StateView kind="empty" compact title={filter === "alle" ? "Nog geen toegepaste wijzigingen" : "Geen wijzigingen met dit filter"}
          body={filter === "alle" ? "Zodra een wijziging is toegepast, verschijnt hier de controle: beter, gelijk, slechter of onvoldoende data." : undefined} />}>
        {(d) => (
          <div className="list">
            {visible(d.items).map((i) => (
              <div className="list-row" key={i.id} style={{ alignItems: "flex-start" }}>
                <div className="list-main" style={{ minWidth: 0 }}>
                  <div className="list-title" style={{ whiteSpace: "normal" }}>
                    <Activity className="ico" aria-hidden="true" style={{ width: 14, height: 14, marginRight: 6, verticalAlign: -2 }} />
                    {i.title}
                  </div>
                  <div className="list-sub mono">#{i.id} · {i.target} · toegepast {relative(i.applied_at)}{i.status === "rolled_back" ? " · teruggedraaid" : ""}</div>
                  <div style={{ marginTop: 8 }}><EvaluationSummary item={i} /></div>
                </div>
                <div className="list-aside" style={{ flexDirection: "column", alignItems: "flex-end", gap: 6 }}>
                  <RiskChip risk={i.risk} />
                  <Button size="sm" onClick={() => onOpenChange(i.id)}>Wijziging bekijken</Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </ResourceView>
    </Card>
  );
}
