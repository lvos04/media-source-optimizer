"use client";

import { Bot, ListChecks, ScrollText, ShieldCheck } from "lucide-react";

import { Card, InlineState, StatusChip, Switch } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import type { Resource } from "@/lib/api/hooks";
import type { AutomationStatus } from "@/lib/api/insights";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/** Per-area "automatisch veilig" toggles (PUT /automation/toggles, admin), state, log and jobs. */
export function TogglesPanel({ status }: { status: Resource<AutomationStatus> }) {
  const { t, num, relative } = useI18n();
  const { can } = useSession();
  const isAdmin = can("admin");
  const { run, pending } = useAction();
  const s = status.data;
  if (!s) return <Card className="span-12"><StateView kind={status.error ? "error" : "loading"} compact /></Card>;
  const toggle = async (area: string, on: boolean) => {
    const res = await run<AutomationStatus>(`t-${area}`, "put", "/automation/toggles", { areas: { [area]: on } }, t("automation.toggleSaved"));
    if (res) status.setData(() => res);
  };
  return (
    <>
      <Card className="span-7" title={t("automation.areas")} icon={Bot} flush>
        <p className="hint" style={{ margin: 0, padding: "12px 16px 4px" }}>{t("automation.areasHint", { age: num(s.max_age_h), cap: num(s.max_per_hour) })}</p>
        <div className="list">
          {s.areas.map((a) => (
            <div className="list-row" key={a.area}>
              <div className="list-main">
                <div className="list-title">{a.label}</div>
                <div className="list-sub">{a.pending_safe_changes ? t("automation.pending", { count: num(a.pending_safe_changes) }) : <span className="mono">{a.setting_key}</span>}</div>
              </div>
              <StatusChip tone={a.auto_apply_safe ? "success" : "neutral"}>{a.auto_apply_safe ? t("automation.on") : t("automation.off")}</StatusChip>
              <span title={isAdmin ? undefined : t("automation.adminOnly")}>
                <Switch checked={a.auto_apply_safe} label={a.label} disabled={!isAdmin || pending === `t-${a.area}`} onChange={(v) => void toggle(a.area, v)} />
              </span>
            </div>
          ))}
        </div>
      </Card>
      <Card className="span-5" title={t("automation.state")} icon={ShieldCheck}>
        <div className="stack">
          {s.automation_allowed ? <InlineState tone="success">{t("automation.allowed")}</InlineState>
            : <InlineState tone="warning">{s.mode !== "manage" ? t("automation.notAllowedReadOnly") : t("automation.notAllowedPaused")}</InlineState>}
          <dl className="kv">
            <dt>{t("automation.appliedLastHour")}</dt><dd>{num(s.applied_last_hour)} / {num(s.max_per_hour)}</dd>
            <dt>{t("automation.autoRollback")}</dt><dd><StatusChip tone={s.auto_rollback ? "success" : "neutral"}>{s.auto_rollback ? t("automation.on") : t("automation.off")}</StatusChip></dd>
            <dt>{t("automation.evaluators")}</dt>
            <dd>{s.evaluators.length ? <span className="mono">{s.evaluators.join(", ")}</span> : <span className="t-2">{t("automation.evaluatorsNone")}</span>}</dd>
          </dl>
        </div>
      </Card>
      <Card className="span-7" title={t("automation.log")} icon={ScrollText} flush>
        {s.log.length === 0 ? <StateView kind="empty" compact title={t("automation.logEmpty")} /> : (
          <div className="list" style={{ maxHeight: 360, overflow: "auto" }}>
            {s.log.map((l) => (
              <div className="list-row" key={l.id}>
                <div className="list-main"><div className="list-title" style={{ whiteSpace: "normal" }}>{l.message}</div><div className="list-sub mono">{l.kind}{l.area ? ` · ${l.area}` : ""}{l.change_id ? ` · #${l.change_id}` : ""} · {relative(l.ts)}</div></div>
                <StatusChip tone={l.result === "applied" || l.result === "keep" || l.result === "done" ? "success" : l.result === "failed" || l.result.startsWith("roll") ? "danger" : l.result === "skipped" || l.result === "rate_limited" ? "warning" : "neutral"}>{l.result}</StatusChip>
              </div>
            ))}
          </div>
        )}
      </Card>
      <Card className="span-5" title={t("automation.jobs")} icon={ListChecks} flush>
        {s.jobs.length === 0 ? <StateView kind="empty" compact title={t("automation.jobsEmpty")} /> : (
          <div className="list">
            {s.jobs.map((j) => (
              <div className="list-row" key={j.kind}>
                <div className="list-main"><div className="list-title mono">{j.kind}</div><div className="list-sub">{j.last_update ? relative(j.last_update) : "—"}</div></div>
                <div className="list-aside">
                  {j.pending ? <StatusChip tone="accent" pending>{t("automation.jobPending", { count: num(j.pending) })}</StatusChip> : null}
                  {j.failed_24h ? <StatusChip tone="danger">{t("automation.jobFailed", { count: num(j.failed_24h) })}</StatusChip> : <StatusChip tone="success">OK</StatusChip>}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}
