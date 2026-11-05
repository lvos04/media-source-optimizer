"use client";

import { Eye, ShieldAlert, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button, Card, InlineState } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ChangeDialog, notifyTorrentChanged, useTorrentChanged } from "@/components/mso/torrent/shared";
import { api, errorMessage } from "@/lib/api/client";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { SuspiciousList } from "@/lib/api/torrent";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/**
 * Malware cleanup: suspicious torrents of the last scan and ONE button that creates the cleanup proposal
 *   "Opruimvoorstel maken" → POST /api/v1/torrent/suspicious/cleanup-preview (operator)
 * The proposal (toestemming_nodig) opens in the standard change dialog: approve → apply (beheermodus). When a
 * proposal is already open it is shown instead of creating a new one (MSO's torrent.autopilot job keeps one open
 * proposal up to date by itself; it is never applied without approval). Renders nothing when nothing is suspicious.
 * `compact` = banner only (Downloads → Torrent); otherwise a full card with the list (Torrentclient).
 */
export function SuspiciousCleanup({ compact = false, onChanged }: { compact?: boolean; onChanged?: () => void }) {
  const { t, num, bytes } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const isOperator = can("operator");
  const list = useResource<SuspiciousList>("/torrent/suspicious", undefined, 60_000);
  useTorrentChanged(list.reload);
  const [change, setChange] = useState<ChangeSet | null>(null);
  const [pending, setPending] = useState(false);

  const create = async () => {
    setPending(true);
    try {
      setChange(await api.post<ChangeSet>("/torrent/suspicious/cleanup-preview", {}));
      toast.push("success", t("torrent.cleanup.created"));
      await list.reload();
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(false);
    }
  };

  const data = list.data;
  if (!list.error && (!data || data.items.length === 0) && !change) return null;

  const actions = (d: SuspiciousList) => (
    <div className="row wrap">
      {d.open_change ? (
        <Button size="sm" variant="primary" icon={Eye} onClick={() => setChange(d.open_change)}>{t("torrent.cleanup.openProposal")}</Button>
      ) : (
        <Button size="sm" variant="danger" icon={Trash2} pending={pending} disabled={!isOperator || !d.items.length}
          title={!isOperator ? t("torrent.operatorOnly") : undefined} onClick={() => void create()}>{t("torrent.cleanup.create")}</Button>
      )}
      <span className="hint" style={{ margin: 0 }}>{t(d.open_change ? "torrent.cleanup.autoHint" : "torrent.cleanup.hint")}</span>
    </div>
  );

  const dialog = (
    <ChangeDialog change={change} onClose={() => { setChange(null); void list.reload(); }}
      onChanged={() => { void list.reload(); onChanged?.(); notifyTorrentChanged(); }} />
  );

  if (compact) {
    return (
      <>
        {data && data.items.length ? (
          <InlineState tone="danger" role="alert">
            <div className="stack-sm">
              <span>{t("torrent.suspiciousBanner", { count: num(data.items.length) })}</span>
              {actions(data)}
            </div>
          </InlineState>
        ) : null}
        {dialog}
      </>
    );
  }

  return (
    <Card className="span-12" title={t("torrent.cleanup.title")} icon={ShieldAlert}>
      <ResourceView resource={list} endpoint="GET /api/v1/torrent/suspicious" compact>
        {(d) => !d.items.length ? <InlineState tone="success" role="status">{t("torrent.cleanup.none")}</InlineState> : (
          <div className="stack">
            <InlineState tone="danger" role="alert">{t("torrent.cleanup.body", { count: num(d.items.length) })}</InlineState>
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
              <table className="table table--stack table--compact">
                <thead><tr>
                  <th>{t("torrent.cleanup.col.name")}</th><th>{t("torrent.cleanup.col.files")}</th>
                  <th>{t("torrent.cleanup.col.tracker")}</th><th>{t("torrent.cleanup.col.category")}</th><th>{t("torrent.cleanup.col.size")}</th>
                </tr></thead>
                <tbody>{d.items.slice(0, 50).map((x) => (
                  <tr key={x.hash}>
                    <td className="cell-primary">{x.name}</td>
                    <td data-label={t("torrent.cleanup.col.files")} className="mono" style={{ fontSize: 12 }}>{x.files.length ? x.files.join(", ") : "—"}</td>
                    <td data-label={t("torrent.cleanup.col.tracker")}>{x.tracker_host ?? t("torrent.cleanup.unknown")}</td>
                    <td data-label={t("torrent.cleanup.col.category")}>{x.category ?? "—"}</td>
                    <td data-label={t("torrent.cleanup.col.size")}>{x.size !== null ? bytes(x.size) : "—"}</td>
                  </tr>
                ))}</tbody>
              </table>
              {d.items.length > 50 ? <p className="t-3" style={{ margin: "6px 0 0", fontSize: 12 }}>{t("torrent.cleanup.more", { count: num(d.items.length - 50) })}</p> : null}
            </div>
            <p className="t-2" style={{ margin: 0, fontSize: 13 }}><strong>{t("torrent.cleanup.irreversible")}</strong></p>
            {actions(d)}
          </div>
        )}
      </ResourceView>
      {dialog}
    </Card>
  );
}
