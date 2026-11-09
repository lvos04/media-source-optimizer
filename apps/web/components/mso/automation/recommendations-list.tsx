"use client";

import { Check, Lightbulb, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Confidence } from "@/components/mg/domain";
import { Button, Card, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { RiskChip } from "@/components/mso/risk-chip";
import { useAction } from "@/lib/api/actions";
import type { Risk } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { Recommendation } from "@/lib/api/insights";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { CHANGE_STATUS } from "@/lib/status";

const STATUSES = ["open", "accepted", "dismissed", "all"] as const;
const AREAS = ["", "usenet", "torrent", "indexers", "recovery", "mediaguard", "costs", "automation", "system"] as const;
const RISKS: Array<Risk | ""> = ["", "advies", "automatisch_veilig", "toestemming_nodig"];
export const areaLabel = (a: string): MessageKey => `automation.area.${a}` as MessageKey;

/** Recommendations inbox: GET /automation/recommendations, accept/dismiss (operator). Accepting never applies. */
export function RecommendationsList({ onOpenChange }: { onOpenChange: (changeId: number) => void }) {
  const { t, relative } = useI18n();
  const { can } = useSession();
  const canAct = can("operator");
  const [status, setStatus] = useState<(typeof STATUSES)[number]>("open");
  const [area, setArea] = useState<string>("");
  const [risk, setRisk] = useState<string>("");
  const recs = useResource<{ items: Recommendation[] }>("/automation/recommendations", { status, area: area || undefined, risk: risk || undefined }, 60_000);
  const { run, pending } = useAction();
  // deep link from "Wacht op jou": ?tab=aanbevelingen&rec=<id> → that row is marked and scrolled into view
  const [focusRec, setFocusRec] = useState<number | null>(null);
  useEffect(() => {
    const n = Number(new URLSearchParams(window.location.search).get("rec"));
    if (Number.isInteger(n) && n > 0) setFocusRec(n);
  }, []);
  const loaded = !!recs.data;
  useEffect(() => {
    if (focusRec != null && loaded) document.getElementById(`rec-${focusRec}`)?.scrollIntoView({ block: "center" });
  }, [focusRec, loaded]);

  const act = async (r: Recommendation, what: "accept" | "dismiss") => {
    const res = await run<Recommendation>(`${what}-${r.id}`, "post", `/automation/recommendations/${r.id}/${what}`, what === "dismiss" ? {} : undefined,
      what === "dismiss" ? t("automation.dismissed") : r.can_create_change || r.change ? t("automation.acceptedChange") : t("automation.accepted"));
    if (res) void recs.reload();
  };

  return (
    <Card className="span-12" title={t("automation.tab.recommendations")} icon={Lightbulb} flush
      actions={
        <div className="toolbar">
          <Segmented label={t("automation.filter.status")} value={status} onChange={setStatus}
            options={STATUSES.map((s) => ({ value: s, label: s === "all" ? t("automation.filter.all") : t(`automation.status.${s}` as MessageKey) }))} />
          <select className="select input--sm" aria-label={t("automation.filter.area")} value={area} onChange={(e) => setArea(e.target.value)} style={{ width: "auto" }}>
            {AREAS.map((a) => <option key={a} value={a}>{a ? t(areaLabel(a)) : `${t("automation.filter.area")}: ${t("automation.filter.all")}`}</option>)}
          </select>
          <select className="select input--sm" aria-label={t("automation.filter.risk")} value={risk} onChange={(e) => setRisk(e.target.value)} style={{ width: "auto" }}>
            {RISKS.map((r) => <option key={r} value={r}>{r ? t(`risk.${r}` as MessageKey) : `${t("automation.filter.risk")}: ${t("automation.filter.all")}`}</option>)}
          </select>
        </div>
      }>
      <ResourceView resource={recs} endpoint="GET /api/v1/automation/recommendations" isEmpty={(d) => d.items.length === 0}
        empty={<StateView kind="empty" compact title={t("automation.noRecommendations")} />}>
        {(d) => (
          <div className="list">
            {d.items.map((r) => (
              <div className="list-row list-row--wrap" key={r.id} id={`rec-${r.id}`} aria-current={r.id === focusRec ? "true" : undefined}
                style={{ alignItems: "flex-start", ...(r.id === focusRec ? { boxShadow: "inset 3px 0 0 var(--mg-accent)", background: "var(--mg-accent-tint)" } : {}) }}>
                <div className="list-main">
                  <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <RiskChip risk={r.risk} />
                    <StatusChip tone="neutral" icon={null}>{t(areaLabel(r.area))}</StatusChip>
                    {r.status !== "open" ? <StatusChip tone="neutral">{t(`automation.status.${r.status}` as MessageKey)}</StatusChip> : null}
                    <span className="t-3">{relative(r.updated_at)}</span>
                  </div>
                  <div className="list-title" style={{ marginTop: 6, whiteSpace: "normal" }}>{r.title}</div>
                  <div className="list-sub" style={{ whiteSpace: "pre-wrap" }}>{r.explanation}</div>
                  {r.impact ? <div className="hint">{r.impact}</div> : null}
                  {r.confidence != null ? <div style={{ marginTop: 6 }}><Confidence value={r.confidence} /></div> : null}
                </div>
                <div className="list-aside" style={{ flexWrap: "wrap", justifyContent: "flex-end" }}>
                  {r.change ? (
                    <Button size="sm" variant="ghost" onClick={() => onOpenChange(Number(r.change!.id))}>
                      {t("automation.linkedChange", { id: String(r.change.id) })} · {t((CHANGE_STATUS[r.change.status as keyof typeof CHANGE_STATUS] ?? CHANGE_STATUS.preview).label)}
                    </Button>
                  ) : null}
                  {r.status === "open" ? (
                    <>
                      <Button size="sm" variant="primary" icon={Check} disabled={!canAct} title={canAct ? undefined : t("automation.operatorOnly")}
                        pending={pending === `accept-${r.id}`} onClick={() => void act(r, "accept")}>
                        {r.can_create_change ? t("automation.acceptCreates") : t("automation.accept")}
                      </Button>
                      <Button size="sm" icon={X} disabled={!canAct} title={canAct ? undefined : t("automation.operatorOnly")}
                        pending={pending === `dismiss-${r.id}`} onClick={() => void act(r, "dismiss")}>{t("automation.dismiss")}</Button>
                    </>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </ResourceView>
    </Card>
  );
}
