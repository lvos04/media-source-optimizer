// Slice A — API types for zoeken (/api/v1/search), indexers (/api/v1/indexers) and the Newznab/Torznab endpoint
// (/api/v1/newznab). Mirrors apps/core/mso/features/{search,indexers,newznab_server}.
import type { ChangeSet, Connector, Risk } from "@/lib/api/contracts";

export type MediaCategory = "tv" | "tv-anime" | "movies" | "movies-anime";
export type Protocol = "usenet" | "torrent";
export type SourceStatus =
  | "ok" | "empty" | "timeout" | "error" | "category_error" | "skipped_cycle" | "skipped_scope"
  | "rate_limited" | "backoff" | "auth_error";

export interface ParsedTitle {
  resolution: number | null; source: string | null; video_codec: string | null; hdr: string[];
  dv_profile: string | null; audio_codecs: string[]; audio_languages: string[]; dual_audio: boolean;
  multi_audio: boolean; subtitles: string[]; hardsub: boolean; season: number | null; episodes: number[];
  absolute_episodes: number[]; full_season: boolean; special: boolean; year: number | null;
  release_group: string | null; repack: boolean; proper: boolean; anime_style: boolean; obfuscated: boolean;
  confidence: number;
}

export interface SearchItem {
  token: string; release_key: string; protocol: Protocol; source_id: string; source_label: string;
  source_reliability: number | null; origin: string; title: string; guid: string; info_url: string | null;
  magnet_url: string | null; size_bytes: number | null; publish_date: string | null; age_days: number | null;
  categories: number[]; media_category: MediaCategory | null; tvdb_id: number | null; tmdb_id: number | null;
  imdb_id: string | null; seeders: number | null; leechers: number | null; grabs: number | null;
  private: boolean; freeleech: boolean | null; confidence: number; false_positive_flags: string[];
  parsed: ParsedTitle; trace: string[]; also_from: string[];
}

export interface SourceReport {
  source_id: string; label: string; status: SourceStatus; count: number; latency_ms: number | null; message: string;
}

export interface SearchResponse {
  items: SearchItem[]; sources: SourceReport[]; trace_id: string; trace: string[]; total: number; offset: number; limit: number;
}

export interface SendTarget {
  client: "sabnzbd" | "qbittorrent"; label: string; protocol: Protocol; available: boolean; categories: string[];
  reason: string | null;
}
export interface SendTargets { items: SendTarget[]; media_categories: MediaCategory[] }
export interface SendResult {
  status: "sent" | "already_sent";
  send: { release_key: string; client: string; category: string; status: string; client_ref: string | null; updated_at: string };
}

export interface CompareCandidate {
  token: string; title: string; release_key: string; media_category: MediaCategory | null;
  fields: Record<string, unknown>; rejections: string[] | null;
}
export interface CompareResult {
  candidates: CompareCandidate[]; fields: Array<{ key: string; label: string }>; differs: string[];
  same_identity: boolean; note: string;
}

// ---------------------------------------------------------------- indexers
export interface DirectIndexer {
  slug: string; source_id: string; kind: "newznab" | "torznab" | "spotweb"; protocol: Protocol; label: string;
  url: string; api_path: string; enabled: boolean; scopes: Array<"normal" | "anime">; private: boolean;
  rate_per_min: number; daily_quota: number; timeout_s: number; status: string; last_error: string | null;
  consecutive_failures: number; backoff_until: string | null; caps_checked_at: string | null;
  caps: { server: Record<string, string> | null; searching: Record<string, { available: boolean; params: string[] }> | null; limits: Record<string, number> | null; category_count: number };
  api_key: "ingesteld" | "leeg"; queries_today: number; created_at: string; updated_at: string;
}

export interface SourceStats {
  ok: number; empty: number; errors: number; median_latency_ms: number | null; last_search_at: string | null;
  last_status: string | null;
}

export interface IndexerSource {
  id: string; kind: "nzb_indexer" | "torrent_indexer" | "spotweb" | "header_index"; label: string;
  origin: "prowlarr" | "direct" | "mso"; protocol: Protocol; scopes: Array<"normal" | "anime">; private: boolean;
  enabled: boolean; reliability: number | null;
  reliability_detail: { samples: number; ok: number; failed: number; false_positives: number; median_latency_ms: number | null; window_days: number } | null;
  untagged: boolean; is_mso: boolean; stats: SourceStats | null; updated_at: string;
  prowlarr?: { prowlarr_id: number; implementation: string | null; definition: string | null; privacy: string | null; tags: string[]; disabled_till: string | null; most_recent_failure: string | null; avg_response_ms: number | null; queries: number | null; grabs: number | null; failed_queries: number | null; anime_name: boolean };
  direct?: DirectIndexer;
}
export interface IndexerList {
  items: IndexerSource[]; prowlarr: Pick<Connector, "id" | "url" | "enabled" | "status" | "version" | "last_checked_at" | "last_error"> | null;
  prowlarr_configured: boolean;
}

export interface IndexerTestResult {
  ok: boolean; status: string; message: string; latency_ms?: number | null; version?: string | null;
  capabilities?: { searching?: Record<string, { available: boolean; params: string[] }>; categories?: number[]; probe?: { ok: boolean; count?: number; message?: string } };
}

export interface ProwlarrConflict {
  code: string; severity: "error" | "warning" | "info"; subject: string; title: string; explanation: string;
  preview: Record<string, unknown>; action: "register" | null;
}
export interface ProwlarrView {
  mediaguard_managed: boolean; tags: Array<{ id: number; label: string }>;
  applications: Array<{ id: number; name: string; implementation: string; syncLevel: string; tags: string[]; mediaguard: boolean }>;
  indexers: Array<{ id: number; name: string; protocol: string; privacy: string; enable: boolean; tags: string[]; scopes: string[]; untagged: boolean; is_mso: boolean; anime_name: boolean }>;
  conflicts: ProwlarrConflict[]; policy: string;
}

export interface Recommendation {
  id: number; area: string; subject: string | null; title: string; explanation: string; impact: string; risk: Risk;
  confidence: number | null; status: string; change_id: number | null; dedupe_key: string | null;
  evidence: { code?: string; severity?: string; preview?: Record<string, unknown>; action?: string | null };
  updated_at: string;
}

export type RegisterResult =
  | { status: "preview"; change: ChangeSet; already: string[] }
  | { status: "already_registered"; kinds: string[] };

// ---------------------------------------------------------------- newznab endpoint
export interface NewznabConsumer {
  id: number; label: string; node: string; key_hint: string; enabled: boolean; rate_per_min: number | null;
  daily_quota: number | null; created_at: string; rotated_at: string | null; last_used_at: string | null; default: boolean;
  usage_today?: { queries: number; grabs: number };
}
export interface NewznabEndpoint {
  public_url: string | null; public_url_configured: boolean; newznab_url: string; torznab_url: string; api_path: string;
  default_consumer: NewznabConsumer; backends: string[]; max_limit: number;
  prowlarr_registrations: Array<{ source_id: string; label: string; base_url_kind: string | null }>;
  categories: Record<MediaCategory, number[]>;
}

export interface OriginGraph {
  nodes: string[]; edges: Array<{ src: string; dst: string; note: string }>; hubs: string[]; self: string; max_hops: number;
  cycles: Array<{ cycle: string[]; resolution: string }>;
}
