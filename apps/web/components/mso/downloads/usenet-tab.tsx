"use client";

import { FlaskConical, History, ListOrdered, RefreshCw } from "lucide-react";
import { useState } from "react";

import { Button, Card, LinkButton, Progress, Segmented, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { StarCategoryNote } from "@/components/mso/usenet/categories";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { SabHistory, SabHistoryItem, SabQueue } from "@/lib/api/usenet";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

type Filter = "all" | "failed";
const STATUS_TONE: Record<SabHistoryItem["status"], Tone> = { success: "success", failed: "danger", active: "accent" };
const CERTAINTY_TONE: Record<string, Tone> = { zeker: "danger", waarschijnlijk: "warning", onbekend: "neutral" };
const QUEUE_TONE: Record<string, Tone> = { Paused: "neutral", Failed: "danger", Completed: "success", Queued: "neutral", Idle: "neutral" };

/** SABnzbd queue status ("Downloading", "Queued", …) in Dutch; unknown statuses are shown as SABnzbd sends them. */
function useQueueStatus() {
  const { t } = useI18n();
  return (status: string | null | undefined) => {
    if (!status) return "—";
    const key = `usenet.dl.queueStatus.${status}`;
    const label = t(key as MessageKey);
    return label === key ? status : label;
  };
}

function ReasonCell({ item }: { item: SabHistoryItem }) {
  const { t } = useI18n();
  if (item.status !== "failed") return <span className="t-3">—</span>;
  const cause = item.likely_cause;
  return (
    <div className="stack-sm" style={{ gap: 2 }}>
      <span className="row wrap" style={{ gap: 6 }}>
        <StatusChip tone={CERTAINTY_TONE[item.certainty ?? "onbekend"] ?? "neutral"} title={item.evidence ?? undefined}>
          {item.failure_label ?? item.failure_reason ?? "—"}
        </StatusChip>
        {item.certainty ? <span className="t-3" style={{ fontSize: 12 }}>{t(`usenet.dl.certainty.${item.certainty}` as MessageKey)}</span> : null}
      </span>
      {cause ? (
        <span className="t-2" style={{ fontSize: 12, whiteSpace: "normal" }} title={item.evidence ?? undefined}>
          {t("usenet.dl.likelyCause", {
            cause: `${cause.label.toLowerCase()} — ${t("usenet.dl.likelyCauseServers", {
              servers: cause.servers.map((s) => `${s.name}: ${(s.label || t(`usenet.status.${s.status}` as MessageKey)).toLowerCase()}`).join("; "),
            })}`,
          })}
        </span>
      ) : item.evidence ? (
        <span className="t-2" style={{ fontSize: 12, whiteSpace: "normal" }}>{item.evidence}</span>
      ) : null}
      {item.failure_detail ? (
        <span className="t-3 pre-wrap" style={{ fontSize: 11.5, wordBreak: "break-word" }}>{t("usenet.dl.sabMessage", { message: item.failure_detail })}</span>
      ) : null}
    </div>
  );
}

/** P5 / 1.6.0: "standaard (*)" explained in place with the diagnosed cause and a link to the fix (categories.tsx). */
function DefaultCategoryNote({ items }: { items: SabHistoryItem[] }) {
  const n = items.filter((h) => h.category === "*").length;
  return n ? <StarCategoryNote count={n} total={items.length} /> : null;
}

function CategoryCell({ category, media, source }: { category: string | null; media: string | null; source: SabHistoryItem["category_source"] }) {
  const { t } = useI18n();
  return (
    <>
      {category === "*" ? <span title={t("usenet.dl.defaultCatBody")}>{t("usenet.dl.defaultCategory")}</span> : category ?? "—"}
      <div className="t-3" style={{ fontSize: 12 }}>
        {media ? t(`usenet.cat.media.${media}` as MessageKey) : t("usenet.dl.unmapped")}
        {media && source !== "geen" ? ` · ${t(`usenet.dl.catSource.${source}` as MessageKey)}` : ""}
      </div>
    </>
  );
}

/** Downloads → Usenet: SABnzbd queue and history with evidence-based failure reasons. */
export function UsenetDownloadsTab() {
  const { t, num, bytes, relative } = useI18n();
  const queueStatus = useQueueStatus();
  const toast = useToast();
  const { can } = useSession();
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState(false);
  const queue = useResource<SabQueue>("/usenet/sab/queue", undefined, 15_000);
  const history = useResource<SabHistory>("/usenet/sab/history", { limit: 100, failed_only: filter === "failed" }, 60_000);

  const refresh = async () => {
    setBusy(true);
    try {
      await api.post("/usenet/sab/refresh", {});
      toast.push("success", t("usenet.dl.refreshed"));
      await Promise.all([queue.reload(), history.reload()]);
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid">
      <Card title={t("usenet.dl.queue")} icon={ListOrdered} className="span-12" flush
        sub={queue.data ? (queue.data.paused ? t("usenet.dl.paused") : queue.data.status && !queue.data.speed_kbps ? queueStatus(queue.data.status) : queue.data.speed_kbps ? t("usenet.dl.speed", { speed: bytes(queue.data.speed_kbps * 1024) }) : undefined) : undefined}
        actions={
          <Button size="sm" icon={RefreshCw} onClick={() => void refresh()} pending={busy} disabled={!can("operator")}
            title={can("operator") ? undefined : t("usenet.operatorOnly")}>{t("usenet.refresh")}</Button>
        }>
        <ResourceView resource={queue} endpoint="GET /api/v1/usenet/sab/queue" isEmpty={(d) => !d.items.length}
          empty={<StateView kind="empty" compact title={t("usenet.dl.queueEmpty")} />}>
          {(d) => (
            <table className="table table--stack">
              <thead>
                <tr><th>{t("usenet.dl.name")}</th><th>{t("usenet.dl.category")}</th><th>{t("usenet.dl.status")}</th><th>{t("usenet.dl.progress")}</th><th>{t("usenet.dl.timeleft")}</th></tr>
              </thead>
              <tbody>
                {d.items.map((q) => (
                  <tr key={q.nzo_id}>
                    <td data-label={t("usenet.dl.name")} style={{ wordBreak: "break-word" }}>{q.name}</td>
                    <td data-label={t("usenet.dl.category")}>
                      <CategoryCell category={q.category} media={q.media_category} source={q.category_source} />
                    </td>
                    <td data-label={t("usenet.dl.status")}>
                      <StatusChip tone={QUEUE_TONE[q.status] ?? "accent"} icon={null} title={q.status !== queueStatus(q.status) ? `SABnzbd: ${q.status}` : undefined}>{queueStatus(q.status)}</StatusChip>
                    </td>
                    <td data-label={t("usenet.dl.progress")} style={{ minWidth: 120 }}>
                      <Progress value={q.percentage} thin label={t("usenet.dl.progress")} />
                      <span className="t-3" style={{ fontSize: 12 }}>{q.percentage === null ? "—" : `${num(q.percentage)}%`} · {q.size_mb === null ? "—" : bytes(q.size_mb * 1024 * 1024)}</span>
                    </td>
                    <td data-label={t("usenet.dl.timeleft")}>{q.timeleft ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </ResourceView>
      </Card>
      <Card title={t("usenet.dl.history")} icon={History} className="span-12" flush
        actions={<Segmented label={t("usenet.dl.history")} value={filter} onChange={setFilter}
          options={[{ value: "all", label: t("usenet.dl.filter.all") }, { value: "failed", label: t("usenet.dl.filter.failed") }]} />}>
        <ResourceView resource={history} endpoint="GET /api/v1/usenet/sab/history" isEmpty={(d) => !d.items.length}
          empty={<StateView kind="empty" compact title={t("usenet.dl.historyEmpty")} />}>
          {(d) => (
            <>
            <DefaultCategoryNote items={d.items} />
            <table className="table table--stack">
              <thead>
                <tr><th>{t("usenet.dl.name")}</th><th>{t("usenet.dl.category")}</th><th>{t("usenet.dl.status")}</th><th>{t("usenet.dl.reason")}</th><th>{t("usenet.dl.size")}</th><th>{t("usenet.dl.finished")}</th></tr>
              </thead>
              <tbody>
                {d.items.map((h) => (
                  <tr key={h.nzo_id}>
                    <td data-label={t("usenet.dl.name")} style={{ wordBreak: "break-word" }}>
                      {h.name}
                      {h.status === "failed" && h.testable ? (
                        <div style={{ marginTop: 4 }}>
                          <LinkButton href={`/usenet-providers?nzb=${h.nzb_id}#artikeltest`} size="sm" icon={FlaskConical}>{t("usenet.dl.testArticles")}</LinkButton>
                        </div>
                      ) : null}
                    </td>
                    <td data-label={t("usenet.dl.category")}>
                      <CategoryCell category={h.category} media={h.media_category} source={h.category_source} />
                    </td>
                    <td data-label={t("usenet.dl.status")}>
                      <StatusChip tone={STATUS_TONE[h.status]} pending={h.status === "active"}>
                        {t(h.status === "success" ? "usenet.dl.success" : h.status === "failed" ? "usenet.dl.failed" : "usenet.dl.active")}
                      </StatusChip>
                    </td>
                    <td data-label={t("usenet.dl.reason")}><ReasonCell item={h} /></td>
                    <td data-label={t("usenet.dl.size")}>{bytes(h.size_bytes)}</td>
                    <td data-label={t("usenet.dl.finished")}>{relative(h.finished_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </>
          )}
        </ResourceView>
      </Card>
    </div>
  );
}
