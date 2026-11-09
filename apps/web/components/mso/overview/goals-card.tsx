"use client";

import {
  ArrowDownRight, ArrowRight, ArrowUpRight, CircleCheck, CircleHelp, CircleX, Equal, type LucideIcon, Percent, PowerOff, Server,
  ShieldAlert, Target, TriangleAlert,
} from "lucide-react";
import Link from "next/link";

import { Card, InlineState, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import type { Goal, GoalKey, Goals, GoalStatus, GoalTrendPoint, GoalsTrial } from "@/lib/api/goals";
import { useResource } from "@/lib/api/hooks";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const ENDPOINT = "GET /api/v1/overview/goals";
const ICON: Record<GoalKey, LucideIcon> = { failure_ratio: Percent, suspicious_torrents: ShieldAlert, failing_servers: Server };
const TITLE: Record<GoalKey, MessageKey> = {
  failure_ratio: "goals.failure.title", suspicious_torrents: "goals.suspicious.title", failing_servers: "goals.servers.title",
};
const DEF: Record<GoalKey, MessageKey> = {
  failure_ratio: "goals.failure.def", suspicious_torrents: "goals.suspicious.def", failing_servers: "goals.servers.def",
};
const STATUS_TONE: Record<GoalStatus, Tone> = { met: "success", not_met: "danger", insufficient: "neutral", unavailable: "warning" };
const STATUS_ICON: Record<GoalStatus, LucideIcon> = { met: CircleCheck, not_met: CircleX, insufficient: CircleHelp, unavailable: TriangleAlert };
const KPI_TONE: Partial<Record<GoalStatus, string>> = { met: "kpi--success", not_met: "kpi--danger" };

/** "2026-10-13" (a local day from the API) → Date at local noon, so no time zone shifts the day. */
function localDay(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1, 12);
}

function shortDay(day: string): string {
  return localDay(day).toLocaleDateString("nl-NL", { day: "numeric", month: "short" });
}

function useValue() {
  const { num, pct } = useI18n();
  return (goal: Pick<Goal, "unit">, v: number | null | undefined) =>
    v === null || v === undefined ? "—" : goal.unit === "ratio" ? pct(v * 100, 1) : num(v);
}

/** Overzicht → first card: the three trial goals (start, now, target, day X of 28, trend, what holds it back). */
export function GoalsCard() {
  const { t, num } = useI18n();
  const { can } = useSession();
  const res = useResource<Goals>("/overview/goals", undefined, 300_000);
  const data = res.data;
  return (
    <Card className="span-12" title={t("goals.title")} icon={Target} flush
      sub={data ? <TrialSub trial={data.trial} /> : undefined}
      actions={data ? (
        <>
          <StatusChip tone={data.met === data.total ? "success" : "neutral"} icon={data.met === data.total ? CircleCheck : Target}>
            {t("goals.metCount", { met: num(data.met), total: num(data.total) })}
          </StatusChip>
          {can("admin") ? <Link className="link-more" href="/instellingen?tab=automatisering">{t("goals.changeStart")} <ArrowRight className="ico ico-sm" aria-hidden="true" /></Link> : null}
        </>
      ) : undefined}>
      <ResourceView resource={res} endpoint={ENDPOINT} compact>
        {(g) => (
          <>
            {g.trial.start_warning ? <div style={{ padding: "12px 16px 0" }}><InlineState tone="warning">{g.trial.start_warning}</InlineState></div> : null}
            <div data-testid="goals-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 280px), 1fr))", gap: 12, padding: "12px 16px" }}>
              {g.goals.map((goal) => <GoalTile key={goal.key} goal={goal} notStarted={g.trial.not_started} />)}
            </div>
            <TrialFooter trial={g.trial} />
          </>
        )}
      </ResourceView>
    </Card>
  );
}

function TrialSub({ trial }: { trial: GoalsTrial }) {
  const { t, num } = useI18n();
  if (trial.not_started) return <>{t("goals.sub.notStarted", { date: shortDay(trial.start_date) })}</>;
  if (trial.ended) return <>{t("goals.sub.ended", { length: num(trial.length_days) })}</>;
  return <>{t("goals.sub.day", { day: num(trial.day), length: num(trial.length_days) })}</>;
}

function TrialFooter({ trial }: { trial: GoalsTrial }) {
  const { t, date } = useI18n();
  const source = t(`goals.startSource.${trial.start_source}` as MessageKey);
  return (
    <div className="row wrap t-3" style={{ gap: "4px 12px", padding: "0 16px 12px", fontSize: 12 }}>
      <span>{t(trial.not_started ? "goals.starts" : "goals.started", { date: date(localDay(trial.start_date)) })} ({source})</span>
      <span>{t(trial.ended ? "goals.ended" : "goals.ends", { date: date(localDay(trial.end_date)) })}</span>
      <span>{t("goals.timezone", { tz: trial.timezone })}</span>
      <span>{t("goals.startDef")}</span>
    </div>
  );
}

