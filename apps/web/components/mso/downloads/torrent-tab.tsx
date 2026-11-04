"use client";

import { ChevronDown, ChevronRight, Magnet, Megaphone, RefreshCw, ScanSearch, ShieldAlert, TriangleAlert } from "lucide-react";
import { Fragment, useMemo, useState } from "react";

import { Button, Card, InlineState, Progress, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { ChangeDialog, DiagnosisChip, FindingList, PrivateChip, StateChip, useRate } from "@/components/mso/torrent/shared";
import { UnavailableState } from "@/components/mso/unavailable-state";
import { api, errorMessage } from "@/lib/api/client";
import type { ChangeSet } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { JobRef, TorrentDetail, TorrentList, TorrentRow } from "@/lib/api/torrent";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

type Filter = "all" | "problems" | "downloading" | "seeding" | "stopped";
/** Active or waiting downloads (stopped downloads are under "Gestopt"). */
const DL = new Set(["downloading", "forcedDL", "metaDL", "forcedMetaDL", "stalledDL", "queuedDL", "checkingDL", "allocating"]);
/** Really seeding (or waiting/checking to seed) — a stopped complete torrent does not seed. */
const SEED = new Set(["uploading", "forcedUP", "stalledUP", "queuedUP", "checkingUP"]);
/** Stopped by the user/arr (qBittorrent 5: stopped*, older: paused*). */
const STOPPED = new Set(["pausedUP", "stoppedUP", "pausedDL", "stoppedDL"]);
const MATCH: Record<Exclude<Filter, "all" | "problems">, (x: TorrentRow) => boolean> = {
  downloading: (x) => DL.has(x.state ?? ""),
  seeding: (x) => SEED.has(x.state ?? ""),
  stopped: (x) => STOPPED.has(x.state ?? ""),
};

function Details({ hash }: { hash: string }) {
  const { t, num, relative } = useI18n();
  const detail = useResource<TorrentDetail>(`/torrent/torrents/${encodeURIComponent(hash)}`);
  return (
    <ResourceView resource={detail} compact endpoint={`GET /api/v1/torrent/torrents/${hash}`}>
      {(d) => (
        <div className="stack" style={{ padding: "8px 0" }}>
          <FindingList findings={d.findings} />
          <div>
            <span className="eyebrow">{t("torrent.trackers")}</span>
            {d.trackers.filter((x) => x.kind === "tracker").length ? (
              <ul className="list" style={{ marginTop: 4 }}>
                {d.trackers.filter((x) => x.kind === "tracker").map((x, i) => (
                  <li key={`${x.host}-${i}`} className="list-row">
                    <div className="list-main">
                      <div className="list-title mono">{x.host ?? "—"}</div>
                      <div className="list-sub">{x.msg || "—"} · {t("torrent.seedsPeers", { seeds: num(x.seeds ?? 0), peers: num(x.peers ?? 0) })}</div>
                    </div>
                    <StatusChip tone={x.status === "working" ? "success" : x.status === "updating" || x.status === "not_contacted" ? "neutral" : "danger"}>
                      {t(x.status === "working" ? "torrent.tracker.working" : x.status === "updating" || x.status === "not_contacted" ? "torrent.tracker.pending" : "torrent.tracker.failing")}
                    </StatusChip>
                  </li>
                ))}
              </ul>
            ) : <p className="t-3" style={{ margin: "4px 0 0", fontSize: 12.5 }}>{t("torrent.noTrackers")}</p>}
          </div>
          <p className="t-3" style={{ margin: 0, fontSize: 12 }}>{t("torrent.updated", { when: relative(d.updated_at) })} · <span className="mono">{d.hash}</span></p>
        </div>
      )}
    </ResourceView>
  );
}

function EtaCell({ row }: { row: TorrentRow }) {
  const { t, duration } = useI18n();
  if ((row.progress ?? 0) >= 1) return <span className="t-3">{t("torrent.complete")}</span>;
  if (row.eta_s === null && row.realistic_eta_s === null) return <span className="t-3">∞</span>;
  return (
    <span className="stack-sm" style={{ gap: 2 }}>
      <span className={row.eta_unreliable ? "t-3" : undefined} style={row.eta_unreliable ? { textDecoration: "line-through" } : undefined}>{row.eta_s === null ? "∞" : duration(row.eta_s)}</span>
      {row.eta_unreliable ? (
        <StatusChip tone="warning" icon={TriangleAlert} title={row.eta_reasons.join("; ")}>{t("torrent.etaUnreliable")}</StatusChip>
      ) : null}
      {row.eta_unreliable && row.realistic_eta_s !== null ? <span className="t-2" style={{ fontSize: 12 }}>{t("torrent.etaRealistic", { eta: duration(row.realistic_eta_s) })}</span> : null}
    </span>
  );
}

/** Downloads → Torrent tab: torrents with state, diagnosis + explanation, ETA reliability and evidence-based actions. */
export function TorrentDownloadsTab() {
  const { t, num, pct, bytes, relative } = useI18n();
  const rate = useRate();
  const toast = useToast();
  const { can } = useSession();
  const isOperator = can("operator");
  const list = useResource<TorrentList>("/torrent/torrents", undefined, 30_000);
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [change, setChange] = useState<ChangeSet | null>(null);

  const items = useMemo(() => {
    const all = list.data?.items ?? [];
    const needle = q.trim().toLowerCase();
    return all.filter((x) => {
      if (needle && !x.name.toLowerCase().includes(needle)) return false;
      if (filter === "problems") return !!x.diagnosis && x.diagnosis.severity !== "info";
      if (filter !== "all") return MATCH[filter](x);
      return true;
    });
  }, [filter, list.data, q]);
  const all = list.data?.items ?? [];
  const problems = all.filter((x) => x.diagnosis && x.diagnosis.severity !== "info").length;
  const suspicious = all.filter((x) => x.suspicious).length;

  const refresh = async () => {
    setPending("refresh");
    try {
      await api.post<JobRef>("/torrent/refresh");
      toast.push("success", t("torrent.refreshQueued"));
      window.setTimeout(() => void list.reload(), 2500);
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(null);
    }
  };

  const preview = async (row: TorrentRow, kind: "recheck" | "reannounce") => {
    setPending(`${kind}:${row.hash}`);
    try {
      setChange(await api.post<ChangeSet>(`/torrent/torrents/${encodeURIComponent(row.hash)}/${kind}-preview`));
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(null);
    }
  };

  if (list.data && !list.data.connector.configured) {
    return <UnavailableState connector="qBittorrent" connectorId="qbittorrent" />;
  }

  return (
    <div className="stack">
      <Card title={t("torrent.tab.title")} icon={Magnet} flush
        actions={<>
          {list.data?.updated_at ? <span className="t-3" style={{ fontSize: 12 }}>{t("torrent.updated", { when: relative(list.data.updated_at) })}</span> : null}
          <Button size="sm" icon={RefreshCw} pending={pending === "refresh"} disabled={!isOperator} title={isOperator ? undefined : t("torrent.operatorOnly")} onClick={() => void refresh()}>{t("torrent.refresh")}</Button>
        </>}>
        <div className="toolbar" style={{ padding: "0 16px 12px", flexWrap: "wrap" }}>
          <Segmented label={t("torrent.filter")} value={filter} onChange={setFilter} options={[
            { value: "all", label: t("torrent.filter.all"), count: all.length },
            { value: "problems", label: t("torrent.filter.problems"), count: problems },
            { value: "downloading", label: t("torrent.filter.downloading"), count: all.filter(MATCH.downloading).length },
            { value: "seeding", label: t("torrent.filter.seeding"), count: all.filter(MATCH.seeding).length },
            { value: "stopped", label: t("torrent.filter.stopped"), count: all.filter(MATCH.stopped).length },
          ]} />
          <input className="input input--sm" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("torrent.searchPlaceholder")} aria-label={t("torrent.searchPlaceholder")} style={{ maxWidth: 260 }} />
        </div>
        {list.data?.stale ? <div style={{ padding: "0 16px 12px" }}><InlineState tone="warning">{t("torrent.staleData")}</InlineState></div> : null}
        {suspicious ? <div style={{ padding: "0 16px 12px" }}><InlineState tone="danger" role="alert">{t("torrent.suspiciousBanner", { count: num(suspicious) })}</InlineState></div> : null}
        <ResourceView resource={list} endpoint="GET /api/v1/torrent/torrents" isEmpty={(d) => d.items.length === 0}
          empty={<StateView kind="empty" title={t("torrent.empty")} body={t("torrent.emptyBody")} />}>
          {() => items.length === 0 ? <StateView kind="empty" compact title={t("torrent.noMatch")} /> : (
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel (horizontaal scrollbaar)">
              <table className="table table--stack">
                <thead><tr>
                  <th><span className="sr-only">{t("torrent.col.expand")}</span></th>
                  <th>{t("torrent.col.name")}</th><th>{t("torrent.col.state")}</th><th>{t("torrent.col.progress")}</th>
                  <th>{t("torrent.col.speed")}</th><th>{t("torrent.col.eta")}</th><th>{t("torrent.col.diagnosis")}</th>
                  <th><span className="sr-only">{t("torrent.col.actions")}</span></th>
                </tr></thead>
                <tbody>{items.map((x) => {
                  const expanded = open === x.hash;
                  const canReannounce = x.diagnosis?.action?.kind === "reannounce";
                  return (
                    <Fragment key={x.hash}>
                      <tr>
                        <td data-label={t("torrent.col.expand")}>
                          <Button size="sm" variant="ghost" iconOnly icon={expanded ? ChevronDown : ChevronRight} aria-expanded={expanded} onClick={() => setOpen(expanded ? null : x.hash)}>
                            {t(expanded ? "torrent.collapse" : "torrent.expand")}
                          </Button>
                        </td>
                        <td className="cell-primary">
                          <div style={{ overflowWrap: "anywhere" }}>{x.name}</div>
                          <div className="row wrap" style={{ gap: 4, marginTop: 4 }}>
                            {x.category ? <span className="tag">{x.category}</span> : null}
                            <PrivateChip value={x.private} />
                            {x.suspicious ? <StatusChip tone="danger" icon={ShieldAlert} title={x.suspicious.explanation}>{t("torrent.suspicious")}</StatusChip> : null}
                            {x.tracker_host ? <span className="t-3 mono" style={{ fontSize: 11.5 }}>{x.tracker_host}</span> : null}
                          </div>
                        </td>
                        <td data-label={t("torrent.col.state")}><StateChip state={x.state} /></td>
                        <td data-label={t("torrent.col.progress")} style={{ minWidth: 120 }}>
                          <Progress value={(x.progress ?? 0) * 100} thin label={t("torrent.col.progress")} tone={(x.progress ?? 0) >= 1 ? "success" : undefined} />
                          <span className="t-2" style={{ fontSize: 12 }}>{pct((x.progress ?? 0) * 100, 1)} · {bytes(x.size)}</span>
                        </td>
                        <td data-label={t("torrent.col.speed")}>
                          <div style={{ fontSize: 12.5 }}>↓ {rate(x.dlspeed)}</div>
                          <div style={{ fontSize: 12.5 }}>↑ {rate(x.upspeed)}</div>
                          <div className="t-3" style={{ fontSize: 11.5 }}>{t("torrent.seedsPeers", { seeds: num(x.num_seeds ?? 0), peers: num(x.num_leechs ?? 0) })}</div>
                        </td>
                        <td data-label={t("torrent.col.eta")}><EtaCell row={x} /></td>
                        <td data-label={t("torrent.col.diagnosis")} style={{ maxWidth: 320 }}>
                          <DiagnosisChip finding={x.diagnosis} />
                          {x.diagnosis ? <div className="t-2" style={{ fontSize: 12, marginTop: 4 }}>{x.diagnosis.explanation}</div> : null}
                          {x.findings_count > 1 ? <div className="t-3" style={{ fontSize: 11.5 }}>{t("torrent.moreFindings", { count: num(x.findings_count - 1) })}</div> : null}
                        </td>
                        <td data-label={t("torrent.col.actions")}>
                          <div className="toolbar" style={{ justifyContent: "flex-end" }}>
                            {x.recheck_suggested ? (
                              <Button size="sm" icon={ScanSearch} pending={pending === `recheck:${x.hash}`} disabled={!isOperator} title={isOperator ? x.recheck_reason ?? undefined : t("torrent.operatorOnly")} onClick={() => void preview(x, "recheck")}>{t("torrent.recheck")}</Button>
                            ) : null}
                            {canReannounce ? (
                              <Button size="sm" icon={Megaphone} pending={pending === `reannounce:${x.hash}`} disabled={!isOperator} title={isOperator ? undefined : t("torrent.operatorOnly")} onClick={() => void preview(x, "reannounce")}>{t("torrent.reannounce")}</Button>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                      {expanded ? <tr><td colSpan={8}><Details hash={x.hash} /></td></tr> : null}
                    </Fragment>
                  );
                })}</tbody>
              </table>
            </div>
          )}
        </ResourceView>
      </Card>
      <p className="hint" style={{ margin: 0 }}>{t("torrent.tab.never")}</p>
      <ChangeDialog change={change} onClose={() => setChange(null)} onChanged={() => void list.reload()} />
    </div>
  );
}
