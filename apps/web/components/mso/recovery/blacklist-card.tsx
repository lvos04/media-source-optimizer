"use client";

import { Ban, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { BlacklistEntry } from "@/lib/api/recovery";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export function BlacklistCard({ reloadKey }: { reloadKey?: number }) {
  const { t, relative, num } = useI18n();
  const { can } = useSession();
  const [filter, setFilter] = useState<"active" | "all">("active");
  const list = useResource<{ items: BlacklistEntry[] }>("/recovery/blacklist", { active_only: filter === "active", limit: 500 });
  const [removing, setRemoving] = useState<BlacklistEntry | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isAdmin = can("admin");
  const reload = list.reload;
  useEffect(() => { if (reloadKey) void reload(); }, [reloadKey, reload]);

  const remove = async () => {
    if (!removing) return;
    setPending(true);
    setError(null);
    try {
      await api.del(`/recovery/blacklist/${removing.id}`);
      setRemoving(null);
      void list.reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <Card className="span-12" title={t("rec.bl.title")} icon={Ban} sub={t("rec.bl.sub")} flush
      actions={<Segmented label={t("rec.bl.filter")} value={filter} onChange={setFilter}
        options={[{ value: "active", label: t("rec.bl.active") }, { value: "all", label: t("rec.bl.all") }]} />}>
      <ResourceView resource={list} endpoint="GET /api/v1/recovery/blacklist" isEmpty={(d) => d.items.length === 0}
        empty={<StateView kind="empty" compact title={t("rec.bl.empty")} body={t("rec.bl.emptyBody")} />}>
        {(d) => (
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
            <table className="table table--stack">
              <thead><tr><th>{t("rec.bl.release")}</th><th>{t("rec.bl.reason")}</th><th>{t("rec.bl.failures")}</th><th>{t("rec.bl.until")}</th><th><span className="sr-only">{t("rec.bl.actions")}</span></th></tr></thead>
              <tbody>
                {d.items.map((e) => (
                  <tr key={e.id}>
                    <td data-label={t("rec.bl.release")}>
                      <span className="mono" style={{ overflowWrap: "anywhere" }}>{e.release_key}</span>
                      <div className="hint">{[e.protocol, e.media_ref].filter(Boolean).join(" · ") || "—"}</div>
                    </td>
                    <td data-label={t("rec.bl.reason")}>{e.reason}<div className="hint">{e.created_by} · {relative(e.created_at)}</div></td>
                    <td data-label={t("rec.bl.failures")} className="num">{num(e.failures)}</td>
                    <td data-label={t("rec.bl.until")}>
                      {e.permanent ? <StatusChip tone="danger">{t("rec.bl.permanent")}</StatusChip>
                        : e.active ? <StatusChip tone="warning">{e.expires_at ? relative(e.expires_at) : "—"}</StatusChip>
                          : <StatusChip tone="neutral">{t("rec.bl.expired")}</StatusChip>}
                    </td>
                    <td data-label={t("rec.bl.actions")}>
                      <Button size="sm" variant="danger" icon={Trash2} disabled={!isAdmin} title={isAdmin ? undefined : t("mg.needAdmin")} onClick={() => setRemoving(e)}>{t("rec.bl.remove")}</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </ResourceView>
      <DangerDialog open={removing !== null} onClose={() => setRemoving(null)} title={t("rec.bl.removeTitle")}
        description={t("rec.bl.removeBody", { key: removing?.release_key ?? "" })} confirmLabel={t("rec.bl.remove")}
        pending={pending} error={error} onConfirm={() => void remove()} />
    </Card>
  );
}
