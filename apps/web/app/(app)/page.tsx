// Overzicht — layout follows MediaGuard Ultimate's overzicht (KPI row + card grid), data from GET /api/v1/overview.
"use client";

import {
  Activity, ArrowRight, Coins, DatabaseBackup, GitPullRequestArrow, HeartPulse, History, Lightbulb, MapPin, OctagonX, Plug, ShieldCheck,
} from "lucide-react";
import Link from "next/link";

import { KpiCard, Timeline } from "@/components/mg/domain";
import { Card, LinkButton, PageHeader, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView, StaleMark, StateView } from "@/components/mg/states";
import { AutopilotCard, WaitingCard } from "@/components/mso/automation/autopilot-summary";
import { GoalsCard } from "@/components/mso/overview/goals-card";
import { RiskChip } from "@/components/mso/risk-chip";
import type { Risk } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { localizeAuditSummary, type Overview } from "@/lib/api/insights";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useMode } from "@/lib/mode";
import { healthLabel, healthTone, peilStatusText } from "@/lib/status";

const RISKS: Risk[] = ["toestemming_nodig", "automatisch_veilig", "advies"];
const LOC_LABEL: Record<string, MessageKey> = { source: "overview.loc.source", provider: "overview.loc.provider", client: "overview.loc.client" };
// "connected" is a link state, not health (Peil §4.3): neutral "Gekoppeld" pill.
const MG_TONE: Record<string, Tone> = { connected: "linked", degraded: "warning", standalone: "neutral" };