function Direction({ goal }: { goal: Goal }) {
  const { t } = useI18n();
  if (!goal.direction) return null;
  const icon = goal.direction === "better" ? ArrowDownRight : goal.direction === "worse" ? ArrowUpRight : Equal;
  const tone: Tone = goal.direction === "better" ? "success" : goal.direction === "worse" ? "danger" : "neutral";
  return <StatusChip tone={tone} icon={icon}>{t(`goals.direction.${goal.direction}` as MessageKey)}</StatusChip>;
}

function StartValue({ goal, notStarted }: { goal: Goal; notStarted: boolean }) {
  const { t } = useI18n();
  const value = useValue();
  const s = goal.start;
  if (s.value === null) return <>{t(notStarted ? "goals.startNotYet" : "goals.startNone", { date: shortDay(s.date) })}</>;
  return (
    <>
      {t("goals.start", { date: shortDay(s.date) })}: <strong className="num">{value(goal, s.value)}</strong>
      {goal.unit === "ratio" && s.insufficient ? <span className="t-3"> · {t("goals.startTooFew")}</span> : null}
      {s.provisional ? <span className="t-3"> · {t("goals.startProvisional")}</span> : null}
    </>
  );
}

function GoalTile({ goal, notStarted }: { goal: Goal; notStarted: boolean }) {
  const { t, num } = useI18n();
  const value = useValue();
  const Icon = ICON[goal.key];
  const target = value(goal, goal.target);
  return (
    <article className={`kpi ${KPI_TONE[goal.status] ?? ""}`} data-testid={`goal-${goal.key}`} aria-labelledby={`goal-${goal.key}-title`} style={{ gap: 8, minWidth: 0 }}>
      <div className="row-between wrap" style={{ gap: 6 }}>
        <span className="kpi-label" id={`goal-${goal.key}-title`}><Icon className="ico" aria-hidden="true" />{t(TITLE[goal.key])}</span>
        <StatusChip tone={STATUS_TONE[goal.status]} icon={STATUS_ICON[goal.status]}>{t(`goals.status.${goal.status}` as MessageKey)}</StatusChip>
      </div>
      {!goal.available ? (
        <InlineState tone="warning">{goal.reason}</InlineState>
      ) : (
        <>
          <div className="row wrap" style={{ alignItems: "baseline", gap: "2px 10px" }}>
            <span className="kpi-value num">{value(goal, goal.now.value)}</span>
            <span className="t-3" style={{ fontSize: 12 }}>{t(`goals.target.${goal.comparison}` as MessageKey, { value: target })}</span>
          </div>
          <div className="row wrap" style={{ gap: 6, fontSize: 12.5 }}>
            <span className="t-2"><StartValue goal={goal} notStarted={notStarted} /></span>
            <Direction goal={goal} />
          </div>
          <GoalDetails goal={goal} />
          <TrendLine goal={goal} />
          <Blocker goal={goal} />
          {goal.key === "failing_servers" && goal.disabled_by_mso?.length ? <SwitchedOff goal={goal} /> : null}
        </>
      )}
      <p className="t-3" style={{ margin: 0, fontSize: 11.5 }}>{t(DEF[goal.key])}</p>
      {goal.key === "failing_servers" && goal.now.probing ? <span className="t-3" style={{ fontSize: 12 }}>{t("goals.servers.probing", { count: num(goal.now.probing) })}</span> : null}
    </article>
  );
}

function GoalDetails({ goal }: { goal: Goal }) {
  const { t, num, pct } = useI18n();
  if (goal.key === "failure_ratio") {
    const n = goal.now;
    const min = num(goal.min_n ?? 20);
    return (
      <dl className="kv" style={{ fontSize: 12 }}>
        <dt>{t("goals.failure.net")}</dt>
        <dd>{n.n ? t("goals.failure.nShort", { failed: num(n.failed ?? 0), n: num(n.n) }) : "—"}
          {n.insufficient ? <span className="t-3"> · {t("goals.failure.tooFew", { min })}</span> : null}</dd>
        <dt>{t("goals.failure.gross")}</dt>
        <dd>{n.gross === null || n.gross === undefined ? "—" : <><strong className="num">{pct(n.gross * 100, 1)}</strong> · {t("goals.failure.nShort", { failed: num(n.gross_failed ?? 0), n: num(n.gross_n ?? 0) })}</>}
          {n.gross_insufficient && n.gross_n ? <span className="t-3"> · {t("goals.failure.tooFew", { min })}</span> : null}</dd>
      </dl>
    );
  }
  if (goal.key === "suspicious_torrents") {
    const n = goal.now;
    return (
      <div className="row wrap" style={{ gap: 6, fontSize: 12 }}>
        <span className="t-2">{n.new_7d ? t("goals.suspicious.new", { count: num(n.new_7d) }) : t("goals.suspicious.noneNew")}</span>
        {n.awaiting_approval ? <StatusChip tone="warning">{t("goals.suspicious.waiting", { count: num(n.awaiting_approval) })}</StatusChip> : null}
      </div>
    );
  }
  return null;
}

