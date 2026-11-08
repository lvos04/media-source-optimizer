"use client";

import { Bot, RotateCcw, RefreshCw } from "lucide-react";
import { useState } from "react";

import { Button, Card, InlineState, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ChangePreview } from "@/components/mso/change-preview";
import { useMode, useWriteGate } from "@/components/mso/mode-gate";
import { TestServerButton } from "@/components/mso/usenet/server-test";
import { api, errorMessage } from "@/lib/api/client";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { RetryProposeResult, RetryRow, ServerAutopilot, UsenetAutopilot } from "@/lib/api/usenet";
import { useI18n, type MessageKey } from "@/lib/i18n";

const AP_TONE: Record<ServerAutopilot["status"], Tone> = { disabled: "warning", probing: "accent", recovered: "success", given_up: "danger" };
const RS_TONE: Record<RetryRow["status"], Tone> = { proposed: "info", retried: "accent", verified: "success", failed: "danger" };

/** Autopilot state of one SABnzbd server: chip + next SABnzbd test (or, without the test, next probe) / trigger. */
export function ServerAutopilotCell({ ap }: { ap: ServerAutopilot | null | undefined }) {
  const { t, relative, num } = useI18n();
  if (!ap) return null;
  const off = ap.status === "disabled" || ap.status === "given_up";
  const tested = ap.test_supported !== false;
  const trigger = ap.probe_trigger === "server_test" ? t("usenetserver.trigger.server_test")
    : ap.probe_trigger ? t(`usenet.ap.trigger.${ap.probe_trigger}` as MessageKey) : null;
  return (
    <div className="stack-sm" style={{ gap: 2, marginTop: 4 }}>
      <StatusChip tone={AP_TONE[ap.status]} pending={ap.status === "probing"} title={ap.last_result ?? undefined}>
        {t(`usenet.ap.st.${ap.status}` as MessageKey)}{ap.reason_label ? ` · ${ap.reason_label.toLowerCase()}` : ""}
      </StatusChip>
      <span className="t-3" style={{ fontSize: 12, whiteSpace: "normal" }}>
        {[
          off && tested && ap.next_test_at ? t("usenetserver.nextTest", { when: relative(ap.next_test_at) }) : null,
          off && !tested && ap.next_probe_at ? t("usenet.ap.nextProbe", { when: relative(ap.next_probe_at) }) : null,
          ap.status === "probing" ? trigger : null,
          ap.status === "probing" && ap.waiting_for_use ? t("usenetserver.waitingForUse") : null,
          off && tested && ap.tests_failed ? t("usenetserver.testsFailed", { n: num(ap.tests_failed) }) : null,
          !tested && ap.probes ? t("usenet.ap.probes", { n: num(ap.probes) }) : null,
        ].filter(Boolean).join(" · ")}
      </span>
      {ap.last_test_at && ap.last_test_label ? (
        <span className="t-3" style={{ fontSize: 12, whiteSpace: "normal" }}>
          {t("usenetserver.lastTest", { when: relative(ap.last_test_at), label: ap.last_test_label.toLowerCase() })}
        </span>
      ) : null}
      {off && !tested ? <span className="t-3" style={{ fontSize: 12, whiteSpace: "normal" }}>{t("usenetserver.noTest")}</span> : null}
    </div>
  );
}

/** "Opnieuw proberen": switch a failing/switched-off SABnzbd server on again and watch it (beheermodus). */
export function RetryServerButton({ server, name, onDone }: { server: string; name: string; onDone: () => void | Promise<void> }) {
  const { t } = useI18n();
  const toast = useToast();
  const gate = useWriteGate("operator");
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      await api.post(`/usenet/sab/servers/${encodeURIComponent(server)}/retry`, {});
      toast.push("success", t("usenet.ap.retryServerDone", { name }));
      await onDone();
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Button size="sm" icon={RotateCcw} onClick={() => void run()} pending={busy} disabled={!gate.allowed}
      title={gate.reason ?? t("usenet.ap.retryServerTitle")}>{t("usenet.ap.retryServer")}</Button>
  );
}

function OpenBundle({ id, onChanged }: { id: number; onChanged: () => void }) {
  const change = useResource<ChangeSet>(`/changes/${id}`);
  return (
    <ResourceView resource={change} compact endpoint={`GET /api/v1/changes/${id}`}>
      {(c) => <ChangePreview change={c} onChanged={async (u) => { if (u) change.setData(() => u); else await change.reload(); onChanged(); }} />}
    </ResourceView>
  );
}

