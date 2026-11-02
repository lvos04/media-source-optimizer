"use client";

import { Gauge, ListOrdered, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { useState } from "react";

import { Button, Card, InlineState, StatusChip } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ChangeDialog, useRate } from "@/components/mso/torrent/shared";
import { api, errorMessage } from "@/lib/api/client";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { PreferenceItem, TorrentPolicy } from "@/lib/api/torrent";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

function usePreview(onDone: () => void) {
  const toast = useToast();
  const { t } = useI18n();
  const [change, setChange] = useState<ChangeSet | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const create = async (key: string, changes: Record<string, unknown>, reason: string) => {
    setPending(key);
    try {
      setChange(await api.post<ChangeSet>("/torrent/preferences/preview", { changes, reason }));
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(null);
    }
  };
  const dialog = <ChangeDialog change={change} onClose={() => setChange(null)} onChanged={onDone} />;
  return { create, pending, dialog };
}

export function PreferencesCard() {
  const { t, num } = useI18n();
  const rate = useRate();
  const prefs = useResource<{ items: PreferenceItem[]; updated_at: string | null; stale: boolean }>("/torrent/preferences", undefined, 60_000);
  const show = (p: PreferenceItem) => {
    if (!p.present) return <span className="t-3">{t("torrent.prefs.absent")}</span>;
    if (p.kind === "bool") return p.value ? t("torrent.on") : t("torrent.off");
    if (p.kind === "rate") return Number(p.value) === 0 ? t("torrent.unlimited") : rate(Number(p.value));
    if (typeof p.value === "number") return num(p.value, 2);
    return <span className="mono">{String(p.value ?? "—")}</span>;
  };
  return (
    <Card className="span-6" title={t("torrent.prefs.title")} icon={SlidersHorizontal} flush>
      <ResourceView resource={prefs} endpoint="GET /api/v1/torrent/preferences" compact>
        {(d) => (
          <div className="table-wrap">
            <table className="table table--stack table--compact">
              <thead><tr><th>{t("torrent.prefs.setting")}</th><th>{t("torrent.prefs.value")}</th><th>{t("torrent.prefs.bounds")}</th></tr></thead>
              <tbody>{d.items.map((p) => (
                <tr key={p.key}>
                  <td className="cell-primary">{p.label}<div className="t-3 mono" style={{ fontSize: 11 }}>{p.key}</div></td>
                  <td data-label={t("torrent.prefs.value")}>{show(p)}</td>
                  <td data-label={t("torrent.prefs.bounds")} className="t-3">{p.min !== null || p.max !== null ? `${p.min ?? "…"} – ${p.max ?? "…"}` : "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </ResourceView>
    </Card>
  );
}

export function PolicyCards() {
  const { t, num } = useI18n();
  const rate = useRate();
  const { can } = useSession();
  const isOperator = can("operator");
  const policy = useResource<TorrentPolicy>("/torrent/policy", undefined, 60_000);
  const { create, pending, dialog } = usePreview(() => void policy.reload());
  return (
    <>
      <Card className="span-6" title={t("torrent.speed.title")} icon={Gauge}>
        <ResourceView resource={policy} endpoint="GET /api/v1/torrent/policy" compact>
          {(d) => {
            const s = d.speed;
            return (
              <div className="stack">
                <dl className="kv">
                  <dt>{t("torrent.speed.line")}</dt><dd>{s.line_down_mbit ? `${num(s.line_down_mbit)} / ${num(s.line_up_mbit)} Mbit/s` : t("torrent.speed.lineUnknown")}</dd>
                  <dt>{t("torrent.speed.cap")}</dt><dd>{s.max_upload_bps ? `${rate(s.max_upload_bps)} (${num(s.upload_cap_pct)}%)` : "—"}</dd>
                  <dt>{t("torrent.speed.current")}</dt><dd>{s.current_up_limit === null ? "—" : s.current_up_limit === 0 ? t("torrent.unlimited") : rate(s.current_up_limit)}</dd>
                  <dt>{t("torrent.speed.status")}</dt>
                  <dd>{s.status === "ok" ? <StatusChip tone="success">{t("torrent.speed.ok")}</StatusChip>
                    : s.status === "boven_grens" ? <StatusChip tone="warning">{t("torrent.speed.over")}</StatusChip>
                      : <StatusChip tone="neutral">{t("torrent.speed.unknown")}</StatusChip>}</dd>
                  <dt>{t("torrent.speed.conns")}</dt><dd>{num(s.current_max_connec ?? 0)} / {t("torrent.max")} {num(s.max_connections_cap)} · {t("torrent.speed.perTorrent")} {num(s.current_max_connec_per_torrent ?? 0)} / {num(s.max_connections_per_torrent_cap)}</dd>
                </dl>
                {s.status === "onbekend" ? <InlineState tone="info">{t("torrent.speed.setLine")}</InlineState> : null}
                {s.status === "boven_grens" && s.max_upload_bps ? (
                  <div className="row wrap">
                    <Button size="sm" variant="primary" pending={pending === "up"} disabled={!isOperator} title={isOperator ? undefined : t("torrent.operatorOnly")}
                      onClick={() => void create("up", { up_limit: s.max_upload_bps }, t("torrent.speed.reason"))}>{t("torrent.speed.propose")}</Button>
                  </div>
                ) : null}
              </div>
            );
          }}
        </ResourceView>
      </Card>
      <Card className="span-6" title={t("torrent.queue.title")} icon={ListOrdered}>
        <ResourceView resource={policy} endpoint="GET /api/v1/torrent/policy" compact>
          {(d) => {
            const q = d.queue;
            return (
              <div className="stack">
                <dl className="kv">
                  <dt>{t("torrent.queue.enabled")}</dt><dd>{q.queueing_enabled ? t("torrent.on") : t("torrent.off")}</dd>
                  <dt>{t("torrent.queue.limits")}</dt><dd>{t("torrent.queue.limitsValue", { dl: num(q.max_active_downloads ?? 0), ul: num(q.max_active_uploads ?? 0), all: num(q.max_active_torrents ?? 0) })}</dd>
                  <dt>{t("torrent.queue.slow")}</dt><dd>{q.dont_count_slow_torrents ? t("torrent.queue.slowOff") : t("torrent.queue.slowOn")}</dd>
                  <dt>{t("torrent.queue.now")}</dt><dd>{t("torrent.queue.nowValue", { active: num(q.active_downloads), stalled: num(q.stalled_downloads), queued: num(q.queued_downloads) })}</dd>
                </dl>
                {q.suggestions.length ? q.suggestions.map((sug) => (
                  <InlineState key={sug.key} tone="info">
                    <span>{sug.why}{" "}
                      <Button size="sm" pending={pending === sug.key} disabled={!isOperator} title={isOperator ? undefined : t("torrent.operatorOnly")}
                        onClick={() => void create(sug.key, { [sug.key]: sug.after }, sug.why)}>{t("torrent.makePreview")}</Button>
                    </span>
                  </InlineState>
                )) : <p className="t-2" style={{ margin: 0, fontSize: 13 }}>{t("torrent.queue.noSuggestion")}</p>}
              </div>
            );
          }}
        </ResourceView>
      </Card>
      <Card className="span-12" title={t("torrent.private.title")} icon={ShieldCheck}>
        <ResourceView resource={policy} endpoint="GET /api/v1/torrent/policy" compact>
          {(d) => (
            <div className="stack-sm">
              <div className="row wrap" style={{ gap: 6 }}>
                <StatusChip tone={d.private.has_private_torrents ? "info" : "neutral"}>{d.private.has_private_torrents ? t("torrent.private.has") : t("torrent.private.none")}</StatusChip>
                <StatusChip tone="success">{t("torrent.private.noInjection")}</StatusChip>
                <StatusChip tone="success">{t("torrent.private.noDelete")}</StatusChip>
                <StatusChip tone={d.private.add_trackers_enabled ? "warning" : "success"}>{d.private.add_trackers_enabled ? t("torrent.private.autoAddOn") : t("torrent.private.autoAddOff")}</StatusChip>
              </div>
              <p className="t-2" style={{ margin: 0, fontSize: 13 }}>{d.private.note}</p>
            </div>
          )}
        </ResourceView>
      </Card>
      {dialog}
    </>
  );
}
