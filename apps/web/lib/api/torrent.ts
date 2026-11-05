// API types for the torrent engine (/api/v1/torrent/*) — Slice C.
import type { ChangeSet, Risk } from "@/lib/api/contracts";

export type Severity = "critical" | "warning" | "info";

export interface TorrentFinding {
  code: string; title: string; explanation: string; severity: Severity; confidence: number;
  evidence: Record<string, unknown>; suggestion: string | null;
  action: { kind: "recheck" | "reannounce"; label: string } | null;
}

export interface ObligationRule {
  rule_id: number; label: string; min_ratio: number | null; min_seed_time_h: number | null; mode: "either" | "both";
  met: boolean; ratio_progress: number | null; time_progress: number | null;
}
export interface Obligation {
  status: "geen_regel" | "gehaald" | "loopt" | "nog_downloaden"; met: boolean; rules: ObligationRule[];
  ratio: number; seeding_time_h: number; private: boolean | null; warning: boolean; tracker?: string | null;
}

export interface TorrentRow {
  hash: string; name: string; category: string | null; media_category: string | null; state: string | null;
  private: boolean | null; tracker_host: string | null; progress: number | null; size: number | null;
  amount_left: number | null; dlspeed: number | null; upspeed: number | null; num_seeds: number | null;
  num_leechs: number | null; num_complete: number | null; availability: number | null; ratio: number | null;
  seeding_time_s: number | null; eta_s: number | null; eta_unreliable: boolean; realistic_eta_s: number | null;
  eta_reasons: string[]; diagnosis: TorrentFinding | null; findings_count: number; recheck_suggested: boolean;
  recheck_reason: string | null; obligation: Obligation | null; stalled_for_h: number | null; updated_at: string;
  /** suspicious_payload finding (executable in a media download) when present, else null */
  suspicious: TorrentFinding | null;
}
export interface TrackerRow {
  host: string | null; kind: string; status: string; msg: string; seeds: number | null; peers: number | null;
  leeches: number | null; tier: number | null;
}
export interface TorrentDetail extends TorrentRow {
  findings: TorrentFinding[]; trackers: TrackerRow[]; save_path: string | null;
}
export interface TorrentList { items: TorrentRow[]; updated_at: string | null; stale: boolean; connector: TorrentConnectorInfo }

export interface TorrentConnectorInfo {
  configured: boolean; status: string; version: string | null; capabilities: Record<string, unknown> | null;
  last_checked_at: string | null; last_error: string | null;
}

export interface Connectivity {
  connection_status: "connected" | "firewalled" | "disconnected" | null; listen_port: number | null;
  upnp: boolean | null; random_port: boolean | null; dht: boolean | null; pex: boolean | null; lsd: boolean | null;
  dht_nodes: number | null; total_peer_connections: number | null; bound_interface: string; bound_address: string;
  expected_interface: string; interfaces: string[]; binding_ok: boolean | null; port_ok: boolean | null;
}

export interface TorrentOverview {
  connector: TorrentConnectorInfo;
  client: {
    version: string | null; webapi: string | null; auth_method: "api_key" | "session" | null;
    capabilities: Record<string, unknown>; updated_at: string; error: string | null; stale: boolean;
  } | null;
  transfer: Record<string, number | boolean | null>;
  connectivity: Connectivity | null;
  findings: TorrentFinding[];
  diagnosis_counts: Record<string, number>;
  state_counts: Record<string, number>;
  /** torrents with a suspicious_payload finding (primary or not) */
  suspicious_count: number;
  mode: string; paused: boolean;
}

export interface PreferenceItem {
  key: string; label: string; kind: "int" | "rate" | "bool" | "float" | "string"; value: unknown;
  min: number | null; max: number | null; present: boolean;
  /** "buiten_grens" = outside MSO's safe bounds; null = not judged (no bound known) */
  bounds: "ok" | "buiten_grens" | null; target: number | null;
}
export interface PreferenceList { items: PreferenceItem[]; out_of_bounds: number; updated_at: string | null; stale: boolean }
export interface PrefViolation { key: string; label: string; value: number; min: number | null; max: number | null; target: number }

