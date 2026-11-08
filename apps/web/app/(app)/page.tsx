// Overzicht — layout follows MediaGuard Ultimate's overzicht (KPI row + card grid), data from GET /api/v1/overview.
"use client";

import {
  Activity, ArrowRight, CircleX, Coins, DatabaseBackup, GitPullRequestArrow, HeartPulse, History, Lightbulb, MapPin, Plug, ShieldCheck,
} from "lucide-react";
import Link from "next/link";

import { KpiCard, Timeline } from "@/components/mg/domain";
import { Card, LinkButton, PageHeader, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { AutopilotCard, WaitingCard } from "@/components/mso/automation/autopilot-summary";
import { RiskChip } from "@/components/mso/risk-chip";
import type { Risk } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { localizeAuditSummary, type Overview } from "@/lib/api/insights";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useMode } from "@/lib/mode";
import { healthLabel, healthTone } from "@/lib/status";

const RISKS: Risk[] = ["toestemming_nodig", "automatisch_veilig", "advies"];
const LOC_LABEL: Record<string, MessageKey> = { source: "overview.loc.source", provider: "overview.loc.provider", client: "overview.loc.client" };
const MG_TONE: Record<string, Tone> = { connected: "success", degraded: "warning", standalone: "neutral" };

export default function OverviewPage() {
  const { t, date, num, pct, money } = useI18n();
  const ov = useResource<Overview>("/overview", undefined, 30_000);
  const o = ov.data;
  const health = o?.health;
  const healthTxt = health ? t(healthLabel(health.status)) : "—";
  const cost = o?.costs;
  const { mode: liveMode } = useMode();
  const shownMode = liveMode ?? o?.mode ?? null;
  return (
    <>
      <PageHeader
        title={t("overview.title")}
        sub={t("overview.sub", { date: date(new Date()), mode: shownMode ? t(`mode.${shownMode}`) : "—" })}
        actions={<LinkButton href="/automatisering" icon={Lightbulb}>{t("nav.automation")}</LinkButton>}
      />
      {ov.error && !o ? (
        <ResourceView resource={ov} endpoint="GET /api/v1/overview">{() => null}</ResourceView>
      ) : (
        <>
          <section className="kpi-grid" aria-label={t("overview.kpis")}>
            <KpiCard label={t("overview.kpi.health")} icon={HeartPulse} unavailable={!o || health?.status === "unknown"}
              value={healthTxt}
              tone={health?.status === "healthy" ? "success" : health?.status === "offline" ? "danger" : health?.status === "degraded" ? "warning" : undefined}
              meta={health ? (health.issues.length ? t("overview.kpi.healthMeta", { count: num(health.issues.length) }) : t("overview.kpi.healthOk")) : null}
              href="/systeem" drill={t("overview.drill.system")} />
            <KpiCard label={t("overview.kpi.active")} icon={Activity} unavailable={!o?.downloads.available}
              value={num(o?.downloads.total_active ?? 0)}
              meta={o?.downloads.available ? t("overview.kpi.activeMeta", { usenet: num(o.downloads.active.usenet), torrent: num(o.downloads.active.torrent) }) : o?.downloads.reason}
              href="/downloads" drill={t("overview.drill.downloads")} />
            <KpiCard label={t("overview.kpi.failed")} icon={CircleX} unavailable={!o?.failures.available}
              tone={o?.failures.last_7d ? "danger" : undefined} value={num(o?.failures.last_7d ?? 0)}
              meta={o?.failures.available ? t("overview.kpi.failedMeta", { day: num(o.failures.last_24h), rate: o.failures.failure_rate_7d == null ? "—" : pct(o.failures.failure_rate_7d * 100, 1) }) : o?.failures.reason}
              href="/downloads" drill={t("overview.drill.downloads")} />
            <KpiCard label={t("overview.kpi.costs")} icon={Coins} unavailable={!cost?.available}
              value={cost?.available ? money((cost.monthly_cents ?? 0) / 100, cost.currency) : "—"}
              meta={cost?.available ? (cost.unknown?.length ? t("overview.kpi.costsUnknown", { count: num(cost.unknown.length) }) : t("overview.kpi.costsMeta", { count: num(cost.services ?? 0) })) : cost?.reason}
              href="/kosten" drill={t("overview.drill.costs")} />
          </section>

          <div className="grid" style={{ marginTop: 16 }}>
            <WaitingCard className="span-12" />
            <AutopilotCard className="span-12" />
            {o ? (
              <>
                <FailureLocations o={o} />
                <Connectors o={o} />
                <Recommendations o={o} />
                <Changes o={o} />
                <SystemCard o={o} />
                <FailureReasons o={o} />
                <RecentAudit o={o} />
              </>
            ) : <Card className="span-12"><StateView kind="loading" /></Card>}
          </div>
        </>
      )}
    </>
  );
}

