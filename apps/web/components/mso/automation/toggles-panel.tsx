"use client";

import { Bot, ListChecks, ScrollText, ShieldCheck } from "lucide-react";

import { Card, InlineState, StatusChip, Switch } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { useAction } from "@/lib/api/actions";
import type { Resource } from "@/lib/api/hooks";
import type { AutomationStatus } from "@/lib/api/insights";
import type { AutopilotHold } from "@/lib/api/safety";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useMode } from "@/lib/mode";
import { useSession } from "@/lib/session";
import { jobKindLabel } from "@/lib/status";

const LOG_RESULT: Record<string, string> = {
  done: "Klaar", applied: "Toegepast", skipped: "Overgeslagen", rate_limited: "Limiet bereikt", failed: "Mislukt",
  keep: "Behouden", unknown: "Onbeslist", niet_beschikbaar: "Niet beschikbaar", rolled_back: "Teruggedraaid",
  rollback_proposed: "Terugdraaien voorgesteld", rollback_failed: "Terugdraaien mislukt",
  rollback_skipped: "Niet teruggedraaid (buiten MSO gewijzigd)", self_paused: "Noodrem", resumed: "Hervat",
  beter: "Beter", gelijk: "Gelijk", slechter: "Slechter", onvoldoende: "Onvoldoende data", niet_van_toepassing: "Niet van toepassing",
};
const EVALUATOR_LABEL: Record<string, string> = { sabnzbd: "SABnzbd (faalratio, tijd tot voltooiing)", qbittorrent: "qBittorrent" };
/** automation_log.kind (backend `features/automation/engine.py`) → Dutch. */
const LOG_KIND: Record<string, string> = { run: "Ronde", apply: "Toepassen", evaluate: "Evaluatie" };
const KNOWN_AREAS = new Set(["usenet", "torrent", "indexers", "recovery", "costs", "mediaguard", "automation"]);

