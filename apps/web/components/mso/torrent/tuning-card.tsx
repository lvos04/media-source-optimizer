"use client";

import { Check, FlaskConical, Hourglass, Play, Sparkles } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button, Card, InlineState, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ChangePreview, formatOpValue } from "@/components/mso/change-preview";
import { RiskChip } from "@/components/mso/risk-chip";
import { notifyTorrentChanged } from "@/components/mso/torrent/shared";
import { api, errorMessage } from "@/lib/api/client";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { MetricStats, ProposeResult, TuningList, TuningRun } from "@/lib/api/torrent";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useWriteGate } from "@/lib/mode";
import { useSession } from "@/lib/session";
import { CHANGE_STATUS } from "@/lib/status";

const RUN_STATUS: Record<TuningRun["status"], { tone: Tone; key: MessageKey }> = {
  proposed: { tone: "info", key: "torrent.tuning.status.proposed" },
  evaluating: { tone: "accent", key: "torrent.tuning.status.evaluating" },
  kept: { tone: "success", key: "torrent.tuning.status.kept" },
  rolled_back: { tone: "warning", key: "torrent.tuning.status.rolled_back" },
  degraded_pending: { tone: "danger", key: "torrent.tuning.status.degraded_pending" },
  inconclusive: { tone: "neutral", key: "torrent.tuning.status.inconclusive" },
  discarded: { tone: "neutral", key: "torrent.tuning.status.discarded" },
};

function MetricValue({ stats, unit }: { stats: Partial<MetricStats> | undefined; unit: string | null }) {
  const { num, bytes, t } = useI18n();
  if (!stats || stats.median === null || stats.median === undefined) return <span className="t-3">{t("torrent.tuning.noData", { n: num(stats?.n ?? 0) })}</span>;
  const v = unit === "B/s" ? `${bytes(stats.median)}/s` : unit === "ratio" ? `${num(stats.median * 100, 1)}%` : num(stats.median, 1);
  return <span>{v} <span className="t-3" style={{ fontSize: 11.5 }}>({t("torrent.tuning.samples", { n: num(stats.n ?? 0) })})</span></span>;
}

const LEGACY_IMPACT_LABEL: Record<string, MessageKey> = {
  kind: "torrent.tuning.impact.kind",
  metric: "torrent.tuning.impact.metric",
  baseline: "torrent.tuning.impact.baseline",
  evaluate_after_h: "torrent.tuning.impact.evaluation",
};

/** Readable "verwachte impact": flat label → text (current backend); older nested impacts are summarised. */
function TuningImpact({ impact, unit }: { impact: unknown; unit: string | null }) {
  const { t, num } = useI18n();
  if (!impact || typeof impact !== "object" || Array.isArray(impact)) return null;
  const entries = Object.entries(impact as Record<string, unknown>);
  if (!entries.length) return null;
  return (
    <div>
      <span className="eyebrow">{t("torrent.tuning.impact")}</span>
      <dl className="kv" style={{ marginTop: 4, fontSize: 13 }}>
        {entries.map(([k, v]) => {
          let value: ReactNode;
          if (v === null || v === undefined || v === "") value = "—";
          else if (typeof v === "string") value = v;
          else if (Array.isArray(v) && v.every((x) => typeof x === "string")) value = <ul style={{ margin: 0, paddingLeft: 18 }}>{(v as string[]).map((x) => <li key={x}>{x}</li>)}</ul>;
          else if (typeof v === "number") value = k === "evaluate_after_h" ? t("torrent.tuning.hours", { h: num(v, 1) }) : num(v, 2);
          else if (typeof v === "object" && "median" in (v as object)) value = <MetricValue stats={v as Partial<MetricStats>} unit={unit} />;
          else return null; // nested technical data is not shown here (it stays in the change log)
          const label = LEGACY_IMPACT_LABEL[k] ? t(LEGACY_IMPACT_LABEL[k]) : k;
          return <div key={k} style={{ display: "contents" }}><dt>{label}</dt><dd>{value}</dd></div>;
        })}
      </dl>
    </div>
  );
}

/**
 * The run's change set. Approve/apply are only offered while the change is open (preview/approved); after
 * any outcome — including a failed apply — the change is re-read, so a failed change shows its final status
 * and error instead of a stale "Toepassen" button. Rollback of an applied change uses the shared preview.
 */
