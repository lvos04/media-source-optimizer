"use client";

import { Ban, CheckCheck, Gauge, History, KeyRound, ListChecks, RefreshCw, Scale, ShieldCheck, ShieldAlert, TableProperties } from "lucide-react";

import { Button, Card, InlineState, PageHeader, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { ConnectorList } from "@/components/mso/connector-form";
import { CompatTable, MgModeChip, QuotaMeter } from "@/components/mso/mediaguard/parts";
import { useAction } from "@/lib/api/actions";
import { useResource } from "@/lib/api/hooks";
import type { MgSearchRequest, MgStatus } from "@/lib/api/recovery";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const REQUEST_TONE: Record<MgSearchRequest["status"], "success" | "warning" | "danger" | "accent"> = {
  sent: "success", pending: "accent", failed: "danger", refused_quota: "warning", refused_mediaguard_quota: "warning",
};
const REQUEST_KEY = {
  sent: "mg.req.sent", pending: "mg.req.pending", failed: "mg.req.failed", refused_quota: "mg.req.refusedQuota",
  refused_mediaguard_quota: "mg.req.refusedMgQuota",
} as const;

export default function MediaGuardPage() {
  const { t, num, relative, duration } = useI18n();
  const { can } = useSession();
  const status = useResource<MgStatus>("/mediaguard/status", undefined, 60_000);
  const requests = useResource<{ items: MgSearchRequest[] }>("/mediaguard/search-requests", { limit: 50 }, 60_000);
  const { run, pending } = useAction();
  const isOperator = can("operator");
  const isAdmin = can("admin");
  const s = status.data;

  const probe = async () => { const r = await run<MgStatus>("probe", "post", "/mediaguard/probe", undefined, t("mg.probed")); if (r) status.setData(() => r); };
  const sync = async () => { if (await run("sync", "post", "/mediaguard/sync", undefined, t("mg.syncQueued"))) window.setTimeout(() => void status.reload(), 3000); };
  const setMethod = async (method: "apikey" | "user" | "token") => {
    if (await run("method", "put", "/connectors/mediaguard", { options: { auth_method: method } }, t("mg.auth.saved"))) void probe();
  };

  return (
    <>
      <PageHeader title={t("nav.mediaguard")} sub={t("mg.sub")}
        actions={<>
          <Button icon={RefreshCw} pending={pending === "probe"} disabled={!isOperator || !s?.configured}
            title={!isOperator ? t("mg.needOperator") : !s?.configured ? t("mg.notConfigured") : undefined} onClick={() => void probe()}>{t("mg.probe")}</Button>
          <Button variant="primary" icon={RefreshCw} pending={pending === "sync"} disabled={!isOperator || !s?.configured}
            title={!isOperator ? t("mg.needOperator") : !s?.configured ? t("mg.notConfigured") : undefined} onClick={() => void sync()}>{t("mg.syncNow")}</Button>
        </>} />

      <div className="grid">
        <Card className="span-8" title={t("mg.connection")} icon={ShieldCheck} actions={<MgModeChip mode={s?.mode} />}>
          <ResourceView resource={status} endpoint="GET /api/v1/mediaguard/status">
            {(d) => (
              <div className="stack">
                <InlineState tone={d.mode === "connected" ? "success" : d.mode === "degraded" ? "warning" : "info"}>{d.reason}</InlineState>
                <dl className="kv">
                  <dt>{t("mg.version")}</dt><dd className="mono">{d.version ?? "—"}</dd>
                  <dt>{t("mg.role")}</dt><dd>{d.role ? <StatusChip tone={d.role === "operator" ? "success" : d.role === "admin" ? "warning" : "info"}>{d.role}</StatusChip> : "—"}</dd>
                  <dt>{t("mg.authMethod")}</dt><dd>{d.auth_method === "apikey" ? t("mg.auth.apikey") : d.auth_method === "token" ? t("mg.auth.token") : d.auth_method === "user" ? t("mg.auth.user") : "—"}</dd>
                  <dt>{t("mg.reachable")}</dt><dd>{d.configured ? (d.reachable ? <StatusChip tone="success">{t("mg.yes")}</StatusChip> : <StatusChip tone="danger">{t("mg.no")}</StatusChip>) : t("mg.notConfiguredShort")}</dd>
                  <dt>{t("mg.emergency")}</dt><dd>{d.emergency_stop ? <StatusChip tone="danger" icon={ShieldAlert}>{t("mg.emergencyActive")}</StatusChip> : d.emergency_stop === false ? <StatusChip tone="success">{t("mg.emergencyOff")}</StatusChip> : "—"}</dd>
                  <dt>{t("mg.latency")}</dt><dd className="num">{d.latency_ms != null ? `${num(d.latency_ms)} ms` : "—"}</dd>
                  <dt>{t("mg.checked")}</dt><dd>{d.checked_at ? relative(d.checked_at) : "—"}</dd>
                  <dt>{t("mg.searchRoute")}</dt><dd>{d.search.available ? <StatusChip tone="success">{t("mg.available")}</StatusChip> : <span>{d.search.reason ?? t("mg.unavailable")}</span>}</dd>
                  <dt>{t("mg.autoSearch")}</dt><dd>{d.search.auto_enabled
                    ? (d.search.automation_allowed ? <StatusChip tone="warning">{t("mg.autoOn")}</StatusChip> : <StatusChip tone="neutral">{t("mg.autoBlocked")}</StatusChip>)
                    : <StatusChip tone="neutral">{t("mg.autoOff")}</StatusChip>}</dd>
                </dl>
                {d.error ? <p className="t-danger" style={{ fontSize: 12 }}>{d.error}</p> : null}
                {d.warnings.map((w) => <InlineState key={w} tone="warning">{w}</InlineState>)}
              </div>
            )}
          </ResourceView>
        </Card>

        <Card className="span-4" title={t("mg.auth.title")} icon={KeyRound}>
          <div className="stack">
            <p style={{ fontSize: 13 }}>{t("mg.auth.advice")}</p>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5 }}>
              <li>{t("mg.auth.apikeyAdvice")}</li>
              <li>{t("mg.auth.operator")}</li>
              <li>{t("mg.auth.viewer")}</li>
              <li>{t("mg.auth.tokenWarn")}</li>
            </ul>
            <Segmented<"apikey" | "user" | "token"> label={t("mg.authMethod")} value={s?.auth_method ?? "apikey"} onChange={(v) => void setMethod(v)}
              options={[
                { value: "apikey", label: t("mg.auth.apikey"), disabled: !isAdmin || pending === "method", title: isAdmin ? undefined : t("mg.needAdmin") },
                { value: "user", label: t("mg.auth.user"), disabled: !isAdmin || pending === "method", title: isAdmin ? undefined : t("mg.needAdmin") },
                { value: "token", label: t("mg.auth.token"), disabled: !isAdmin || pending === "method", title: isAdmin ? undefined : t("mg.needAdmin") },
              ]} />
            {s?.auth_method === "token" ? <InlineState tone="warning">{t("mg.auth.tokenActive")}</InlineState> : null}
          </div>
        </Card>

        <Card className="span-12" title={t("mg.setup")} icon={ListChecks} sub={t("mg.setupSub")}>
          <ConnectorList only={["mediaguard"]} canEdit={isAdmin} disabledReason={isAdmin ? undefined : t("mg.needAdmin")} />
        </Card>

        <Card className="span-6" title={t("mg.quota.title")} icon={Gauge}>
          <ResourceView resource={status} endpoint="GET /api/v1/mediaguard/status" compact>
            {(d) => <QuotaMeter quota={d.quota} />}
          </ResourceView>
        </Card>

        <Card className="span-6" title={t("mg.sync.title")} icon={RefreshCw}>
          <ResourceView resource={status} endpoint="GET /api/v1/mediaguard/status" compact
            isEmpty={(d) => !d.last_sync}
            empty={<StateView kind="empty" compact title={t("mg.sync.never")} body={s?.configured ? t("mg.sync.neverBody") : t("mg.notConfigured")} />}>
            {(d) => {
              const ls = d.last_sync!;
              return (
                <div className="stack">
                  <div className="row wrap">
                    {ls.ok ? <StatusChip tone="success">{t("mg.sync.ok")}</StatusChip> : <StatusChip tone="danger">{t("mg.sync.failed")}</StatusChip>}
                    {ls.complete === false && ls.ok ? <StatusChip tone="warning">{t("mg.sync.partial")}</StatusChip> : null}
                    {ls.backoff_until ? <StatusChip tone="warning">{t("mg.sync.backoff", { when: relative(ls.backoff_until) })}</StatusChip> : null}
                  </div>
                  <dl className="kv">
                    <dt>{t("mg.sync.last")}</dt><dd>{ls.finished_at ? relative(ls.finished_at) : "—"}</dd>
                    <dt>{t("mg.sync.lastOk")}</dt><dd>{ls.last_success_at ? relative(ls.last_success_at) : "—"}</dd>
                    <dt>{t("mg.sync.items")}</dt><dd className="num">{num(ls.items ?? 0)}{ls.total_open != null ? ` / ${num(ls.total_open)}` : ""}</dd>
                    <dt>{t("mg.sync.duration")}</dt><dd>{ls.duration_ms != null ? duration(ls.duration_ms / 1000) : "—"}</dd>
                  </dl>
                  {ls.last_error ? <p className="t-danger" style={{ fontSize: 12 }}>{ls.last_error}</p> : null}
                  <p className="hint">{t("mg.sync.hint")}</p>
                </div>
              );
            }}
          </ResourceView>
        </Card>

        <Card className="span-12" title={t("mg.compat.title")} icon={TableProperties} flush>
          <ResourceView resource={status} endpoint="GET /api/v1/mediaguard/status">
            {(d) => <CompatTable compat={d.compat} />}
          </ResourceView>
        </Card>

        <Card className="span-12" title={t("mg.own.title")} icon={Scale}>
          <div className="grid" style={{ gap: 16 }}>
            <div className="span-6">
              <h3 className="card-title" style={{ fontSize: 13 }}><CheckCheck className="ico t-success" aria-hidden="true" />{t("mg.own.does")}</h3>
              <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 13 }}>
                <li>{t("mg.own.does1")}</li><li>{t("mg.own.does2")}</li><li>{t("mg.own.does3")}</li><li>{t("mg.own.does4")}</li>
              </ul>
            </div>
            <div className="span-6">
              <h3 className="card-title" style={{ fontSize: 13 }}><Ban className="ico t-danger" aria-hidden="true" />{t("mg.own.never")}</h3>
              <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 13 }}>
                <li>{t("mg.own.never1")}</li><li>{t("mg.own.never2")}</li><li>{t("mg.own.never3")}</li><li>{t("mg.own.never4")}</li><li>{t("mg.own.never5")}</li>
              </ul>
            </div>
          </div>
        </Card>

        <Card className="span-12" title={t("mg.req.title")} icon={History} flush>
          <ResourceView resource={requests} endpoint="GET /api/v1/mediaguard/search-requests" isEmpty={(d) => d.items.length === 0}
            empty={<StateView kind="empty" compact title={t("mg.req.empty")} body={t("mg.req.emptyBody")} />}>
            {(d) => (
              <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
                <table className="table table--stack">
                  <thead><tr><th>{t("mg.req.when")}</th><th>{t("mg.req.item")}</th><th>{t("mg.req.status")}</th><th>{t("mg.req.trigger")}</th><th>{t("mg.req.actor")}</th></tr></thead>
                  <tbody>
                    {d.items.map((r) => (
                      <tr key={r.id}>
                        <td data-label={t("mg.req.when")}>{relative(r.requested_at)}</td>
                        <td data-label={t("mg.req.item")} className="mono">mg:{r.mg_id}{r.instance_id ? ` · ${r.instance_id}` : ""}</td>
                        <td data-label={t("mg.req.status")}>
                          <StatusChip tone={REQUEST_TONE[r.status]}>{t(REQUEST_KEY[r.status])}</StatusChip>
                          {r.error ? <div className="hint">{r.error}</div> : null}
                        </td>
                        <td data-label={t("mg.req.trigger")}>{r.trigger === "auto" ? t("mg.req.auto") : t("mg.req.manual")}</td>
                        <td data-label={t("mg.req.actor")}>{r.actor}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </ResourceView>
        </Card>
      </div>
    </>
  );
}