/** Per-area "automatisch veilig" toggles (PUT /automation/toggles, admin), state, log and jobs. */
export function TogglesPanel({ status }: { status: Resource<AutomationStatus> }) {
  const { t, num, relative } = useI18n();
  const { can } = useSession();
  const isAdmin = can("admin");
  const { run, pending } = useAction();
  // live mode/pause state (same source as the topbar chip and pause button) — the status resource may lag behind
  const { mode, paused } = useMode();
  const s = status.data as (AutomationStatus & { attempts_last_hour?: number; hold?: AutopilotHold | null }) | null;
  // Autopilot (mode, not paused or paused): the per-area toggles do not apply — every area is on via Autopilot
  const autopilotMode = (mode ?? s?.mode) === "autopilot";
  if (!s) return <Card className="span-12"><StateView kind={status.error ? "error" : "loading"} compact /></Card>;
  const toggle = async (area: string, on: boolean) => {
    const res = await run<AutomationStatus>(`t-${area}`, "put", "/automation/toggles", { areas: { [area]: on } }, t("automation.toggleSaved"));
    if (res) status.setData(() => res);
  };
  return (
    <>
      <Card className="span-7" title={t("automation.areas")} icon={Bot} flush>
        <p className="hint" style={{ margin: 0, padding: "12px 16px 4px" }}>{t("automation.areasHint", { age: num(s.max_age_h), cap: num(s.max_per_hour) })}</p>
        {autopilotMode ? <div style={{ padding: "8px 16px 4px" }}><InlineState tone="info" icon={Bot}>{t("mode.section.autoHintAutopilot")}</InlineState></div> : null}
        <div className="list">
          {s.areas.map((a) => (
            <div className="list-row" key={a.area}>
              <div className="list-main">
                <div className="list-title">{a.label}</div>
                <div className="list-sub">{a.pending_safe_changes ? t("automation.pending", { count: num(a.pending_safe_changes) }) : <span className="mono">{a.setting_key}</span>}</div>
              </div>
              {autopilotMode ? (
                <StatusChip tone="accent" icon={Bot} title={t("automation.viaAutopilotTitle")}>{t("mode.section.autoViaAutopilot")}</StatusChip>
              ) : <>
                <StatusChip tone={a.auto_apply_safe ? "success" : "neutral"}>{a.auto_apply_safe ? t("automation.on") : t("automation.off")}</StatusChip>
                <span title={isAdmin ? undefined : t("automation.adminOnly")}>
                  <Switch checked={a.auto_apply_safe} label={a.label} disabled={!isAdmin || pending === `t-${a.area}`} onChange={(v) => void toggle(a.area, v)} />
                </span>
              </>}
            </div>
          ))}
        </div>
      </Card>
      <Card className="span-5" title={t("automation.state")} icon={ShieldCheck}>
        <div className="stack">
          {(() => {
            const liveMode = mode ?? s.mode;
            const livePaused = mode === null ? s.paused : paused;
            if (s.hold?.active && liveMode !== "read_only" && !livePaused) return <InlineState tone="danger">{t("safety.stateHeld")}</InlineState>;
            if (liveMode === "autopilot" && !livePaused) return <InlineState tone="success" icon={Bot}>{t("automation.allowedAutopilot")}</InlineState>;
            if (liveMode === "manage" && !livePaused) return <InlineState tone="success">{t("automation.allowed")}</InlineState>;
            return <InlineState tone="warning">{liveMode === "read_only" ? t("automation.notAllowedReadOnly") : t("automation.notAllowedPaused")}</InlineState>;
          })()}
          <dl className="kv">
            <dt>{t("automation.appliedLastHour")}</dt><dd>{num(s.applied_last_hour)}</dd>
            <dt>{t("safety.attemptsLastHour")}</dt><dd>{num(s.attempts_last_hour ?? s.applied_last_hour)} / {num(s.max_per_hour)}</dd>
            <dt>{t("automation.autoRollback")}</dt><dd><StatusChip tone={s.auto_rollback ? "success" : "neutral"}>{s.auto_rollback ? (autopilotMode && !s.auto_rollback_setting ? t("mode.section.autoViaAutopilot") : t("automation.on")) : t("automation.off")}</StatusChip></dd>
            <dt>{t("automation.evaluators")}</dt>
            <dd>
              {s.evaluators.map((e) => EVALUATOR_LABEL[e] ?? e).join(" · ")}
              {s.evaluator_fallback ? <div className="t-2">{t("automation.evaluatorsFallback")}</div> : null}
              {!s.evaluators.length && !s.evaluator_fallback ? <span className="t-2">{t("automation.evaluatorsNone")}</span> : null}
            </dd>
            {s.evaluation ? <>
              <dt>{t("automation.evaluationRule")}</dt>
              <dd>{t("automation.evaluationRuleValue", { hours: num(s.evaluation.hours), hyst: num(s.evaluation.hysteresis_pct), n: num(s.evaluation.min_samples) })}</dd>
            </> : null}
            {s.evaluation && s.evaluation.torrent_auto_rollback !== null ? <>
              <dt>{t("automation.torrentAutoRollback")}</dt>
              <dd><StatusChip tone={s.evaluation.torrent_auto_rollback ? "success" : "neutral"}>{s.evaluation.torrent_auto_rollback ? t("automation.on") : t("automation.off")}</StatusChip></dd>
            </> : null}
          </dl>
        </div>
      </Card>
      <Card className="span-7" title={t("automation.log")} icon={ScrollText} flush>
        {s.log.length === 0 ? <StateView kind="empty" compact title={t("automation.logEmpty")} /> : (
          <div className="list" style={{ maxHeight: 360, overflow: "auto" }} tabIndex={0} role="region" aria-label="Automatiseringslog (scrollbaar)">
            {s.log.map((l) => (
              <div className="list-row" key={l.id}>
                <div className="list-main"><div className="list-title" style={{ whiteSpace: "normal" }}>{l.message}</div><div className="list-sub">{LOG_KIND[l.kind] ?? l.kind}{l.area ? ` · ${KNOWN_AREAS.has(l.area) ? t(`automation.area.${l.area}` as MessageKey) : l.area}` : ""}{l.change_id ? ` · #${l.change_id}` : ""} · {relative(l.ts)}</div></div>
                <StatusChip tone={l.result === "applied" || l.result === "keep" || l.result === "done" || l.result === "beter" ? "success" : l.result === "failed" || l.result === "slechter" || l.result === "self_paused" || l.result.startsWith("roll") ? "danger" : l.result === "onvoldoende" ? "warning" : l.result === "skipped" || l.result === "rate_limited" ? "warning" : "neutral"}>{LOG_RESULT[l.result] ?? l.result}</StatusChip>
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
                <div className="list-main"><div className="list-title">{jobKindLabel(j.kind)} <span className="mono t-3">{j.kind}</span></div><div className="list-sub">{j.last_update ? relative(j.last_update) : "—"}</div></div>
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
