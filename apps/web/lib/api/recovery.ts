// API contracts for Slice D: MediaGuard integration (/api/v1/mediaguard) and recovery (/api/v1/recovery).

export type MgMode = "connected" | "standalone" | "degraded";
export type MediaCategory = "tv" | "tv-anime" | "movies" | "movies-anime";

export interface MgQuotaBlock { hour_used: number; hour_max: number; day_used: number; day_max: number; batch_size?: number; remaining: number }
export interface MgQuotaOverview {
  mso: { hour_used: number; hour_max: number; day_used: number; day_max: number; remaining: number };
  reserve: number;
  mediaguard: Record<string, MgQuotaBlock> | null;
  mediaguard_fetched_at: string | null;
  /** MediaGuard could not be re-read: the shown quota is older than 5 minutes. */
  mediaguard_stale?: boolean;
}

export interface CompatRoute {
  id: string; method: string; path: string; role: string; access: "read" | "write" | "auth"; label: string;
  purpose: string; ref: string; required: boolean; verified: boolean; live: "aanwezig" | "ontbreekt" | "onbekend";
}
export interface CompatMatrix {
  reference: { repo: string; commit: string };
  live_source: "openapi" | "probe" | null;
  checked_at: string | null;
  routes: CompatRoute[];
  never_used: Array<{ method: string; path: string; why: string }>;
}

export interface MgSyncState {
  ok?: boolean; error?: string | null; items?: number; pages?: number; complete?: boolean; total_open?: number;
  /** Multi-run cycle: next page to read (1 = cycle complete), items read in this cycle, rows in the cache. */
  next_page?: number; cycle_items?: number; cached?: number; first_page?: number; pages_total?: number;
  last_complete_at?: string | null;
  started_at?: string; finished_at?: string; duration_ms?: number; last_success_at?: string | null;
  last_error?: string | null; backoff_until?: string;
}

export interface MgStatus {
  mode: MgMode; reason: string; configured: boolean; version: string | null; role: string | null;
  auth_method: "apikey" | "user" | "token" | null; emergency_stop: boolean | null; reachable: boolean; latency_ms: number | null;
  checked_at: string | null; warnings: string[]; error: string | null; capabilities: Record<string, boolean | null>;
  compat: CompatMatrix; last_sync: MgSyncState | null;
  search: { available: boolean; reason: string | null; auto_enabled: boolean; automation_allowed: boolean };
  quota: MgQuotaOverview;
}

export interface MgSearchRequest {
  id: number; mg_id: number; instance_id: string | null; trigger: "manual" | "auto";
  status: "pending" | "sent" | "failed" | "refused_quota" | "refused_mediaguard_quota";
  actor: string; http_status: number | null; error: string | null; requested_at: string;
  response: Record<string, unknown>;
}
export interface MgSearchResult { duplicate: boolean; request: MgSearchRequest; quota: MgQuotaOverview; message: string }

export interface MgDiagnosisReason {
  code: string; label_nl: string; rule: string; ts: string; message_nl: string; suggested_fix_nl: string;
  severity: string; confidence: string; evidence?: unknown;
}
export interface MgDiagnosis { primary_code: string; ts: string; interactive: number | boolean; reasons: MgDiagnosisReason[] }
export type MgReasons = Record<string, { label_nl: string; fix_nl: string }>;

// ------------------------------------------------------------------ recovery
export interface BaselineCounts { [bucket: string]: number }
export interface Baseline {
  window_days: number;
  buckets: Array<{ key: string; label: string }>;
  totals: BaselineCounts;
  by_category: Record<string, BaselineCounts>;
  by_source: Array<{ source_id: string; label: string; origin: "download" | "mediaguard"; counts: BaselineCounts; total: number }>;
  raw_reasons: Record<string, Record<string, number>>;
  raw_labels: Record<string, string>;
  excluded_mediaguard: Record<string, number>;
  coverage: {
    failed_downloads: number; failed_downloads_with_reason: number; mediaguard_items: number;
    mediaguard_items_with_reason: number; known_share: number | null; confidence: string;
  };
  note: string;
}

export interface WantedRow {
  media_ref: string; origin: "mediaguard" | "manual"; title: string; media_category: MediaCategory | null;
  season_number: number | null; episode_number: number | null; absolute_episode_number: number | null;
  year?: number | null; status: string | null; reason_code: string | null; attempts: number | null;
  instance_id?: string; stale: boolean;
  candidates: { accepted: number; total: number; computed_at: string } | null;
}
export interface WantedList {
  items: WantedRow[]; total: number; page: number; page_size: number; mode: MgMode;
  /** When the wanted list was last read from MediaGuard (also a partial run). */
  mediaguard_synced_at?: string | null;
  /** Oldest cached row (can be older than `mediaguard_synced_at` while a multi-run sync cycle is underway). */
  mediaguard_fetched_at?: string | null; mediaguard_stale?: boolean;
  mediaguard_sync?: { partial: boolean; cycle_items: number | null; total_open: number | null; last_complete_at: string | null };
}