function RunChange({ id, unit, onChanged }: { id: number; unit: string | null; onChanged: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const gate = useWriteGate("operator");
  const change = useResource<ChangeSet & { error?: string | null }>(`/changes/${id}`);
  const [pending, setPending] = useState<"approve" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async (action: "approve" | "apply") => {
    setPending(action);
    setError(null);
    try {
      await api.post(`/changes/${encodeURIComponent(String(id))}/${action}`);
      toast.push("success", t(action === "approve" ? "change.approved" : "change.applied"));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(null);
      await change.reload();
      onChanged();
      notifyTorrentChanged();
    }
  };
  return (
    <ResourceView resource={change} compact endpoint={`GET /api/v1/changes/${id}`}>
      {(c) => {
        const isOpen = c.status === "preview" || c.status === "approved";
        const canApply = c.status === "approved" || (c.status === "preview" && c.risk !== "toestemming_nodig");
        const st = CHANGE_STATUS[c.status] ?? CHANGE_STATUS.preview;
        return (
          <div className="stack-sm">
            <ChangePreview change={{ ...c, impact: null }} card={false} actions={c.status === "applied"}
              onChanged={() => { void change.reload(); onChanged(); notifyTorrentChanged(); }} />
            <TuningImpact impact={c.impact} unit={unit} />
            {error ? <InlineState tone="danger" role="alert">{error}</InlineState> : null}
            {isOpen ? (
              <div className="row wrap">
                {c.status === "preview" ? (
                  <Button size="sm" icon={Check} pending={pending === "approve"} disabled={!gate.allowed || pending !== null}
                    title={gate.reason} onClick={() => void run("approve")}>{t("action.approve")}</Button>
                ) : null}
                <Button size="sm" variant="primary" icon={Play} pending={pending === "apply"} disabled={!gate.allowed || !canApply || pending !== null}
                  title={!gate.allowed ? gate.reason : canApply ? undefined : t("change.needsApproval")} onClick={() => void run("apply")}>{t("action.apply")}</Button>
                {!gate.allowed && gate.reason ? <span className="hint" style={{ margin: 0 }}>{gate.reason}</span> : null}
              </div>
            ) : c.status !== "applied" ? (
              <InlineState tone={st.tone === "danger" ? "danger" : "neutral"} role="status">
                {t("torrent.tuning.changeFinal", { status: t(st.label) })}{c.error ? ` — ${c.error}` : ""}
              </InlineState>
            ) : null}
          </div>
        );
      }}
    </ResourceView>
  );
}

const BUNDLE_PARAM = "bundle:guardrails";

/** before → after; a guardrail bundle stores one value per preference ({"pref:max_connec": 50000, …}). */
function RunValues({ run }: { run: TuningRun }) {
  const { t } = useI18n();
  const isMap = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
  if (isMap(run.before_value) && isMap(run.after_value)) {
    const after = run.after_value;
    return (
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {Object.entries(run.before_value).map(([k, b]) => (
          <li key={k}><span className="mono">{k.replace(/^pref:/, "")}</span>: {typeof b === "number" && b <= 0 ? t("torrent.unlimited") : formatOpValue(b)} → {formatOpValue(after[k])}</li>
        ))}
      </ul>
    );
  }
  return <>{formatOpValue(run.before_value)} → {formatOpValue(run.after_value)}</>;
}

function RunRow({ run, onChanged }: { run: TuningRun; onChanged: () => void }) {
  const { t, duration, dateTime, num } = useI18n();
  const guard = run.kind === "guardrail";
  const st = RUN_STATUS[run.status];
  const open = run.status === "proposed" || run.status === "evaluating" || run.status === "degraded_pending";
  const cmp = run.status === "evaluating" ? run.interim : run.result;
  return (
    <article className="evidence">
      <div className="evidence-head">
        <StatusChip tone={st.tone}>{t(st.key)}</StatusChip>
        <span className={run.param === BUNDLE_PARAM ? "evidence-source" : "evidence-source mono"}>{run.param === BUNDLE_PARAM ? t("torrent.tuning.bundleParam") : run.param}</span>
        <span className="grow" />
        {run.change_risk ? <RiskChip risk={run.change_risk} /> : null}
        {!open && run.change_status && CHANGE_STATUS[run.change_status as keyof typeof CHANGE_STATUS] ? (
          <StatusChip tone={CHANGE_STATUS[run.change_status as keyof typeof CHANGE_STATUS].tone}>
            {t("torrent.tuning.changeStatus", { status: t(CHANGE_STATUS[run.change_status as keyof typeof CHANGE_STATUS].label) })}
          </StatusChip>
        ) : null}
      </div>
      <dl className="evidence-body">
        <dt>{t("torrent.tuning.change")}</dt><dd><RunValues run={run} /> <span className="t-3">({t(`torrent.tuning.kind.${run.kind}` as MessageKey)})</span></dd>
        <dt>{t("torrent.tuning.why")}</dt><dd>{run.reason}</dd>
        {guard ? null : <><dt>{t("torrent.tuning.metric")}</dt><dd>{run.metric_label} {run.higher_is_better ? "↑" : "↓"}</dd>
        <dt>{t("torrent.tuning.baseline")}</dt><dd><MetricValue stats={run.baseline} unit={run.metric_unit} /></dd></>}
        {cmp && (cmp.n ?? 0) > 0 ? <><dt>{run.status === "evaluating" ? t("torrent.tuning.now") : t("torrent.tuning.after")}</dt><dd><MetricValue stats={cmp} unit={run.metric_unit} />{cmp.delta_pct !== null && cmp.delta_pct !== undefined ? ` · ${cmp.delta_pct > 0 ? "+" : ""}${num(cmp.delta_pct, 1)}%` : ""}</dd></> : null}
        {run.status === "evaluating" && run.seconds_left !== undefined ? (
          <><dt>{t("torrent.tuning.evaluation")}</dt><dd><Hourglass className="ico ico-sm" aria-hidden="true" /> {t("torrent.tuning.in", { time: duration(run.seconds_left) })} · {dateTime(run.evaluate_at ?? null)}</dd></>
        ) : <><dt>{t("torrent.tuning.window")}</dt><dd>{guard ? t("torrent.tuning.noMeasurement") : t("torrent.tuning.hours", { h: num(run.evaluate_after_h, 1) })}</dd></>}
        {run.note ? <><dt>{t("torrent.tuning.outcome")}</dt><dd>{run.note}</dd></> : null}
      </dl>
      {open && run.change_id ? <div style={{ marginTop: 8 }}><RunChange id={run.change_id} unit={run.metric_unit} onChanged={onChanged} /></div> : null}
    </article>
  );
}

export function TuningCard() {
  const { t, num } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const isOperator = can("operator");
  const runs = useResource<TuningList>("/torrent/tuning", undefined, 60_000);
  const [pending, setPending] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const propose = async () => {
    setPending("propose");
    setMessage(null);
    try {
      const res = await api.post<ProposeResult>("/torrent/tuning/propose");
      if (res.reason) setMessage(res.reason);
      else toast.push("success", t(res.bundle ? "torrent.tuning.bundleProposed" : "torrent.tuning.proposed"), res.auto ? `${t("torrent.tuning.auto")}: ${res.auto}` : undefined);
      await runs.reload();
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(null);
    }
  };
  const evaluate = async () => {
    setPending("evaluate");
    try {
      const res = await api.post<{ evaluated: unknown[] }>("/torrent/tuning/evaluate");
      toast.push("info", t("torrent.tuning.evaluated", { count: num(res.evaluated.length) }));
      await runs.reload();
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(null);
    }
  };

  return (
    <Card className="span-12" title={t("torrent.tuning.title")} icon={FlaskConical}
      actions={<>
        <Button size="sm" icon={Sparkles} pending={pending === "propose"} disabled={!isOperator || !!runs.data?.open_run}
          title={!isOperator ? t("torrent.operatorOnly") : runs.data?.open_run ? t("torrent.tuning.oneAtATime") : undefined} onClick={() => void propose()}>{t("torrent.tuning.propose")}</Button>
        <Button size="sm" icon={Hourglass} pending={pending === "evaluate"} disabled={!isOperator} title={isOperator ? undefined : t("torrent.operatorOnly")} onClick={() => void evaluate()}>{t("torrent.tuning.evaluate")}</Button>
      </>}>
      <ResourceView resource={runs} endpoint="GET /api/v1/torrent/tuning" compact>
        {(d) => (
          <div className="stack">
            <div className="row wrap" style={{ gap: 6 }}>
              <StatusChip tone={d.settings.autotune_enabled ? "success" : "neutral"}>{d.settings.autotune_enabled ? t("torrent.tuning.autoOn") : t("torrent.tuning.autoOff")}</StatusChip>
              <StatusChip tone={d.automation_allowed ? "success" : "warning"}>{d.automation_allowed ? t("torrent.tuning.automationAllowed") : t("torrent.tuning.automationBlocked")}</StatusChip>
              <span className="t-3" style={{ fontSize: 12 }}>{t("torrent.tuning.params", { eval: num(d.settings.evaluation_hours), base: num(d.settings.baseline_hours), hyst: num(d.settings.hysteresis_pct) })}</span>
            </div>
            <p className="t-2" style={{ margin: 0, fontSize: 13 }}>{t("torrent.tuning.explain")}</p>
            {message ? <InlineState tone="info" role="status">{message}</InlineState> : null}
            {d.items.length ? d.items.slice(0, 10).map((r) => <RunRow key={r.id} run={r} onChanged={() => void runs.reload()} />)
              : <StateView kind="empty" compact title={t("torrent.tuning.none")} body={t("torrent.tuning.noneBody")} />}
          </div>
        )}
      </ResourceView>
    </Card>
  );
}
