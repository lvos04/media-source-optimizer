"use client";

import { Eye, RefreshCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, Card, InlineState, Modal, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { ChangePreview } from "@/components/mso/change-preview";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { FallbackList, FallbackRow, FallbackStatus } from "@/lib/api/recovery";
import { useI18n, type MessageKey } from "@/lib/i18n";

const STATUS: Record<FallbackStatus, { tone: Tone; label: MessageKey }> = {
  waiting: { tone: "warning", label: "rec.fb.status.waiting" },
  skipped: { tone: "neutral", label: "rec.fb.status.skipped" },
  no_candidate: { tone: "warning", label: "rec.fb.status.no_candidate" },
  proposed: { tone: "info", label: "rec.fb.status.proposed" },
  sent: { tone: "success", label: "rec.fb.status.sent" },
  failed: { tone: "danger", label: "rec.fb.status.failed" },
  discarded: { tone: "neutral", label: "rec.fb.status.discarded" },
  retry: { tone: "info", label: "rec.fb.status.retry" },
  mg_requested: { tone: "success", label: "rec.fb.status.mg_requested" },
};

const MAPPING: Record<string, MessageKey> = {
  send: "rec.fb.map.send", mediaguard_ids: "rec.fb.map.mgIds", mediaguard_title: "rec.fb.map.mgTitle", outcome_meta: "rec.fb.map.meta",
};

function ProposalDialog({ changeId, onClose, onChanged }: { changeId: number; onClose: () => void; onChanged: () => void }) {
  const { t } = useI18n();
  const change = useResource<ChangeSet>(`/changes/${changeId}`);
  return (
    <Modal open onClose={onClose} wide icon={Eye} title={t("rec.fb.proposal")} footer={<Button onClick={onClose}>{t("rec.item.close")}</Button>}>
      <ResourceView resource={change} endpoint={`GET /api/v1/changes/${changeId}`} compact>
        {(c) => <ChangePreview change={c} card={false} onChanged={async (u) => { if (u) change.setData(() => u); else await change.reload(); onChanged(); }} />}
      </ResourceView>
    </Modal>
  );
}

/** Failed downloads → blacklist → fallback candidate → change set "Versturen naar SABnzbd/qBittorrent". */
export function FallbackCard({ reloadKey }: { reloadKey?: number }) {
  const { t, relative, num } = useI18n();
  const [showSilent, setShowSilent] = useState(false);
  const list = useResource<FallbackList>("/recovery/fallbacks", { limit: 100, include_silent: showSilent }, 60_000);
  const [open, setOpen] = useState<number | null>(null);
  const reload = list.reload;
  useEffect(() => { if (reloadKey) void reload(); }, [reloadKey, reload]);
  const s = list.data?.settings;

  return (
    <Card className="span-12" title={t("rec.fb.title")} icon={RefreshCcw} sub={t("rec.fb.sub")} flush
      actions={s ? (
        <span className="row wrap" style={{ gap: 6 }}>
          <StatusChip tone={s.fallback_on_failure ? "success" : "neutral"}>{t(s.fallback_on_failure ? "rec.fb.on" : "rec.fb.off")}</StatusChip>
          {list.data?.summary?.autopilot ? <StatusChip tone="success">{t("rec.fb.autopilot")}</StatusChip>
            : <StatusChip tone={s.auto_send_fallback ? "warning" : "info"}>{t(s.auto_send_fallback ? "rec.fb.autoSend" : "rec.fb.approval")}</StatusChip>}
        </span>
      ) : undefined}>
      {list.data?.summary?.autopilot ? (
        <div style={{ padding: "12px 16px 0" }}><InlineState tone="success">{t("rec.fb.autopilotNote")}</InlineState></div>
      ) : s && list.data?.mediaguard_mode === "connected" && !s.allow_manual_send_connected ? (
        <div style={{ padding: "12px 16px 0" }}><InlineState tone="info">{t("rec.fb.connectedNote")}</InlineState></div>
      ) : null}
      {list.data?.summary ? (() => {
        const sm = list.data.summary;
        const parts = [
          sm.by_status.retry ? t("rec.fb.summary.retry", { n: num(sm.by_status.retry) }) : null,
          sm.by_status.mg_requested ? t("rec.fb.summary.mg", { n: num(sm.by_status.mg_requested) }) : null,
          sm.not_wanted ? t("rec.fb.summary.notWanted", { n: num(sm.not_wanted) }) : null,
        ].filter(Boolean);
        return parts.length ? (
          <div className="row wrap t-2" style={{ padding: "10px 16px 0", fontSize: 12.5, gap: 8 }}>
            <span>{parts.join(" · ")}</span>
            {sm.not_wanted ? (
              <Button size="sm" variant="ghost" onClick={() => setShowSilent((v) => !v)}>
                {t(showSilent ? "rec.fb.summary.hide" : "rec.fb.summary.show")}
              </Button>
            ) : null}
          </div>
        ) : null;
      })() : null}
      <ResourceView resource={list} endpoint="GET /api/v1/recovery/fallbacks" isEmpty={(d) => d.items.length === 0}
        empty={<StateView kind="empty" compact title={t("rec.fb.empty")} body={t(s && !s.fallback_on_failure ? "rec.fb.emptyOff" : "rec.fb.emptyBody")} />}>
        {(d) => (
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
            <table className="table table--stack" data-testid="fallback-table">
              <thead><tr><th>{t("rec.fb.failed")}</th><th>{t("rec.fb.item")}</th><th>{t("rec.fb.status")}</th><th>{t("rec.fb.candidate")}</th><th><span className="sr-only">{t("rec.bl.actions")}</span></th></tr></thead>
              <tbody>
                {d.items.map((r: FallbackRow) => {
                  const st = STATUS[r.status] ?? STATUS.skipped;
                  return (
                    <tr key={r.id}>
                      <td data-label={t("rec.fb.failed")}>
                        <span style={{ overflowWrap: "anywhere" }}>{r.failed_title}</span>
                        <div className="hint">{[r.protocol === "usenet" ? "Usenet" : r.protocol === "torrent" ? "Torrent" : null, r.failure_label, relative(r.created_at)].filter(Boolean).join(" · ")}</div>
                      </td>
                      <td data-label={t("rec.fb.item")}>
                        {r.media_ref ? <span className="mono">{r.media_ref}</span> : <span className="t-3">{t("rec.fb.noItem")}</span>}
                        {r.mapping && MAPPING[r.mapping] ? <div className="hint">{t(MAPPING[r.mapping])}</div> : null}
                      </td>
                      <td data-label={t("rec.fb.status")}>
                        <StatusChip tone={st.tone}>{t(st.label)}</StatusChip>
                        {r.reason ? <div className="hint" style={{ overflowWrap: "anywhere" }}>{r.reason}</div> : null}
                        {r.status === "waiting" && r.next_attempt_at ? <div className="hint">{t("rec.fb.next", { when: relative(r.next_attempt_at) })}</div> : null}
                      </td>
                      <td data-label={t("rec.fb.candidate")}>
                        {r.candidate_title ? <span style={{ overflowWrap: "anywhere" }}>{r.candidate_title}</span> : <span className="t-3">—</span>}
                      </td>
                      <td data-label={t("rec.bl.actions")}>
                        {r.change_id ? (
                          <Button size="sm" variant={r.status === "proposed" ? "primary" : "secondary"} icon={Eye} onClick={() => setOpen(r.change_id)}>
                            {t(r.status === "proposed" ? "rec.fb.review" : "rec.fb.view")}
                          </Button>
                        ) : <span className="t-3" style={{ fontSize: 12 }}>{t("rec.fb.noProposal")}</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </ResourceView>
      {open !== null ? <ProposalDialog changeId={open} onClose={() => setOpen(null)} onChanged={() => void list.reload()} /> : null}
    </Card>
  );
}