export interface RequirementsInput {
  min_resolution?: number | null; max_resolution?: number | null; require_hdr?: string[]; forbid_hdr?: string[];
  required_audio_languages?: string[]; require_dual_audio?: boolean; required_subtitles?: string[];
  forbid_hardsub?: boolean;
}
export interface RequirementsView {
  media_category: MediaCategory; season: number | null; episodes: number[]; absolute_episodes: number[];
  min_resolution: number | null; max_resolution: number | null; require_hdr: string[]; forbid_hdr: string[];
  required_audio_languages: string[]; require_dual_audio: boolean; required_subtitles: string[];
  forbid_hardsub: boolean; year: number | null; max_size_gb: number | null; blocked_groups: string[];
  informational: string[]; sources: string[]; policy_verified: boolean;
}

export interface EvidenceRow { key: string; label: string; required: string; actual: string; ok: boolean; reasons: string[] }
export interface Candidate {
  release_key: string; protocol: "usenet" | "torrent"; title: string; source_id: string; size_bytes: number | null;
  seeders: number | null; leechers?: number | null; grabs?: number | null; age_days: number | null;
  private?: boolean | null; has_download: boolean; score: number | null; score_basis: string[];
  passed: boolean; reasons: string[]; evidence: EvidenceRow[];
}
export interface CandidateResult {
  media_ref: string; requirements: RequirementsView;
  search: { trace_id: string | null; sources: Array<{ source_id: string; label?: string; status: string; count: number; message?: string }> };
  accepted: Candidate[]; rejected: Candidate[];
  counts: { total: number; accepted: number; rejected: number; blacklisted: number };
  computed_at: string;
}
export interface StoredCandidate {
  id: number; release_key: string; protocol: "usenet" | "torrent"; title: string; source_id: string | null;
  passed: boolean; score: number | null; reasons: string[]; evidence: EvidenceRow[]; computed_at: string;
  expires_at: string; expired: boolean; size_bytes: number | null; seeders: number | null;
}

export interface SendRow {
  id: number; media_ref: string; release_key: string; protocol: string; client: string; client_category: string | null;
  client_ref: string | null; title: string; status: "pending" | "sent" | "failed" | "download_failed" | "download_ok";
  error: string | null; actor: string; created_at: string;
}
export interface BlacklistEntry {
  id: number; release_key: string; media_ref: string | null; protocol: "usenet" | "torrent" | null; reason: string;
  failures: number; permanent: boolean; created_by: string; created_at: string; updated_at: string;
  expires_at: string | null; active: boolean;
}

export interface RecoveryItem {
  target: {
    media_ref: string; origin: "mediaguard" | "manual"; title: string; query: string; media_category: MediaCategory;
    kind: "episode" | "movie"; season: number | null; episode: number | null; absolute: number | null; year: number | null;
    instance_id: string | null;
  };
  requirements: RequirementsView;
  diagnosis: MgDiagnosis | null; diagnosis_error: string | null;
  candidates: StoredCandidate[];
  sends: SendRow[];
  search_requests: MgSearchRequest[];
  blacklist: BlacklistEntry[];
  backoff: { allowed: boolean; next_allowed_at: string | null; failures?: number };
  actions: {
    request_search: { available: boolean; reason: string | null };
    manual_send: { available: boolean; reason: string | null; category: string };
  };
}

/** `GET /recovery/fallbacks` — failed downloads the analysis looked at (1.4.0). */
export type FallbackStatus =
  | "waiting" | "skipped" | "no_candidate" | "proposed" | "sent" | "failed" | "discarded"
  /** server problem: SABnzbd retries the same job once the failing servers are off */
  | "retry"
  /** Autopilot asked MediaGuard to search again (MediaGuard stays owner) */
  | "mg_requested";
export interface FallbackRow {
  id: number; outcome_id: number; failed_release_key: string | null; failed_title: string; protocol: "usenet" | "torrent" | null;
  failure_reason: string | null; failure_label: string; media_ref: string | null;
  mapping: "send" | "mediaguard_ids" | "mediaguard_title" | "outcome_meta" | null; status: FallbackStatus; reason: string | null;
  change_id: number | null; change_status: string | null; change_risk: string | null;
  candidate_release_key: string | null; candidate_title: string | null;
  next_attempt_at: string | null; created_at: string; updated_at: string;
}
export interface FallbackList {
  items: FallbackRow[];
  settings: { fallback_on_failure: boolean; auto_send_fallback: boolean; allow_manual_send_connected: boolean; searches_per_run: number; autopilot_fallback?: boolean };
  mediaguard_mode: MgMode;
  /** Counts per status; `not_wanted` = items MediaGuard no longer wants (skipped silently, not a problem). */
  summary?: { by_status: Partial<Record<FallbackStatus, number>>; not_wanted: number; autopilot: boolean };
}
