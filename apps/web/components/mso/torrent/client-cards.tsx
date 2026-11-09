"use client";

import { Activity, Check, Gauge, KeyRound, Network, Plug, ShieldAlert, X } from "lucide-react";

import { Card, InlineState, StatusChip } from "@/components/mg/primitives";
import { DiagnosisChip, FindingList, useRate } from "@/components/mso/torrent/shared";
import type { TorrentOverview } from "@/lib/api/torrent";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { enumLabel } from "@/lib/status";

const CAP_LABEL: Record<string, MessageKey> = {
  sync_maindata: "torrent.cap.sync_maindata",
  share_limits: "torrent.cap.share_limits",
  inactive_seeding_time_limit: "torrent.cap.inactive_seeding_time_limit",
  infohash_v2: "torrent.cap.infohash_v2",
  private_flag_in_info: "torrent.cap.private_flag_in_info",
  build_info: "torrent.cap.build_info",
  network_interfaces: "torrent.cap.network_interfaces",
  free_space_on_disk: "torrent.cap.free_space_on_disk",
  api_key: "torrent.cap.api_key",
};

export function ClientCard({ data }: { data: TorrentOverview }) {
  const { t, relative } = useI18n();
  const c = data.client;
  const caps = c?.capabilities ?? {};
  return (
    <Card className="span-6" title={t("torrent.client.title")} icon={Plug}>
      {!c ? <InlineState tone="info">{t("torrent.client.noSnapshot")}</InlineState> : (
        <div className="stack">
          {c.error ? <InlineState tone="danger" role="alert">{t("torrent.client.error", { error: c.error })}</InlineState> : null}
          {c.stale && !c.error ? <InlineState tone="stale">{t("torrent.staleData")}</InlineState> : null}
          <dl className="kv">
            <dt>{t("torrent.client.version")}</dt><dd className="mono">{c.version ?? "—"}</dd>
            <dt>{t("torrent.client.webapi")}</dt><dd className="mono">{c.webapi ?? "—"}</dd>
            <dt>{t("torrent.client.auth")}</dt>
            <dd><StatusChip tone="neutral" icon={KeyRound}>{t(c.auth_method === "api_key" ? "torrent.client.authKey" : "torrent.client.authSession")}</StatusChip></dd>
            {typeof caps.api_key_status === "string" && caps.api_key_status !== "niet_ingesteld" ? (
              <><dt>{t("torrent.client.keyStatus")}</dt><dd>{String(caps.api_key_status)}</dd></>
            ) : null}
            {typeof caps.libtorrent === "string" ? <><dt>libtorrent</dt><dd className="mono">{caps.libtorrent}</dd></> : null}
            <dt>{t("torrent.client.updated")}</dt><dd>{relative(c.updated_at)}</dd>
          </dl>
          <div>
            <span className="eyebrow">{t("torrent.client.capabilities")}</span>
            <div className="row wrap" style={{ gap: 6, marginTop: 6 }}>
              {Object.entries(CAP_LABEL).map(([k, label]) => (
                <StatusChip key={k} tone={caps[k] ? "success" : "neutral"} icon={caps[k] ? Check : X} title={k}>{t(label)}{caps[k] ? "" : ` — ${t("torrent.client.notSupported")}`}</StatusChip>
              ))}
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

export function ConnectivityCard({ data }: { data: TorrentOverview }) {
  const { t, num } = useI18n();
  const c = data.connectivity;
  const connFindings = data.findings.filter((f) => ["firewalled", "disconnected", "dht_no_nodes", "vpn_binding_mismatch", "vpn_interface_missing", "binding_unknown"].includes(f.code));
  return (
    <Card className="span-6" title={t("torrent.conn.title")} icon={Network}>
      {!c ? <InlineState tone="info">{t("torrent.client.noSnapshot")}</InlineState> : (
        <div className="stack">
          <dl className="kv">
            <dt>{t("torrent.conn.status")}</dt>
            <dd>{c.connection_status === "connected"
              ? <StatusChip tone="linked">{t("torrent.conn.connected")}</StatusChip>
              : c.connection_status === "firewalled" ? <StatusChip tone="warning">{t("torrent.conn.firewalled")}</StatusChip>
                : <StatusChip tone="danger">{t("torrent.conn.disconnected")}</StatusChip>}</dd>
            <dt>{t("torrent.conn.port")}</dt><dd className="mono">{c.listen_port ?? "—"}{c.upnp ? " · UPnP" : ""}{c.random_port ? ` · ${t("torrent.conn.randomPort")}` : ""}</dd>
            <dt>{t("torrent.conn.peers")}</dt><dd>{num(c.total_peer_connections ?? 0)}</dd>
            <dt>DHT / PeX / LSD</dt><dd>{[c.dht, c.pex, c.lsd].map((v) => (v ? t("torrent.on") : t("torrent.off"))).join(" / ")} · {t("torrent.conn.dhtNodes", { count: num(c.dht_nodes ?? 0) })}</dd>
            <dt>{t("torrent.conn.interface")}</dt>
            <dd>
              <span className="mono">{c.bound_interface || t("torrent.conn.anyInterface")}</span>
              {c.expected_interface ? (
                <> · {c.binding_ok ? <StatusChip tone="success">{t("torrent.conn.bindingOk", { iface: c.expected_interface })}</StatusChip>
                  : <StatusChip tone="danger">{t("torrent.conn.bindingWrong", { iface: c.expected_interface })}</StatusChip>}</>
              ) : <> · <StatusChip tone="neutral">{t("torrent.conn.noExpected")}</StatusChip></>}
            </dd>
            {c.interfaces.length ? <><dt>{t("torrent.conn.available")}</dt><dd className="mono">{c.interfaces.join(", ")}</dd></> : null}
          </dl>
          <FindingList findings={connFindings} empty={t("torrent.conn.allGood")} />
          <p className="hint" style={{ margin: 0 }}>{t("torrent.conn.diagnosisOnly")}</p>
        </div>
      )}
    </Card>
  );
}

export function BottleneckCard({ data }: { data: TorrentOverview }) {
  const { t, num } = useI18n();
  const rate = useRate();
  const counts = Object.entries(data.diagnosis_counts).sort((a, b) => b[1] - a[1]);
  const other = data.findings.filter((f) => !["firewalled", "disconnected", "dht_no_nodes", "vpn_binding_mismatch", "vpn_interface_missing", "binding_unknown"].includes(f.code));
  const tr = data.transfer;
  return (
    <Card className="span-12" title={t("torrent.bottleneck.title")} icon={Activity}>
      <div className="stack">
        {data.suspicious_count ? (
          <InlineState tone="danger" role="alert">
            <ShieldAlert className="ico ico-sm" aria-hidden="true" /> {t("torrent.suspiciousSummary", { count: num(data.suspicious_count) })}
          </InlineState>
        ) : null}
        <dl className="kv">
          <dt>{t("torrent.bottleneck.download")}</dt><dd>{rate(tr.dl_info_speed as number | null)}{tr.dl_rate_limit ? ` (${t("torrent.limit")} ${rate(tr.dl_rate_limit as number)})` : ""}</dd>
          <dt>{t("torrent.bottleneck.upload")}</dt><dd>{rate(tr.up_info_speed as number | null)}{tr.up_rate_limit ? ` (${t("torrent.limit")} ${rate(tr.up_rate_limit as number)})` : ` (${t("torrent.unlimited")})`}</dd>
          <dt>{t("torrent.bottleneck.io")}</dt><dd>{num((tr.queued_io_jobs as number | null) ?? 0)} · {num((tr.average_time_queue as number | null) ?? 0)} ms</dd>
        </dl>
        <div>
          <span className="eyebrow">{t("torrent.bottleneck.perTorrent")}</span>
          <ul className="list" style={{ marginTop: 4 }}>
            {counts.map(([code, n]) => (
              <li key={code} className="list-row">
                <div className="list-main"><div className="list-title">{code === "ok" ? t("torrent.diag.ok") : enumLabel(t, "torrent.code", code)}</div></div>
                <span className="list-aside"><StatusChip tone={code === "ok" ? "success" : code === "suspicious_payload" ? "danger" : "neutral"} icon={code === "suspicious_payload" ? ShieldAlert : Gauge}>{num(n)}</StatusChip></span>
              </li>
            ))}
            {!counts.length ? <li className="list-row t-3">{t("torrent.empty")}</li> : null}
          </ul>
        </div>
        <div>
          <span className="eyebrow">{t("torrent.bottleneck.client")}</span>
          <div style={{ marginTop: 6 }}><FindingList findings={other} /></div>
        </div>
        {data.findings[0] ? <p className="t-3" style={{ margin: 0 }}>{t("torrent.bottleneck.top")}: <DiagnosisChip finding={data.findings[0]} /></p> : null}
      </div>
    </Card>
  );
}
