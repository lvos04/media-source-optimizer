"use client";

import { Copy, FolderTree, HardDrive, Link2, Scale, ScanSearch, ShieldCheck, X } from "lucide-react";
import { useState } from "react";

import { KpiCard } from "@/components/mg/domain";
import { Button, Card, InlineState, PageHeader, Progress, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ChangeDialog, PrivateChip, StateChip } from "@/components/mso/torrent/shared";
import { api, errorMessage } from "@/lib/api/client";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { CrossSeedList, JobRef, StorageReport } from "@/lib/api/torrent";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useSession } from "@/lib/session";

function CrossSeedCard() {
  const { t, bytes, num, relative } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const isOperator = can("operator");
  const list = useResource<CrossSeedList>("/torrent/cross-seed", undefined, 60_000);
  const [pending, setPending] = useState<string | null>(null);
  const [change, setChange] = useState<ChangeSet | null>(null);

  const act = async (key: string, fn: () => Promise<void>) => {
    setPending(key);
    try {
      await fn();
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(null);
    }
  };
  const enabled = list.data?.enabled ?? false;
  const scanWhy = !isOperator ? t("torrent.operatorOnly") : !enabled ? t("storage.cross.disabled") : !list.data?.search_available ? t("storage.cross.noSearch") : undefined;

  return (
    <Card className="span-12" title={t("storage.cross.title")} icon={Copy} flush
      actions={<Button size="sm" icon={ScanSearch} pending={pending === "scan"} disabled={!!scanWhy} title={scanWhy}
        onClick={() => void act("scan", async () => { await api.post<JobRef>("/torrent/cross-seed/scan"); toast.push("success", t("storage.cross.scanQueued")); })}>{t("storage.cross.scan")}</Button>}>
      <ResourceView resource={list} endpoint="GET /api/v1/torrent/cross-seed" compact>
        {(d) => (
          <div className="stack" style={{ paddingBottom: 12 }}>
            <div style={{ padding: "0 16px" }} className="stack-sm">
              <div className="row wrap" style={{ gap: 6 }}>
                <StatusChip tone={d.enabled ? "success" : "neutral"}>{d.enabled ? t("storage.cross.on") : t("storage.cross.off")}</StatusChip>
                <StatusChip tone={d.search_available ? "success" : "neutral"}>{d.search_available ? t("storage.cross.searchOk") : t("storage.cross.noSearch")}</StatusChip>
                <span className="t-3" style={{ fontSize: 12 }}>{t("storage.cross.allowed")}: {d.allowed_trackers.length ? d.allowed_trackers.join(", ") : t("storage.cross.allowedNone")}</span>
              </div>
              <p className="t-2" style={{ margin: 0, fontSize: 13 }}>{t("storage.cross.explain")}</p>
            </div>
            {d.items.length ? (
              <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
                <table className="table table--stack">
                  <thead><tr>
                    <th>{t("storage.cross.col.candidate")}</th><th>{t("storage.cross.col.payload")}</th><th>{t("storage.cross.col.status")}</th>
                    <th><span className="sr-only">{t("torrent.col.actions")}</span></th>
                  </tr></thead>
                  <tbody>{d.items.map((c) => (
                    <tr key={c.id}>
                      <td className="cell-primary">
                        <div style={{ overflowWrap: "anywhere" }}>{c.title}</div>
                        <div className="t-3" style={{ fontSize: 11.5 }}>{c.tracker_host ?? "—"} · {t("storage.cross.source")}: {c.source_name ?? c.source_hash.slice(0, 10)}</div>
                      </td>
                      <td data-label={t("storage.cross.col.payload")}>{t("storage.cross.payload", { files: num(c.files), size: bytes(c.total_size) })}</td>
                      <td data-label={t("storage.cross.col.status")}>
                        <StatusChip tone={c.status === "added" ? "success" : c.status === "rejected" ? "neutral" : "info"}>{t(`storage.cross.status.${c.status}` as MessageKey)}</StatusChip>
                        <div className="t-3" style={{ fontSize: 11.5 }}>{relative(c.created_at)}</div>
                      </td>
                      <td data-label={t("torrent.col.actions")}>
                        <div className="toolbar" style={{ justifyContent: "flex-end" }}>
                          {c.status === "candidate" || c.status === "previewed" ? (
                            <>
                              <Button size="sm" pending={pending === `p${c.id}`} disabled={!isOperator} title={isOperator ? undefined : t("torrent.operatorOnly")}
                                onClick={() => void act(`p${c.id}`, async () => {
                                  setChange(c.change_id && c.change_status && ["preview", "approved", "applied"].includes(c.change_status)
                                    ? await api.get<ChangeSet>(`/changes/${c.change_id}`)
                                    : await api.post<ChangeSet>(`/torrent/cross-seed/${c.id}/preview`));
                                })}>{t("storage.cross.preview")}</Button>
                              <Button size="sm" variant="ghost" icon={X} pending={pending === `r${c.id}`} disabled={!isOperator} title={isOperator ? undefined : t("torrent.operatorOnly")}
                                onClick={() => void act(`r${c.id}`, async () => { await api.post(`/torrent/cross-seed/${c.id}/reject`); await list.reload(); })}>{t("storage.cross.reject")}</Button>
                            </>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            ) : <StateView kind="empty" compact title={t("storage.cross.none")} body={d.enabled ? t("storage.cross.noneBody") : t("storage.cross.disabled")} />}
          </div>
        )}
      </ResourceView>
      <ChangeDialog change={change} onClose={() => setChange(null)} onChanged={() => void list.reload()} />
    </Card>
  );
}

export default function StoragePage() {
  const { t, bytes, num, pct, relative } = useI18n();
  const toast = useToast();
  const { can } = useSession();
  const isOperator = can("operator");
  const report = useResource<StorageReport>("/torrent/storage", undefined, 60_000);
  const [pending, setPending] = useState(false);
  const r = report.data;

  const scan = async () => {
    setPending(true);
    try {
      await api.post<JobRef>("/torrent/storage/scan");
      toast.push("success", t("storage.hl.scanQueued"));
      window.setTimeout(() => void report.reload(), 3000);
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(false);
    }
  };

  const disk = r?.disk ?? null;
  const usedForDownloads = disk && disk.free > 0 ? Math.min(100, (disk.remaining_downloads / disk.free) * 100) : 0;

  return (
    <>
      <PageHeader title={t("nav.storage")} sub={t("storage.sub")} />
      {r && !r.client.available ? <InlineState tone="warning">{r.client.error ? t("torrent.client.error", { error: r.client.error }) : t("storage.noClient")}</InlineState> : null}
      <section className="kpi-grid" aria-label={t("storage.kpis")} style={{ marginTop: 16 }}>
        <KpiCard label={t("storage.kpi.free")} icon={HardDrive} unavailable={!disk} value={disk ? bytes(disk.free) : "—"} meta={disk ? t("storage.kpi.freeMeta", { reserve: bytes(disk.reserve) }) : null}
          tone={disk ? (disk.status === "kritiek" ? "danger" : disk.status === "waarschuwing" ? "warning" : "success") : undefined} />
        <KpiCard label={t("storage.kpi.remaining")} icon={FolderTree} unavailable={!disk} value={disk ? bytes(disk.remaining_downloads) : "—"} meta={t("storage.kpi.remainingMeta")} />
        <KpiCard label={t("storage.kpi.after")} icon={HardDrive} unavailable={!disk} value={disk ? bytes(disk.free_after_downloads) : "—"} meta={disk ? t(`storage.disk.${disk.status}` as MessageKey) : null}
          tone={disk ? (disk.status === "kritiek" ? "danger" : disk.status === "waarschuwing" ? "warning" : undefined) : undefined} />
        <KpiCard label={t("storage.kpi.obligations")} icon={Scale} unavailable={!r} value={r ? num(r.obligations_summary.open) : "—"}
          meta={r ? t("storage.kpi.obligationsMeta", { met: num(r.obligations_summary.met), total: num(r.obligations_summary.total) }) : null}
          tone={r && r.obligations_summary.open ? "warning" : undefined} />
      </section>

      <div className="grid" style={{ marginTop: 16 }}>
        <Card className="span-6" title={t("storage.reserve.title")} icon={HardDrive}>
          <ResourceView resource={report} endpoint="GET /api/v1/torrent/storage" compact>
            {(d) => !d.disk ? <StateView kind="unavailable" compact title={t("storage.noDisk")} body={t("storage.noDiskBody")} /> : (
              <div className="stack">
                <Progress value={usedForDownloads} label={t("storage.reserve.meter")} tone={d.disk.status === "kritiek" ? "danger" : d.disk.status === "waarschuwing" ? "warning" : "success"} />
                <p className="t-2" style={{ margin: 0, fontSize: 13 }}>{t("storage.reserve.explain", { remaining: bytes(d.disk.remaining_downloads), free: bytes(d.disk.free), pct: pct(usedForDownloads), reserve: bytes(d.disk.reserve) })}</p>
                <StatusChip tone={d.disk.status === "kritiek" ? "danger" : d.disk.status === "waarschuwing" ? "warning" : "success"}>{t(`storage.disk.${d.disk.status}` as MessageKey)}</StatusChip>
                {d.media ? (
                  <dl className="kv">
                    <dt>{t("storage.media.path")}</dt><dd className="mono">{d.media.path}</dd>
                    <dt>{t("storage.media.free")}</dt><dd>{bytes(d.media.free)} / {bytes(d.media.total)}</dd>
                    <dt>{t("storage.media.fs")}</dt><dd>{d.media.fstype ?? "—"}{d.media.zfs_dataset ? <> · ZFS <span className="mono">{d.media.zfs_dataset}</span></> : null}</dd>
                    <dt>{t("storage.media.mode")}</dt><dd>{d.media.read_only ? <StatusChip tone="success">{t("storage.media.ro")}</StatusChip> : <StatusChip tone="warning">{t("storage.media.rw")}</StatusChip>}</dd>
                  </dl>
                ) : <p className="hint" style={{ margin: 0 }}>{t("storage.media.none")}</p>}
              </div>
            )}
          </ResourceView>
        </Card>

        <Card className="span-6" title={t("storage.cat.title")} icon={FolderTree} flush>
          <ResourceView resource={report} endpoint="GET /api/v1/torrent/storage" compact isEmpty={(d) => d.categories.length === 0}
            empty={<StateView kind="empty" compact title={t("torrent.empty")} />}>
            {(d) => (
              <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
                <table className="table table--stack table--compact">
                  <thead><tr><th>{t("storage.cat.category")}</th><th>{t("storage.cat.size")}</th><th>{t("storage.cat.torrents")}</th></tr></thead>
                  <tbody>{d.categories.map((c) => (
                    <tr key={c.category}>
                      <td className="cell-primary">{c.category}{c.media_category ? <div className="t-3" style={{ fontSize: 11.5 }}>{c.media_category}</div> : null}</td>
                      <td data-label={t("storage.cat.size")}>{bytes(c.bytes)}</td>
                      <td data-label={t("storage.cat.torrents")}>{t("storage.cat.counts", { total: num(c.torrents), seeding: num(c.seeding), dl: num(c.downloading), priv: num(c.private) })}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </ResourceView>
        </Card>

        <Card className="span-12" title={t("storage.obl.title")} icon={Scale} flush>
          <ResourceView resource={report} endpoint="GET /api/v1/torrent/storage" compact isEmpty={(d) => d.obligations.length === 0}
            empty={<StateView kind="empty" compact title={t("storage.obl.none")} body={t("storage.obl.noneBody")} />}>
            {(d) => (
              <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
                <table className="table table--stack">
                  <thead><tr><th>{t("torrent.col.name")}</th><th>{t("storage.obl.ratio")}</th><th>{t("storage.obl.time")}</th><th>{t("storage.obl.status")}</th></tr></thead>
                  <tbody>{d.obligations.map((o) => {
                    const rule = o.rules[0];
                    return (
                      <tr key={o.hash}>
                        <td className="cell-primary">
                          <div style={{ overflowWrap: "anywhere" }}>{o.name}</div>
                          <div className="row wrap" style={{ gap: 4, marginTop: 4 }}><PrivateChip value={o.private} /><StateChip state={o.state} /><span className="t-3 mono" style={{ fontSize: 11.5 }}>{o.tracker ?? "—"}</span></div>
                        </td>
                        <td data-label={t("storage.obl.ratio")} style={{ minWidth: 140 }}>
                          {num(o.ratio, 2)}{rule?.min_ratio !== null && rule?.min_ratio !== undefined ? ` / ${num(rule.min_ratio, 2)}` : ""}
                          {rule?.ratio_progress !== null && rule?.ratio_progress !== undefined ? <Progress value={rule.ratio_progress * 100} thin label={t("storage.obl.ratio")} tone={rule.ratio_progress >= 1 ? "success" : undefined} /> : null}
                        </td>
                        <td data-label={t("storage.obl.time")} style={{ minWidth: 140 }}>
                          {t("storage.obl.hours", { h: num(o.seeding_time_h, 1) })}{rule?.min_seed_time_h !== null && rule?.min_seed_time_h !== undefined ? ` / ${num(rule.min_seed_time_h, 1)}` : ""}
                          {rule?.time_progress !== null && rule?.time_progress !== undefined ? <Progress value={rule.time_progress * 100} thin label={t("storage.obl.time")} tone={rule.time_progress >= 1 ? "success" : undefined} /> : null}
                        </td>
                        <td data-label={t("storage.obl.status")}>
                          {o.status === "gehaald" ? <StatusChip tone="success">{t("storage.obl.met")}</StatusChip>
                            : o.status === "loopt" ? <StatusChip tone="warning">{t("storage.obl.running")}</StatusChip>
                              : <StatusChip tone="neutral">{t("storage.obl.noRule")}</StatusChip>}
                          {o.warning ? <div className="t-warning" style={{ fontSize: 11.5, marginTop: 4 }}>{t("storage.obl.privateNoRule")}</div> : null}
                        </td>
                      </tr>
                    );
                  })}</tbody>
                </table>
              </div>
            )}
          </ResourceView>
        </Card>

        <Card className="span-12" title={t("storage.hl.title")} icon={Link2}
          actions={<Button size="sm" icon={ScanSearch} pending={pending} disabled={!isOperator} title={isOperator ? undefined : t("torrent.operatorOnly")} onClick={() => void scan()}>{t("storage.hl.scan")}</Button>}>
          <ResourceView resource={report} endpoint="GET /api/v1/torrent/storage" compact>
            {(d) => {
              const h = d.hardlinks;
              if (!h) return <StateView kind="empty" compact title={t("storage.hl.never")} body={t("storage.hl.neverBody")} />;
              return (
                <div className="stack">
                  {h.status !== "ok" ? <InlineState tone={h.status === "niet_read_only" ? "warning" : "info"}>{h.message}</InlineState> : (
                    <>
                      <InlineState tone={h.hardlinked_count ? "warning" : "success"} icon={ShieldCheck}>{h.message}</InlineState>
                      <p className="t-2" style={{ margin: 0, fontSize: 13 }}>{t("storage.hl.summary", { checked: num(h.files_checked ?? 0), linked: num(h.hardlinked_count ?? 0), unmapped: num(h.unmapped ?? 0) })}{d.hardlinks_scanned_at ? ` · ${relative(d.hardlinks_scanned_at)}` : ""}</p>
                      {h.hardlinked?.length ? (
                        <ul className="list">
                          {h.hardlinked.slice(0, 50).map((x) => (
                            <li key={`${x.hash}-${x.file}`} className="list-row">
                              <div className="list-main"><div className="list-title mono" style={{ overflowWrap: "anywhere" }}>{x.file}</div><div className="list-sub">{x.torrent} · {x.tracker ?? "—"}</div></div>
                              <span className="list-aside"><StatusChip tone="warning" icon={Link2}>{t("storage.hl.links", { n: num(x.nlink) })}</StatusChip></span>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </>
                  )}
                </div>
              );
            }}
          </ResourceView>
        </Card>

        <CrossSeedCard />

        <Card className="span-12" title={t("storage.never.title")} icon={ShieldCheck}>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>{(r?.never ?? []).map((n) => <li key={n}>{n}</li>)}</ul>
        </Card>
      </div>
    </>
  );
}