function Unavail({ reason }: { reason?: string | null }) {
  const { t } = useI18n();
  return <StateView kind="unavailable" compact title={t("state.unavailableTitle")} body={reason ?? undefined} />;
}

function FailureLocations({ o }: { o: Overview }) {
  const { t, num } = useI18n();
  const f = o.failures;
  return (
    <Card className="span-7" title={t("overview.failureLocations")} sub={t("overview.failureLocationsSub")} icon={MapPin} flush
      actions={<Link className="link-more" href="/downloads">{t("nav.downloads")} <ArrowRight className="ico ico-sm" aria-hidden="true" /></Link>}>
      {!f.available ? <Unavail reason={f.reason} /> : f.by_location.length === 0 ? (
        <StateView kind="empty" compact title={t("overview.noFailures")} />
      ) : (
        <div className="list">
          {f.by_location.map((l) => {
            const top = Object.entries(l.reasons).sort((a, b) => b[1] - a[1]).slice(0, 2)
              .map(([r, n]) => `${f.by_reason.find((x) => x.reason === r)?.label ?? r} (${num(n)})`).join(" · ");
            return (
              <div className="list-row list-row--wrap" key={`${l.kind}:${l.id}`}>
                <span className="list-icon list-icon--danger"><CircleX className="ico" aria-hidden="true" /></span>
                <div className="list-main"><div className="list-title">{l.label}</div><div className="list-sub" style={{ whiteSpace: "normal" }}>{top}</div></div>
                <div className="list-aside"><StatusChip tone="neutral" icon={null}>{t(LOC_LABEL[l.kind])}</StatusChip><strong className="num">{num(l.count)}</strong></div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

function Connectors({ o }: { o: Overview }) {
  const { t, relative } = useI18n();
  const items = o.connectors.items;
  return (
    <Card className="span-5" title={t("overview.connectors")} icon={Plug} flush
      actions={<Link className="link-more" href="/instellingen?tab=verbindingen">{t("overview.manageConnectors")}</Link>}>
      {items.length === 0 ? <Unavail reason={o.connectors.reason} /> : (
        <div className="list">
          {items.map((c) => (
            <div className="list-row" key={c.id}>
              <div className="list-main">
                <div className="list-title">{c.label}</div>
                <div className="list-sub">{c.configured ? (c.last_checked_at ? t("overview.checked", { age: relative(c.last_checked_at) }) : t("overview.neverChecked")) : t("status.notConfigured")}{c.version ? ` · ${/^v/i.test(c.version) ? c.version : `v${c.version}`}` : ""}</div>
              </div>
              {c.configured ? <StatusChip tone={healthTone(c.status)} title={c.message ?? undefined}>{t(healthLabel(c.status))}</StatusChip>
                : <StatusChip tone="neutral">{t("status.notConfigured")}</StatusChip>}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function Recommendations({ o }: { o: Overview }) {
  const { t, num } = useI18n();
  const r = o.recommendations;
  return (
    <Card className="span-4" title={<>{t("overview.recommendations")} {r.open ? <span className="chip">{num(r.open)}</span> : null}</>} icon={Lightbulb} flush
      actions={<Link className="link-more" href="/automatisering">{t("overview.viewAll")} <ArrowRight className="ico ico-sm" aria-hidden="true" /></Link>}>
      <div className="toolbar" style={{ padding: "12px 16px 4px" }}>
        {RISKS.map((k) => <span key={k} className="row" style={{ gap: 6 }}><RiskChip risk={k} /><strong className="num">{num(r.by_risk[k] ?? 0)}</strong></span>)}
      </div>
      {r.top.length === 0 ? <StateView kind="empty" compact title={t("overview.noRecommendations")} /> : (
        <div className="list">
          {r.top.map((x) => (
            <Link className="list-row" key={x.id} href="/automatisering">
              <div className="list-main"><div className="list-title">{x.title}</div><div className="list-sub">{t(`automation.area.${x.area}` as MessageKey) || x.area}</div></div>
              <RiskChip risk={x.risk} />
            </Link>
          ))}
        </div>
      )}
    </Card>
  );
}

function Changes({ o }: { o: Overview }) {
  const { t, num, relative } = useI18n();
  const c = o.changes;
  return (
    <Card className="span-4" title={<>{t("overview.changes")} {c.open ? <span className="chip">{num(c.open)}</span> : null}</>} icon={GitPullRequestArrow} flush
      sub={c.failed_7d ? t("overview.failedChanges", { count: num(c.failed_7d) }) : undefined}
      actions={<Link className="link-more" href="/automatisering?tab=wijzigingen">{t("overview.viewAll")} <ArrowRight className="ico ico-sm" aria-hidden="true" /></Link>}>
      {c.items.length === 0 ? <StateView kind="empty" compact title={t("overview.noChanges")} /> : (
        <div className="list">
          {c.items.map((x) => (
            <Link className="list-row" key={x.id} href="/automatisering?tab=wijzigingen">
              <div className="list-main"><div className="list-title">{x.title}</div><div className="list-sub mono">{x.target} · {relative(x.created_at)}</div></div>
              <RiskChip risk={x.risk} />
            </Link>
          ))}
        </div>
      )}
    </Card>
  );
}

function SystemCard({ o }: { o: Overview }) {
  const { t, num, duration } = useI18n();
  const w = o.jobs.worker;
  return (
    <Card className="span-4" title={t("overview.system")} icon={ShieldCheck}>
      <dl className="kv">
        <dt>{t("overview.mediaguard")}</dt>
        <dd><StatusChip tone={MG_TONE[o.mediaguard.mode] ?? "neutral"} title={o.mediaguard.reason ?? undefined}>{o.mediaguard.mode_label}</StatusChip>
          {o.mediaguard.stale_s != null ? <span className="t-3"> · {t("overview.mgChecked", { age: duration(o.mediaguard.stale_s) })}</span> : null}
          {o.mediaguard.wanted_sync_s != null ? (
            <span className="t-3"> · {t(o.mediaguard.wanted_sync_partial ? "overview.mgSyncedPartial" : "overview.mgSynced", { age: duration(o.mediaguard.wanted_sync_s) })}</span>
          ) : null}</dd>
        <dt>{t("overview.backup")}</dt>
        <dd>{o.backup.available ? (
          <StatusChip tone={o.backup.stale ? "warning" : "success"} icon={DatabaseBackup}>{duration(o.backup.age_s)}{o.backup.stale ? ` · ${t("overview.backupOld")}` : ""}</StatusChip>
        ) : <StatusChip tone="warning">{t("overview.backupNone")}</StatusChip>}</dd>
        <dt>{t("overview.worker")}</dt>
        <dd><StatusChip tone={w.healthy ? "success" : "warning"}>{w.healthy ? t("overview.workerOk") : w.seen ? t("overview.workerStale") : t("overview.workerNever")}</StatusChip></dd>
        <dt>{t("overview.jobsFailing")}</dt>
        <dd>{o.jobs.failing_24h ? <Link href="/systeem"><StatusChip tone="danger">{num(o.jobs.failing_24h)}</StatusChip></Link> : <StatusChip tone="success">0</StatusChip>}</dd>
        <dt>{t("overview.queue")}</dt>
        <dd>{t("overview.queueValue", { queued: num(o.jobs.queued), running: num(o.jobs.running) })}</dd>
      </dl>
      {o.health.issues.length ? (
        <div style={{ marginTop: 12 }}>
          <div className="label">{t("overview.issues")}</div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5 }}>{o.health.issues.map((i) => <li key={i}>{i}</li>)}</ul>
        </div>
      ) : null}
    </Card>
  );
}

function FailureReasons({ o }: { o: Overview }) {
  const { t, num } = useI18n();
  const f = o.failures;
  if (!f.available || f.by_reason.length === 0) return null;
  return (
    <Card className="span-12" title={t("overview.failureReasons")} icon={CircleX} flush>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
        <table className="table table--compact table--stack">
          <thead><tr><th>{t("overview.reason")}</th><th className="num">{t("overview.last24h")}</th><th className="num">{t("overview.last7d")}</th></tr></thead>
          <tbody>{f.by_reason.map((r) => (
            <tr key={r.reason}>
              <td className="cell-primary">{r.label}</td>
              <td data-label={t("overview.last24h")} className="num">{num(r.last_24h)}</td>
              <td data-label={t("overview.last7d")} className="num">{num(r.last_7d)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </Card>
  );
}

function RecentAudit({ o }: { o: Overview }) {
  const { t, relative } = useI18n();
  return (
    <Card className="span-12" title={t("overview.audit")} icon={History}
      actions={<Link className="link-more" href="/logs">{t("overview.viewAll")} <ArrowRight className="ico ico-sm" aria-hidden="true" /></Link>}>
      {o.recent_audit.length === 0 ? <StateView kind="empty" compact /> : (
        <Timeline items={o.recent_audit.map((a) => ({
          title: localizeAuditSummary(a.summary) ?? a.action,
          meta: <><span className="mono">{a.action}</span> · {a.actor} · {relative(a.ts)}</>,
          tone: a.action.includes("failed") ? "danger" : a.action.includes("applied") ? "success" : "neutral",
        }))} />
      )}
    </Card>
  );
}