/** Autopilot on the Usenet page: servers MSO switched off/on and failed downloads it retries in SABnzbd. */
export function AutopilotCard({ onChanged }: { onChanged?: () => void }) {
  const { t, num, relative } = useI18n();
  const toast = useToast();
  const { autopilot } = useMode();
  const gate = useWriteGate("operator", false);
  const data = useResource<UsenetAutopilot>("/usenet/autopilot", undefined, 60_000);
  const [busy, setBusy] = useState(false);

  const check = async () => {
    setBusy(true);
    try {
      const res = await api.post<RetryProposeResult>("/usenet/retries/propose", {});
      const key = res.change_status === "applied" ? "applied" : res.change_status === "preview" ? "preview" : res.status;
      if (res.status === "waiting" && res.reason) toast.push("info", res.reason);
      else if (["applied", "preview", "nothing", "quota", "off"].includes(key)) {
        toast.push(key === "applied" ? "success" : "info", t(`usenet.ap.checked.${key}` as MessageKey, { count: num(res.count) }));
      } else if (res.auto_reason) toast.push("info", res.auto_reason);
      await data.reload();
      onChanged?.();
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={t("usenet.ap.title")} icon={Bot} sub={t("usenet.ap.sub")} className="span-12" id="autopilot"
      actions={<Button size="sm" icon={RefreshCw} onClick={() => void check()} pending={busy} disabled={!gate.allowed} title={gate.reason}>{t("usenet.ap.check")}</Button>}>
      <ResourceView resource={data} endpoint="GET /api/v1/usenet/autopilot">
        {(d) => {
          const servers = Object.entries(d.servers).filter((e): e is [string, ServerAutopilot] => Boolean(e[1]));
          const c = d.retry.counts;
          const q = d.retry.quota;
          return (
            <div className="stack">
              <InlineState tone={autopilot ? "success" : "info"}>
                {autopilot ? t("usenetserver.apOn", { hours: num(d.auto_disable_after_h), test: num(d.test_interval_h ?? 6) }) : t("usenet.ap.off")}
              </InlineState>
              <div>
                <span className="eyebrow">{t("usenet.ap.servers")}</span>
                {servers.length ? (
                  <ul className="list" style={{ marginTop: 4 }}>
                    {servers.map(([name, ap]) => (
                      <li key={name} className="list-row list-row--wrap">
                        <div className="list-main" style={{ minWidth: 0, flex: "1 1 220px" }}>
                          <div className="list-title mono" style={{ fontSize: 13 }}>{name}</div>
                          <ServerAutopilotCell ap={ap} />
                        </div>
                        {ap.status === "disabled" || ap.status === "given_up" ? (
                          <div className="row wrap" style={{ gap: 6 }}>
                            <TestServerButton server={name} name={name} onDone={async () => { await data.reload(); onChanged?.(); }} />
                            <RetryServerButton server={name} name={name} onDone={async () => { await data.reload(); onChanged?.(); }} />
                          </div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : <p className="t-3" style={{ margin: "4px 0 0", fontSize: 12.5 }}>{t("usenet.ap.noServers")}</p>}
              </div>
              <div className="stack-sm">
                <span className="eyebrow">{t("usenet.ap.retry")}</span>
                {!d.retry.enabled ? <InlineState tone="neutral">{t("usenet.ap.retryOff")}</InlineState> : (
                  <>
                    <span className="t-2" style={{ fontSize: 13 }}>
                      {t("usenet.ap.retryCounts", {
                        retried: num((c.retried ?? 0) + (c.verified ?? 0) + (c.failed ?? 0)), verified: num(c.verified ?? 0),
                        failed: num(c.failed ?? 0), proposed: num(c.proposed ?? 0),
                      })}
                    </span>
                    <span className="t-3" style={{ fontSize: 12 }}>
                      {t("usenet.ap.retryQuota", { hour: num(q.hour_used), hourMax: num(q.hour_max), day: num(q.day_used), dayMax: num(q.day_max) })}
                      {d.retry.last_run?.at ? ` · ${t("usenet.ap.lastRun", { when: relative(d.retry.last_run.at) })}` : ""}
                    </span>
                    {d.retry.last_run?.retry?.status === "waiting" && d.retry.last_run.retry.reason ? (
                      <InlineState tone="warning">{d.retry.last_run.retry.reason}</InlineState>
                    ) : null}
                  </>
                )}
                {d.retry.open_change ? <OpenBundle id={d.retry.open_change.id} onChanged={() => void data.reload()} /> : null}
                {d.retry.items.length ? (
                  <details>
                    <summary className="t-2" style={{ cursor: "pointer", fontSize: 12.5 }}>{t("usenet.ap.recent")}</summary>
                    <ul className="list" style={{ marginTop: 4 }}>
                      {d.retry.items.slice(0, 20).map((r) => (
                        <li key={r.id} className="list-row list-row--wrap">
                          <div className="list-main" style={{ minWidth: 0, flex: "1 1 220px" }}>
                            <div className="list-title" style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{r.title}</div>
                            <div className="list-sub">{[r.reason_label, r.retried_at ? relative(r.retried_at) : null, r.error].filter(Boolean).join(" · ")}</div>
                          </div>
                          <StatusChip tone={RS_TONE[r.status]}>{t(`usenet.ap.rs.${r.status}` as MessageKey)}</StatusChip>
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </div>
            </div>
          );
        }}
      </ResourceView>
    </Card>
  );
}