function Blocker({ goal }: { goal: Goal }) {
  const { t } = useI18n();
  if (goal.status === "met") {
    return <div style={{ fontSize: 12.5 }}><span className="t-3">{t("goals.blocker")}: </span>{t("goals.blockerNone")}</div>;
  }
  if (!goal.blocker) return null;
  return (
    <div style={{ fontSize: 12.5, lineHeight: 1.45 }} data-testid={`goal-${goal.key}-blocker`}>
      <span className="t-3">{t("goals.blocker")}: </span>{goal.blocker.text}{" "}
      <Link className="link-more" href={goal.blocker.link}>{t("goals.view")} <ArrowRight className="ico ico-sm" aria-hidden="true" /></Link>
    </div>
  );
}

function SwitchedOff({ goal }: { goal: Goal }) {
  const { t, date } = useI18n();
  return (
    <InlineState tone="neutral" icon={PowerOff}>
      <div style={{ fontSize: 12.5 }}>
        <strong>{goal.disabled_text}</strong>
        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
          {(goal.disabled_by_mso ?? []).map((s) => (
            <li key={s.server}>
              {s.name} — {s.reason_label ?? s.label}
              {s.disabled_at ? <span className="t-3"> · {t("goals.servers.since", { date: date(s.disabled_at) })}</span> : null}
              {s.given_up ? <span className="t-3"> · {t("goals.servers.givenUp")}</span> : null}
            </li>
          ))}
        </ul>
      </div>
    </InlineState>
  );
}

/** Daily values as an inline SVG line with the target as a dashed line; stays visible on small screens. */
function TrendLine({ goal }: { goal: Goal }) {
  const { t } = useI18n();
  const value = useValue();
  const pts = goal.trend;
  const known = pts.filter((p): p is GoalTrendPoint & { value: number } => p.value !== null);
  if (known.length < 2) return <p className="t-3" style={{ margin: 0, fontSize: 12 }}>{t("goals.trendEmpty")}</p>;
  const W = 280, H = 56, P = 5;
  const max = Math.max(goal.target, ...known.map((p) => p.value), goal.unit === "ratio" ? 0.05 : 1);
  const x = (i: number) => P + (pts.length === 1 ? (W - 2 * P) / 2 : (i / (pts.length - 1)) * (W - 2 * P));
  const y = (v: number) => H - P - (v / max) * (H - 2 * P);
  const segments: string[][] = [];
  let cur: string[] = [];
  pts.forEach((p, i) => {
    if (p.value === null) {
      if (cur.length) segments.push(cur);
      cur = [];
      return;
    }
    cur.push(`${x(i).toFixed(1)},${y(p.value).toFixed(1)}`);
  });
  if (cur.length) segments.push(cur);
  const first = known[0], last = known[known.length - 1];
  const label = t("goals.trendLabel", {
    from: `${shortDay(first.date)} ${value(goal, first.value)}`, to: `${shortDay(last.date)} ${value(goal, last.value)}`, target: value(goal, goal.target),
  });
  const tone = goal.status === "met" ? "var(--mg-success)" : goal.status === "not_met" ? "var(--mg-danger)" : "var(--mg-accent)";
  const reconstructed = pts.some((p) => p.source === "reconstructed");
  return (
    <figure style={{ margin: 0 }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={label} style={{ display: "block", maxHeight: 72 }}>
        <line x1={P} x2={W - P} y1={y(goal.target)} y2={y(goal.target)} stroke="var(--mg-success)" strokeWidth="1.25" strokeDasharray="4 3" opacity="0.8" />
        {segments.map((s, i) => s.length > 1
          ? <polyline key={i} points={s.join(" ")} fill="none" stroke={tone} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
          : null)}
        {pts.map((p, i) => (p.value === null ? null : (
          <circle key={p.date} cx={x(i)} cy={y(p.value)} r={i === pts.length - 1 ? 3 : 2} fill={tone}
            opacity={p.source === "reconstructed" ? 0.4 : 1}><title>{`${shortDay(p.date)}: ${value(goal, p.value)}`}</title></circle>
        )))}
      </svg>
      <figcaption className="t-3" style={{ fontSize: 11 }}>
        {t("goals.trendLegend")}{reconstructed ? ` · ${t("goals.trendReconstructed")}` : ""}
      </figcaption>
    </figure>
  );
}
