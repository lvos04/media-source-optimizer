"use client";

import { CircleSlash, Eye, ShieldCheck, ShieldPlus } from "lucide-react";
import { useState } from "react";

import { Button, Card, InlineState, StatusChip } from "@/components/mg/primitives";
import { ResourceView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ChangeDialog, notifyTorrentChanged, useTorrentChanged } from "@/components/mso/torrent/shared";
import { api, errorMessage } from "@/lib/api/client";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { PreventionPreview, PreventionStatus } from "@/lib/api/torrent";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/**
 * Prevention: qBittorrent's "excluded file names" (≥ 4.6) so programs/scripts from fake releases are never
 * downloaded.
 *   GET  /api/v1/torrent/prevention          status (from the last snapshot)
 *   POST /api/v1/torrent/prevention/preview  (operator) creates the reversible change; in Autopilot MSO applies it
 *                                            itself and verifies it by reading it back.
 */
export function PreventionCard() {
  const { t } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const isOperator = can("operator");
  const res = useResource<PreventionStatus>("/torrent/prevention", undefined, 60_000);
  useTorrentChanged(res.reload);
  const [change, setChange] = useState<ChangeSet | null>(null);
  const [pending, setPending] = useState(false);

  const create = async () => {
    setPending(true);
    try {
      const ch = await api.post<PreventionPreview>("/torrent/prevention/preview", {});
      if (ch.auto === "toegepast") {
        toast.push("success", t("torrent.prevention.applied"));
        notifyTorrentChanged();
      } else {
        toast.push("success", t("torrent.prevention.created"));
        setChange(ch);
      }
      await res.reload();
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(false);
    }
  };

  return (
    <Card className="span-12" title={t("torrent.prevention.title")} icon={ShieldCheck}>
      <ResourceView resource={res} endpoint="GET /api/v1/torrent/prevention" compact>
        {(d) => (
          <div className="stack">
            <div className="row wrap">
              {d.active ? <StatusChip tone="success">{t("torrent.prevention.active")}</StatusChip>
                : !d.supported ? <StatusChip tone="neutral" icon={CircleSlash}>{t("torrent.prevention.unsupported")}</StatusChip>
                  : <StatusChip tone="neutral">{t("torrent.prevention.inactive")}</StatusChip>}
              <span className="t-2">{d.message}</span>
            </div>
            <p className="t-2" style={{ margin: 0 }}>{t("torrent.prevention.body")}</p>
            <dl className="dl">
              <dt>{t("torrent.prevention.recommended")}</dt>
              <dd className="mono">{d.recommended.join("  ")}</dd>
              {d.supported ? (
                <>
                  <dt>{t("torrent.prevention.ownLabel")}</dt>
                  <dd className="mono">{d.user_patterns.length ? d.user_patterns.join("  ") : t("torrent.prevention.none")}</dd>
                </>
              ) : null}
            </dl>
            {d.supported && !d.active && d.missing.length ? (
              <p className="t-3" style={{ margin: 0 }}>{t("torrent.prevention.missing", { list: d.missing.join(", ") })}</p>
            ) : null}
            {d.suppressed && !d.open_change ? <InlineState tone="info" role="status">{t("torrent.prevention.suppressed", { reason: d.suppressed })}</InlineState> : null}
            {d.last_change ? (
              <p className="t-3" style={{ margin: 0 }}>{t("torrent.prevention.last", { title: d.last_change.title, status: d.last_change.status })}</p>
            ) : null}
            {d.supported && !d.active ? (
              <div className="row wrap">
                {d.open_change ? (
                  <Button size="sm" variant="primary" icon={Eye} onClick={() => setChange(d.open_change)}>{t("torrent.prevention.open")}</Button>
                ) : (
                  <Button size="sm" variant="primary" icon={ShieldPlus} pending={pending} disabled={!isOperator}
                    title={!isOperator ? t("torrent.operatorOnly") : undefined} onClick={() => void create()}>{t("torrent.prevention.create")}</Button>
                )}
              </div>
            ) : null}
          </div>
        )}
      </ResourceView>
      <ChangeDialog change={change} onClose={() => { setChange(null); void res.reload(); }} onChanged={() => void res.reload()} />
    </Card>
  );
}
