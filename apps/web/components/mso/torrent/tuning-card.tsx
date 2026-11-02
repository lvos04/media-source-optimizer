"use client";

import { FlaskConical, Hourglass, Sparkles } from "lucide-react";
import { useState } from "react";

import { Button, Card, InlineState, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ChangePreview, formatOpValue } from "@/components/mso/change-preview";
import { RiskChip } from "@/components/mso/risk-chip";
import { api, errorMessage } from "@/lib/api/client";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { MetricStats, ProposeResult, TuningList, TuningRun } from "@/lib/api/torrent";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

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

function OpenChange({ id, onChanged }: { id: number; onChanged: () => void }) {
  const change = useResource<ChangeSet>(`/changes/${id}`);
  return (
    <ResourceView resource={change} compact endpoint={`GET /api/v1/changes/${id}`}>
      {(c) => <ChangePreview change={c} card={false} onChanged={() => { void change.reload(); onChanged(); }} />}
    </ResourceView>
  );
}

function RunRow({ run, onChanged }: { run: TuningRun; onChanged: () => void }) {
  const { t, duration, dateTime, num } = useI18n();
  const st = RUN_STATUS[run.status];
  const open = run.status === "proposed" || run.status === "evaluating" || run.status === "degraded_pending";
  const cmp = run.status === "evaluating" ? run.interim : run.result;
  return (
    <article className="evidence">
      <div className="evidence-head">
        <StatusChip tone={st.tone}>{t(st.key)}</StatusChip>
        <span className="evidence-source mono">{run.param}</span>
        <span className="grow" />
        {run.change_risk ? <RiskChip risk={run.change_risk} /> : null}
      </div>
      <dl className="evidence-body">
        <dt>{t("torrent.tuning.change")}</dt><dd>{formatOpValue(run.before_value)} → {formatOpValue(run.after_value)} <span className="t-3">({t(`torrent.tuning.kind.${run.kind}` as MessageKey)})</span></dd>
        <dt>{t("torrent.tuning.why")}</dt><dd>{run.reason}</dd>
        <dt>{t("torrent.tuning.metric")}</dt><dd>{run.metric_label} {run.higher_is_better ? "↑" : "↓"}</dd>
        <dt>{t("torrent.tuning.baseline")}</dt><dd><MetricValue stats={run.baseline} unit={run.metric_unit} /></dd>
        {cmp && (cmp.n ?? 0) > 0 ? <><dt>{run.status === "evaluating" ? t("torrent.tuning.now") : t("torrent.tuning.after")}</dt><dd><MetricValue stats={cmp} unit={run.metric_unit} />{cmp.delta_pct !== null && cmp.delta_pct !== undefined ? ` · ${cmp.delta_pct > 0 ? "+" : ""}${num(cmp.delta_pct, 1)}%` : ""}</dd></> : null}
        {run.status === "evaluating" && run.seconds_left !== undefined ? (
          <><dt>{t("torrent.tuning.evaluation")}</dt><dd><Hourglass className="ico ico-sm" aria-hidden="true" /> {t("torrent.tuning.in", { time: duration(run.seconds_left) })} · {dateTime(run.evaluate_at ?? null)}</dd></>
        ) : <><dt>{t("torrent.tuning.window")}</dt><dd>{t("torrent.tuning.hours", { h: num(run.evaluate_after_h, 1) })}</dd></>}
        {run.note ? <><dt>{t("torrent.tuning.outcome")}</dt><dd>{run.note}</dd></> : null}
      </dl>
      {open && run.change_id ? <div style={{ marginTop: 8 }}><OpenChange id={run.change_id} onChanged={onChanged} /></div> : null}
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
      else toast.push("success", t("torrent.tuning.proposed"), res.auto ? `${t("torrent.tuning.auto")}: ${res.auto}` : undefined);
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
