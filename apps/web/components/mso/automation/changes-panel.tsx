"use client";

import { GitPullRequestArrow } from "lucide-react";
import { useEffect, useState } from "react";

import { Card, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { ChangePreview } from "@/components/mso/change-preview";
import { RiskChip } from "@/components/mso/risk-chip";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { CHANGE_STATUS, EVALUATION_LABEL } from "@/lib/status";

type Row = ChangeSet & { created_at: string; applied_at: string | null; evaluate_after: string | null };
const FILTERS = { open: "preview,approved", applied: "applied", closed: "rolled_back,failed,discarded" } as const;
type Filter = keyof typeof FILTERS;

function evaluation(impact: unknown): { result: string; message?: string } | null {
  if (!impact || typeof impact !== "object") return null;
  const ev = (impact as Record<string, unknown>).evaluation;
  return ev && typeof ev === "object" ? (ev as { result: string; message?: string }) : null;
}

/** Change sets list + ChangePreview (approve/apply/rollback via the shared component). */
export function ChangesPanel({ focusId }: { focusId: number | null }) {
  const { t, relative } = useI18n();
  const [filter, setFilter] = useState<Filter>("open");
  const list = useResource<{ items: Row[] }>("/changes", { status: FILTERS[filter], limit: 100 }, 30_000);
  const [selected, setSelected] = useState<number | null>(focusId);
  useEffect(() => { if (focusId != null) setSelected(focusId); }, [focusId]);
  const detail = useResource<Row>(selected != null ? `/changes/${selected}` : null);
  const ev = evaluation(detail.data?.impact);

  return (
    <>
      <Card className="span-5" title={t("automation.changes")} icon={GitPullRequestArrow} flush
        actions={<Segmented label={t("automation.filter.status")} value={filter} onChange={setFilter}
          options={(Object.keys(FILTERS) as Filter[]).map((f) => ({ value: f, label: t(`automation.changeFilter.${f}`) }))} />}>
        <ResourceView resource={list} endpoint="GET /api/v1/changes" isEmpty={(d) => d.items.length === 0}
          empty={<StateView kind="empty" compact title={t("automation.noChanges")} />}>
          {(d) => (
            <div className="list">
              {d.items.map((c) => {
                const st = CHANGE_STATUS[c.status] ?? { tone: "neutral" as const, label: "change.status.preview" as const };
                return (
                  <button type="button" key={String(c.id)} className="list-row list-row--hover" aria-pressed={selected === Number(c.id)}
                    style={{ width: "100%", textAlign: "left", background: selected === Number(c.id) ? "var(--mg-raised)" : "none", border: 0, font: "inherit", color: "inherit", cursor: "pointer" }}
                    onClick={() => setSelected(Number(c.id))}>
                    <div className="list-main">
                      <div className="list-title">{c.title}</div>
                      <div className="list-sub mono">#{String(c.id)} · {c.target} · {relative(c.created_at)}</div>
                    </div>
                    <div className="list-aside"><RiskChip risk={c.risk} /><StatusChip tone={st.tone}>{(c.status as string) === "discarded" ? t("automation.changeDiscarded") : t(st.label)}</StatusChip></div>
                  </button>
                );
              })}
            </div>
          )}
        </ResourceView>
      </Card>
      <div className="span-7 stack">
        {selected == null ? (
          <Card><StateView kind="empty" compact title={t("automation.pickChange")} /></Card>
        ) : (
          <>
            <ResourceView resource={detail} endpoint="GET /api/v1/changes/{id}" compact>
              {(c) => <ChangePreview key={String(c.id)} change={c} onChanged={() => { void detail.reload(); void list.reload(); }} />}
            </ResourceView>
            {ev ? (
              <Card title={t("automation.evaluation")}>
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <StatusChip tone={ev.result === "keep" ? "success" : ev.result === "rollback" ? "danger" : "neutral"}>{EVALUATION_LABEL[ev.result] ?? ev.result}</StatusChip>
                  <span className="t-2" style={{ fontSize: 13 }}>{ev.message}</span>
                </div>
              </Card>
            ) : null}
          </>
        )}
      </div>
    </>
  );
}