export interface TorrentPolicy {
  speed: {
    line_up_mbit: number; line_down_mbit: number; upload_cap_pct: number; max_upload_bps: number | null;
    current_up_limit: number | null; current_dl_limit: number | null; status: "ok" | "boven_grens" | "onbekend";
    max_connections_cap: number; max_connections_per_torrent_cap: number; current_max_connec: number | null;
    current_max_connec_per_torrent: number | null; current_max_uploads: number | null;
    current_max_uploads_per_torrent: number | null; max_uploads_cap: number; max_uploads_per_torrent_cap: number;
    out_of_bounds: PrefViolation[];
  };
  queue: {
    queueing_enabled: boolean | null; max_active_downloads: number | null; max_active_uploads: number | null;
    max_active_torrents: number | null; dont_count_slow_torrents: boolean | null; queued_downloads: number;
    active_downloads: number; stalled_downloads: number; suggestions: Array<{ key: string; after: unknown; why: string }>;
  };
  private: { has_private_torrents: boolean; dht: boolean | null; pex: boolean | null; lsd: boolean | null; add_trackers_enabled: boolean | null; note: string };
  share: Record<string, unknown>;
  updated_at: string | null;
}

export interface TrackerRule {
  id: number; tracker_host: string | null; category: string | null; label: string; private: boolean;
  min_ratio: number | null; min_seed_time_h: number | null; mode: "either" | "both"; allow_cross_seed: boolean;
  notes: string; created_at: string; updated_at: string;
}
export interface TrackerRuleList { items: TrackerRule[]; known_trackers: Array<{ host: string; private: boolean; torrents: number | null }> }

export interface MetricStats { metric: string; n: number; median: number | null; mean: number | null; from: string; to: string }
export interface TuningRun {
  id: number; change_id: number | null; param: string; kind: "tuning" | "guardrail" | "disk_io";
  direction: "up" | "down" | "set" | null; before_value: unknown; after_value: unknown; metric: string;
  metric_label: string; metric_unit: string | null; higher_is_better: boolean; baseline: Partial<MetricStats>;
  result: Partial<MetricStats> & { verdict?: string; delta_pct?: number | null; why?: string };
  reason: string; status: "proposed" | "evaluating" | "kept" | "rolled_back" | "degraded_pending" | "inconclusive" | "discarded";
  evaluate_after_h: number; created_at: string; applied_at: string | null; evaluated_at: string | null; note: string;
  change_status: string | null; change_risk: Risk | null; evaluate_at?: string; seconds_left?: number;
  interim?: Partial<MetricStats> & { verdict?: string; delta_pct?: number | null; why?: string };
}
export interface TuningList {
  items: TuningRun[];
  settings: { autotune_enabled: boolean; auto_rollback: boolean; evaluation_hours: number; baseline_hours: number; hysteresis_pct: number; cooldown_hours: number };
  automation_allowed: boolean; mode: string; paused: boolean; open_run: number | null;
}
export interface ProposeResult { reason?: string; run?: TuningRun; change?: ChangeSet; auto?: string | null; bundle?: boolean }

/** GET /torrent/suspicious — suspicious torrents of the last scan + the open cleanup proposal. */
export interface SuspiciousTorrent { hash: string; name: string; category: string | null; tracker_host: string | null; size: number | null; files: string[] }
export interface SuspiciousList { items: SuspiciousTorrent[]; open_change: ChangeSet | null }

export interface StorageReport {
  client: { available: boolean; updated_at: string | null; error: string | null };
  disk: { free: number; remaining_downloads: number; free_after_downloads: number; reserve: number; status: "ok" | "waarschuwing" | "kritiek" } | null;
  categories: Array<{ category: string; media_category: string | null; torrents: number; bytes: number; seeding: number; downloading: number; stopped: number; private: number }>;
  obligations: Array<Obligation & { hash: string; name: string; tracker: string | null; category: string | null; state: string | null }>;
  obligations_summary: { total: number; met: number; open: number; private_without_rule: number };
  media: { path: string; total: number; free: number; read_only: boolean; fstype: string | null; mountpoint: string | null; zfs_dataset: string | null } | null;
  hardlinks: {
    status: "ok" | "niet_ingesteld" | "onbereikbaar" | "niet_read_only"; message: string; files_checked?: number; unmapped?: number;
    hardlinked_count?: number; hardlinked?: Array<{ hash: string; torrent: string; file: string; nlink: number; private: boolean | null; tracker: string | null }>;
    scanned_at?: string;
  } | null;
  hardlinks_scanned_at: string | null;
  never: string[];
}

export interface CrossSeedCandidate {
  id: number; source_hash: string; candidate_hash: string; tracker_host: string | null; title: string;
  release_source: string | null; files: number; total_size: number; save_path: string | null; category: string | null;
  status: "candidate" | "previewed" | "added" | "rejected"; change_id: number | null; change_status: string | null;
  source_name: string | null; created_at: string;
}
export interface CrossSeedList { enabled: boolean; search_available: boolean; allowed_trackers: string[]; items: CrossSeedCandidate[] }
export interface JobRef { job_id: number; status: string }