export default function OverviewPage() {
  const { t, date, num, pct, money, relative } = useI18n();
  const ov = useResource<Overview>("/overview", undefined, 30_000);
  const o = ov.data;
  const loading = ov.loading || (!o && !ov.error);
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
      {/* goals → Wacht op jou → Wat MSO deed first (own endpoints: also shown when GET /overview fails, so the
          topbar pill's /#wacht-op-jou always has its anchor); the KPI row and the /overview cards below */}
      <div className="grid" style={{ marginBottom: 16 }}>
        <GoalsCard />
        <WaitingCard className="span-12" />
        <AutopilotCard className="span-12" />
      </div>
      {ov.error && !o ? (
        <ResourceView resource={ov} endpoint="GET /api/v1/overview">{() => null}</ResourceView>
      ) : (
        <>
          {/* Peil: verouderd is visible (dashed + hatched mark, status Onbekend) — the last known numbers stay */}
          {ov.stale && o ? <div className="mb-3"><StaleMark>{t("state.stale", { age: relative(ov.fetchedAt) })}</StaleMark></div> : null}
          <section className="kpi-grid" aria-label={t("overview.kpis")}>
            <KpiCard label={t("overview.kpi.health")} icon={HeartPulse} loading={loading} unavailable={!loading && (!o || health?.status === "unknown" || ov.stale)}
              reason={ov.stale ? t("state.staleShort") : undefined}
              value={health ? <StatusChip tone={healthTone(health.status)} size="lg">{healthTxt}</StatusChip> : healthTxt}
              meta={health ? (health.issues.length ? t("overview.kpi.healthMeta", { count: num(health.issues.length) }) : t("overview.kpi.healthOk")) : null}
              href="/systeem" drill={t("overview.drill.system")} />
            <KpiCard label={t("overview.kpi.active")} icon={Activity} loading={loading} unavailable={!loading && !o?.downloads.available}
              reason={o?.downloads.reason ?? undefined}
              value={num(o?.downloads.total_active ?? 0)}
              meta={o?.downloads.available ? t("overview.kpi.activeMeta", { usenet: num(o.downloads.active.usenet), torrent: num(o.downloads.active.torrent) }) : o?.downloads.reason}
              href="/downloads" drill={t("overview.drill.downloads")} />
            <KpiCard label={t("overview.kpi.failed")} icon={OctagonX} loading={loading} unavailable={!loading && !o?.failures.available}
              reason={o?.failures.reason ?? undefined}
              value={num(o?.failures.last_7d ?? 0)}
              meta={o?.failures.available ? t("overview.kpi.failedMeta", { day: num(o.failures.last_24h), rate: o.failures.failure_rate_7d == null ? "—" : pct(o.failures.failure_rate_7d * 100, 1) }) : o?.failures.reason}
              href="/downloads" drill={t("overview.drill.downloads")} />
            {/* B05-008: never an amount that looks complete when services have no price ("Deels onbekend") */}
            <KpiCard label={t("overview.kpi.costs")} icon={Coins} loading={loading} unavailable={!loading && !cost?.available}
              reason={cost?.reason ?? undefined}
              value={cost?.available ? money((cost.monthly_cents ?? 0) / 100, cost.currency) : "—"}
              meta={cost?.available ? (cost.unknown?.length
                ? <><StatusChip tone="unknown">{t("overview.kpi.costsPartlyUnknown")}</StatusChip> {t("overview.kpi.costsUnknown", { count: num(cost.unknown.length) })}</>
                : t("overview.kpi.costsMeta", { count: num(cost.services ?? 0) })) : cost?.reason}
              href="/kosten" drill={t("overview.drill.costs")} />
          </section>

          <div className="grid" style={{ marginTop: 16 }}>
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
                <span className="list-icon list-icon--danger"><OctagonX className="ico" aria-hidden="true" /></span>
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
  const { t, relative, duration, num } = useI18n();
  const items = o.connectors.items;
  return (
    <Card className="span-5" title={t("overview.connectors")} icon={Plug} flush
      actions={<Link className="link-more" href="/instellingen?tab=verbindingen">{t("overview.manageConnectors")}</Link>}>
      {items.length === 0 ? <Unavail reason={o.connectors.reason} /> : (
        <div className="list">
          {items.map((c) => {
            // background monitoring: a connector that is down says so, with how long ("Onbereikbaar sinds 2d 4u")
            const down = c.configured && (c.status === "offline" || c.status === "auth_failed");
            const downKey: MessageKey = c.status === "auth_failed" ? "maintenance.conn.authSince"
              : c.failure_kind === "timeout" ? "maintenance.conn.timeoutSince" : "maintenance.conn.offlineSince";
            const chip = c.status === "auth_failed" ? t("maintenance.status.authFailed")
              : down && c.failure_kind === "timeout" ? t("maintenance.status.timeout") : t(healthLabel(c.status));
            return (
              <div className="list-row list-row--wrap" key={c.id} data-testid={`connector-${c.id}`}>
                <div className="list-main">
                  <div className="list-title">{c.label}</div>
                  {down && c.down_for_s != null ? (
                    <div className="list-sub t-danger" style={{ whiteSpace: "normal" }}>
                      {t(downKey, { age: duration(c.down_for_s) })}{c.last_ok_at ? ` · ${t("maintenance.conn.lastOk", { when: relative(c.last_ok_at) })}` : ""}
                    </div>
                  ) : (
                    <div className="list-sub">{c.configured ? (c.last_checked_at ? t("overview.checked", { age: relative(c.last_checked_at) }) : t("overview.neverChecked")) : t("status.notConfigured")}{c.version ? ` · ${/^v/i.test(c.version) ? c.version : `v${c.version}`}` : ""}</div>
                  )}
                  {down && c.message ? <div className="list-sub" style={{ whiteSpace: "normal" }}>{c.message}</div> : null}
                  {down && c.job_failures_24h ? <div className="list-sub">{t("maintenance.conn.jobsFailed", { count: num(c.job_failures_24h) })}</div> : null}
                </div>
                <div className="list-aside">
                  {c.configured ? <StatusChip tone={healthTone(c.status)} title={c.message ?? undefined}>{chip}</StatusChip>
                    : <StatusChip tone="unlinked">{t("status.notConfigured")}</StatusChip>}
                  {down ? <Link className="link-more" href="/instellingen?tab=verbindingen">{t("maintenance.conn.fix")}</Link> : null}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

function Recommendations({ o }: { o: Overview }) {
  const { t, num } = useI18n();
  const r = o.recommendations;
  return (
    <Card className="span-4" title={<>{t("calm.otherRecs")} {r.open ? <StatusChip tone="neutral">{num(r.open)}</StatusChip> : null}</>} icon={Lightbulb} flush
      sub={r.waiting ? <Link className="link-more" href="/#wacht-op-jou">{t("calm.inInbox", { count: num(r.waiting) })}</Link> : undefined}
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
    <Card className="span-4" title={<>{t("overview.changes")} {c.open ? <StatusChip tone="neutral">{num(c.open)}</StatusChip> : null}</>} icon={GitPullRequestArrow} flush
      sub={[t("calm.otherChanges"), c.failed_7d ? t("overview.failedChanges", { count: num(c.failed_7d) }) : null].filter(Boolean).join(" · ")}
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
  // failures caused by a connector outage are shown as that connector's state ("… onbereikbaar sinds …"), not here
  const failing = Math.max(0, o.jobs.failing_24h - (o.connectors.outage_job_failures_24h ?? 0));
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
          o.backup.stale ? <StatusChip tone="warning" icon={DatabaseBackup}>{duration(o.backup.age_s)} · {t("overview.backupOld")}</StatusChip>
            : <span className="row" style={{ gap: 6, display: "inline-flex" }}><DatabaseBackup className="ico" aria-hidden="true" />{duration(o.backup.age_s)}</span>
        ) : <StatusChip tone="warning">{t("overview.backupNone")}</StatusChip>}</dd>
        <dt>{t("overview.worker")}</dt>
        <dd><StatusChip tone={w.healthy ? "healthy" : w.seen ? "warning" : "unknown"}>{w.healthy ? t("overview.workerOk") : w.seen ? t("overview.workerStale") : t("overview.workerNever")}</StatusChip></dd>
        <dt>{t("overview.jobsFailing")}</dt>
        <dd>{failing ? <Link href="/systeem"><StatusChip tone="danger">{num(failing)}</StatusChip></Link> : <span className="num">{num(0)}</span>}</dd>
        <dt>{t("overview.queue")}</dt>
        <dd>{t("overview.queueValue", { queued: num(o.jobs.queued), running: num(o.jobs.running) })}</dd>
      </dl>
      {o.health.issues.length ? (
        <div style={{ marginTop: 12 }}>
          <div className="label">{t("overview.issues")}</div>
          <ul style={{ margin: 0, paddingLeft: 18 }}>{o.health.issues.map((i) => <li key={i}>{peilStatusText(i, t)}</li>)}</ul>
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
    <Card className="span-12" title={t("overview.failureReasons")} icon={OctagonX} flush>
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
