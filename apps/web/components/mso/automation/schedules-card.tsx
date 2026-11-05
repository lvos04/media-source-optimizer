"use client";

import { Timer } from "lucide-react";

import { Card, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useResource } from "@/lib/api/hooks";
import type { ScheduleItem, ScheduleList } from "@/lib/api/insights";
import { useI18n, type MessageKey } from "@/lib/i18n";

const STATUS: Record<ScheduleItem["status"], { tone: Tone; key: MessageKey }> = {
  ok: { tone: "success", key: "system.sched.ok" },
  mislukt: { tone: "danger", key: "system.sched.failed" },
  bezig: { tone: "accent", key: "system.sched.running" },
  nog_niet: { tone: "neutral", key: "system.sched.notYet" },
  uit: { tone: "neutral", key: "system.sched.off" },
};

/** Systeem → "Automatische taken": every periodic job (GET /automation/schedules) with last/next run and status. */
export function SchedulesCard() {
  const { t, relative, dateTime, duration, num } = useI18n();
  const res = useResource<ScheduleList>("/automation/schedules", undefined, 60_000);
  return (
    <Card id="automatische-taken" className="span-12" title={t("system.sched.title")} sub={t("system.sched.sub")} icon={Timer} flush>
      <ResourceView resource={res} endpoint="GET /api/v1/automation/schedules" compact isEmpty={(d) => d.items.length === 0}
        empty={<StateView kind="empty" compact title={t("system.sched.empty")} />}>
        {(d) => (
          <div className="table-wrap" tabIndex={0} role="region" aria-label={t("system.sched.title")}>
            <table className="table table--compact table--stack">
              <thead><tr>
                <th>{t("system.sched.task")}</th><th>{t("system.sched.every")}</th><th>{t("system.sched.last")}</th>
                <th>{t("system.sched.next")}</th><th>{t("system.sched.status")}</th>
              </tr></thead>
              <tbody>{d.items.map((i) => {
                const st = STATUS[i.status] ?? STATUS.nog_niet;
                return (
                  <tr key={i.kind} data-testid={`schedule-${i.kind}`}>
                    <td className="cell-primary">
                      {i.label}
                      <div className="cell-sub">{i.area} · <span className="mono">{i.kind}</span></div>
                    </td>
                    <td data-label={t("system.sched.every")}>{t("system.sched.everyValue", { d: duration(i.every_s) })}</td>
                    <td data-label={t("system.sched.last")}>
                      {i.last_run_at ? <span title={dateTime(i.last_run_at)}>{relative(i.last_run_at)}</span> : "—"}
                      {i.last_error && i.last_status === "failed" ? <div className="cell-sub" style={{ whiteSpace: "normal", color: "var(--mg-danger)" }}>{i.last_error.slice(0, 160)}</div> : null}
                    </td>
                    <td data-label={t("system.sched.next")}>{i.next_run_at ? <span title={dateTime(i.next_run_at)}>{relative(i.next_run_at)}</span> : <span className="t-3">{i.reason ?? "—"}</span>}</td>
                    <td data-label={t("system.sched.status")}>
                      <StatusChip tone={st.tone} pending={i.status === "bezig"}>{t(st.key)}</StatusChip>
                      {i.failed_24h ? <div className="cell-sub">{t("system.sched.failed24h", { count: num(i.failed_24h) })}</div> : null}
                    </td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        )}
      </ResourceView>
      <p className="hint" style={{ margin: 0, padding: "8px 16px 12px" }}>{t("system.sched.hint")}</p>
    </Card>
  );
}
