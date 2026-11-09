"use client";

import { ArrowDownUp, GitCompareArrows, Lightbulb, PowerOff, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { DiffViewer } from "@/components/mg/domain";
import { Button, Card, InlineState, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ChangePreview } from "@/components/mso/change-preview";
import { RiskChip } from "@/components/mso/risk-chip";
import { RetryServerButton, ServerAutopilotCell } from "@/components/mso/usenet/autopilot";
import { ExpiryNote, expiryFromDate, ServerTestLine, TestServerButton } from "@/components/mso/usenet/server-test";
import { ConfidenceChip, ProviderStatusChip, SabStatusCell, useRatio } from "@/components/mso/usenet/shared";
import { api, errorMessage } from "@/lib/api/client";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { PrioritySuggestion, SabServerList, ServerTestOverview } from "@/lib/api/usenet";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/** SABnzbd servers (read-only view) + MSO's priority/fallback suggestion → change set (preview → apply → rollback). */
export function PriorityCard() {
  const { t, num, bytes, relative } = useI18n();
  const ratio = useRatio();
  const toast = useToast();
  const { can } = useSession();
  const servers = useResource<SabServerList>("/usenet/sab/servers", { period_days: 30 }, 120_000);
  const suggestion = useResource<PrioritySuggestion>("/usenet/recommendations", { period_days: 30 });
  const tests = useResource<ServerTestOverview>("/usenet/sab/server-tests", undefined, 120_000);
  const [change, setChange] = useState<ChangeSet | null>(null);
  const [pending, setPending] = useState(false);
  const [disableChange, setDisableChange] = useState<ChangeSet | null>(null);
  const [disablePending, setDisablePending] = useState(false);

  const proposeDisable = async () => {
    setDisablePending(true);
    try {
      const ch = await api.post<ChangeSet>("/usenet/recommendations/disable-failing", {});
      setDisableChange(ch);
      toast.push("success", t("usenet.prio.disableProposed"), ch.title);
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setDisablePending(false);
    }
  };

  const onDisableChanged = async (updated: ChangeSet | null) => {
    if (updated) setDisableChange(updated);
    await Promise.all([servers.reload(), suggestion.reload()]);
  };

  const propose = async () => {
    setPending(true);
    try {
      const ch = await api.post<ChangeSet>("/usenet/recommendations/priority", { period_days: 30 });
      setChange(ch);
      toast.push("success", t("usenet.prio.proposed"));
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setPending(false);
    }
  };

  const onChanged = async (updated: ChangeSet | null) => {
    if (updated) setChange(updated);
    await Promise.all([servers.reload(), suggestion.reload()]);
  };

  return (
    <Card title={t("usenet.prio.title")} icon={ArrowDownUp} sub={t("usenet.prio.sub")} className="span-12" flush
      actions={servers.data?.last_stats_at ? <span className="t-3">{t("usenet.prio.lastStats", { when: relative(servers.data.last_stats_at) })}</span> : undefined}>
      <ResourceView resource={servers} endpoint="GET /api/v1/usenet/sab/servers" isEmpty={(d) => !d.items.length}
        empty={<StateView kind="empty" compact />}>
        {(d) => (
          <table className="table table--stack">
            <thead>
              <tr>
                <th>{t("usenet.prio.server")}</th><th>{t("usenet.prio.status")}</th><th>{t("usenet.prio.priority")}</th><th>{t("usenet.prio.enabled")}</th>
                <th>{t("usenet.prio.optional")}</th><th>{t("usenet.providers.connections")}</th>
                <th>{t("usenet.prio.periodBytes", { days: d.period_days })}</th><th>{t("usenet.prio.successRatio")}</th>
              </tr>
            </thead>
            <tbody>
              {d.items.map((s) => (
                <tr key={s.name}>
                  <td data-label={t("usenet.prio.server")}>
                    <strong>{s.displayname}</strong> <span className="t-3 mono">{s.name}</span>
                    {s.provider ? <div className="row" style={{ gap: 6, marginTop: 2 }}><span className="t-3">{s.provider.label}</span>{s.provider.source !== "sabnzbd" ? <ProviderStatusChip status={s.provider.status} /> : null}</div>
                      : <div className="t-3">{t("usenet.providers.notLinked")}</div>}
                  </td>
                  <td data-label={t("usenet.prio.status")} style={{ minWidth: 200, maxWidth: 340 }}>
                    <SabStatusCell st={s.sab_status} />
                    <ServerAutopilotCell ap={s.autopilot} />
                    {tests.data?.items[s.name] ? (
                      <div style={{ marginTop: 4 }}>
                        <ServerTestLine test={tests.data.items[s.name].latest} flapping={tests.data.items[s.name].flapping} />
                      </div>
                    ) : null}
                    {s.expire_date ? (
                      <div style={{ marginTop: 4 }}>
                        <ExpiryNote expiry={expiryFromDate(s.expire_date)} />
                      </div>
                    ) : null}
                    <div className="row wrap" style={{ gap: 6, marginTop: 4 }}>
                      <TestServerButton server={s.name} name={s.displayname} onDone={() => Promise.all([servers.reload(), tests.reload()]).then(() => undefined)} />
                      {!s.enable ? (
                        <RetryServerButton server={s.name} name={s.displayname} onDone={() => Promise.all([servers.reload(), suggestion.reload()]).then(() => undefined)} />
                      ) : null}
                    </div>
                  </td>
                  <td data-label={t("usenet.prio.priority")}>{num(s.priority)}</td>
                  <td data-label={t("usenet.prio.enabled")}>
                    <StatusChip tone={s.enable ? "success" : "neutral"}>{t(s.enable ? "usenet.prio.yes" : "usenet.prio.no")}</StatusChip>
                  </td>
                  <td data-label={t("usenet.prio.optional")}>{t(s.optional ? "usenet.prio.yes" : "usenet.prio.no")}</td>
                  <td data-label={t("usenet.providers.connections")}>{num(s.connections)}</td>
                  <td data-label={t("usenet.prio.periodBytes", { days: d.period_days })}>{bytes(s.period.bytes)}</td>
                  <td data-label={t("usenet.prio.successRatio")}>
                    {s.period.articles_tried ? `${ratio(s.period.success_ratio)} · ${num(s.period.articles_tried)}` : "—"}
                    {s.low_article_success ? (
                      <div style={{ marginTop: 2 }}>
                        <StatusChip tone="warning" icon={TriangleAlert} title={t("usenet.prio.lowSuccessHint")}>{t("usenet.prio.lowSuccess")}</StatusChip>
                      </div>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </ResourceView>
      <div className="card-body stack">
        <ResourceView resource={suggestion} compact endpoint="GET /api/v1/usenet/recommendations">
          {(sg) => (
            <div className="stack">
              <div className="row-between wrap">
                <span className="eyebrow">{t("usenet.prio.suggestion")}</span>
                <RiskChip risk={sg.risk} />
              </div>
              {sg.issues.length ? (
                <div>
                  <span className="eyebrow">{t("usenet.prio.issues")}</span>
                  <ul className="list" style={{ marginTop: 4 }}>
                    {sg.issues.map((i) => (
                      <li key={i.server} className="list-row list-row--wrap">
                        <ProviderStatusChip status={i.status} detail={i.message} />
                        <div className="list-main">
                          <div className="list-title" style={{ whiteSpace: "normal" }}>{i.title}</div>
                          <div className="list-sub" style={{ whiteSpace: "normal" }}>{i.explanation}</div>
                        </div>
                        <RiskChip risk={i.risk} />
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {sg.disable.count || disableChange ? (
                <div className="stack-sm" style={{ border: "1px solid var(--mg-border-soft)", borderRadius: "var(--mg-r)", padding: 12 }}>
                  <div className="row-between wrap">
                    <span className="eyebrow">{t("usenet.prio.disableTitle")}</span>
                    <RiskChip risk="toestemming_nodig" />
                  </div>
                  {sg.disable.count ? (
                    <p className="t-2" style={{ margin: 0 }}>
                      {t("usenet.prio.disableHint", {
                        servers: sg.disable.servers.map((name) => sg.issues.find((i) => i.server === name)?.name ?? name).join(", "),
                      })}
                    </p>
                  ) : null}
                  {!disableChange ? (
                    <div>
                      <Button variant="danger" icon={PowerOff} onClick={() => void proposeDisable()} pending={disablePending}
                        disabled={!can("admin")} title={can("admin") ? undefined : t("usenet.adminOnly")}>
                        {t("usenet.prio.disablePropose", { count: num(sg.disable.count) })}
                      </Button>
                    </div>
                  ) : <ChangePreview change={disableChange} onChanged={onDisableChanged} />}
                </div>
              ) : null}
              {sg.notes.map((n, idx) => <InlineState key={n} tone={idx === 0 && sg.issues.length ? "danger" : "info"}>{n}</InlineState>)}
              {sg.ops.length ? (
                <DiffViewer keyWidth={180} headers={[t("usenet.prio.server"), t("usenet.prio.now"), t("usenet.prio.suggestion")]}
                  rows={sg.ops.map((o) => ({ key: o.path, label: o.label, before: String(o.before), after: `${o.after}${o.reason ? ` — ${o.reason}` : ""}`, change: "changed" as const }))} />
              ) : <p className="t-2" style={{ margin: 0 }}>{t(sg.issues.length ? "usenet.prio.noOpsIssues" : "usenet.prio.noOps")}</p>}
              {sg.insights.length ? (
                <div>
                  <span className="eyebrow">{t("usenet.prio.insights")}</span>
                  <ul className="list" style={{ marginTop: 4 }}>
                    {sg.insights.map((i) => (
                      <li key={`${i.slug}-${i.kind}`} className="list-row">
                        {i.kind === "low_success" ? <TriangleAlert className="ico" aria-hidden="true" /> : <Lightbulb className="ico" aria-hidden="true" />}
                        <div className="list-main"><div className="list-sub" style={{ whiteSpace: "normal" }}>{i.text}</div></div>
                        <ConfidenceChip level={i.confidence} />
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {!change ? (
                <div>
                  <Button variant="primary" icon={GitCompareArrows} onClick={() => void propose()} pending={pending}
                    disabled={!sg.ops.length || !can("admin")} title={!can("admin") ? t("usenet.adminOnly") : !sg.ops.length ? t("usenet.prio.noOps") : undefined}>
                    {t("usenet.prio.propose")}
                  </Button>
                </div>
              ) : null}
            </div>
          )}
        </ResourceView>
        {change ? <ChangePreview change={change} onChanged={onChanged} /> : null}
      </div>
    </Card>
  );
}
