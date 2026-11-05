// Usenet engine (Slice B) API types — backend: apps/core/mso/features/usenet/api.py (/api/v1/usenet)

export type ProviderStatus =
  | "ok" | "auth_failed" | "account_expired" | "rate_limited" | "block_exhausted" | "unreachable"
  | "tls_error" | "timeout" | "error" | "unknown" | "missing_credentials"
  // only from SABnzbd's own server status:
  | "access_denied" | "sab_blocked" | "disabled"
  // only in automatic STAT samples:
  | "budget_exhausted";

/** SABnzbd's own view of one of its servers (`mode=status` + `mode=warnings`). */
export interface SabServerStatus {
  status: ProviderStatus; label: string; explanation: string; message: string | null; message_at: string | null;
  since: string | null; observed_at: string | null; active: boolean | null; source: "sabnzbd";
}
export type SecretStatus = "ingesteld" | "leeg";
export type ConfidenceLevel = "high" | "medium" | "low" | "none";

/** `GET /usenet/providers` → `{items}` */
export interface UsenetProvider {
  slug: string; source_id: string; label: string; kind: "unlimited" | "block";
  host: string; port: number; ssl: boolean; connections: number; mso_connections: number;
  retention_days: number | null; block_size_gb: number | null; block_used_bytes: number | null;
  block_size_bytes: number | null; block_used_ratio: number | null; sab_server: string | null;
  enabled: boolean; notes: string; status: ProviderStatus; status_detail: string | null;
  last_check_at: string | null; last_ok_at: string | null; capabilities: string[];
  credentials: { username: SecretStatus; password: SecretStatus }; credentials_complete: boolean;
  sab_status: SabServerStatus | null;
  /** How much MSO's own NNTP test still counts: only `current` tests (credentials, < 24 h, newer than
   *  SABnzbd's latest status change) determine the health; `stale` ones are shown as "verouderd". */
  mso_test: {
    status: ProviderStatus; detail: string | null; checked_at: string | null; stale_after: string | null;
    state: "current" | "stale" | "superseded" | "untested" | "missing_credentials";
  };
  /** Primary health: MSO's own current check, otherwise SABnzbd's view. */
  health: { status: ProviderStatus; detail: string | null; source: "mso" | "sabnzbd"; stale?: boolean };
  updated_at: string;
}
export interface ProviderPreset { slug: string; label: string; kind: "unlimited" | "block"; host: string; port: number; ssl: boolean }
/** `POST /usenet/providers/{slug}/test` */
export interface ProviderTestResult {
  status: ProviderStatus; message: string; capabilities: string[]; latency_ms: number | null; code: number | null;
  tested: boolean; provider: UsenetProvider;
}
/** `POST /usenet/providers/import-sab` */
export interface ProviderImportResult { items: UsenetProvider[]; note: string | null }
export interface ProviderInput {
  slug?: string; label?: string; kind?: "unlimited" | "block"; host?: string; port?: number; ssl?: boolean;
  connections?: number; retention_days?: number | null; block_size_gb?: number | null; sab_server?: string | null;
  enabled?: boolean; notes?: string; username?: string; password?: string;
}

/** `GET /usenet/sab/servers` */
export interface SabServer {
  name: string; displayname: string; port: number; ssl: boolean; connections: number; priority: number;
  enable: number; optional: number; retention: number; username_set: boolean;
  /** `status` is the provider's effective health (see `UsenetProvider.health`). */
  provider: { slug: string; label: string; kind: "unlimited" | "block"; status: ProviderStatus; source?: "mso" | "sabnzbd" } | null;
  period: { bytes: number; articles_tried: number | null; articles_success: number | null; success_ratio: number | null };
  daily: Array<{ day: string; bytes: number }>; total_bytes: number | null;
  sab_status: SabServerStatus | null; low_article_success: boolean;
  /** Autopilot state of this server (switched off by MSO, probe running, …); null = MSO never touched it. */
  autopilot: ServerAutopilot | null;
}

/** Autopilot: automatic off/on per SABnzbd server. */
export type ServerAutopilotStatus = "disabled" | "probing" | "recovered" | "given_up";
export interface ServerAutopilot {
  status: ServerAutopilotStatus; reason: string | null; reason_label: string | null; disabled_at: string | null;
  probes: number; next_probe_at: string | null; probe_started_at: string | null;
  probe_trigger: "autopilot" | "user" | "sabnzbd" | "own_test" | null; last_result: string | null;
  disable_change_id: number | null; updated_at: string | null;
}
export interface RetryRow {
  id: number; nzo_id: string; title: string; reason: string | null; reason_label: string | null;
  status: "proposed" | "retried" | "verified" | "failed"; trigger: "autopilot" | "proposal"; change_id: number | null;
  error: string | null; created_at: string; retried_at: string | null; verified_at: string | null;
}
export interface RetryQuota { hour_used: number; hour_max: number; day_used: number; day_max: number; left: number }
export interface AutopilotRunSummary {
  at: string;
  servers?: { autopilot?: boolean; switched_off?: string[]; switched_on?: string[]; probe_failed?: string[]; recovered?: string[]; kept_last?: string[]; error?: string };
  retry?: { status?: string; count?: number; more?: number; reason?: string; change_id?: number; auto_reason?: string | null; error?: string };
}
/** `GET /usenet/autopilot` */
export interface UsenetAutopilot {
  autopilot: boolean; auto_disable_after_h: number; probe_interval_h: number;
  servers: Record<string, ServerAutopilot | null>;
  retry: {
    enabled: boolean; counts: Partial<Record<RetryRow["status"], number>>; quota: RetryQuota;
    open_change: { id: number; title: string; risk: string } | null; items: RetryRow[]; last_run: AutopilotRunSummary | null;
  };
}
/** `POST /usenet/retries/propose` */
export interface RetryProposeResult {
  status: "off" | "waiting" | "nothing" | "quota" | "applied" | "preview" | "failed" | string; count: number; more?: number;
  reason?: string; change_id?: number; change_status?: string; auto_reason?: string | null;
}
export interface SabServerList { items: SabServer[]; period_days: number; last_stats_at: string | null }

/** `GET /usenet/contribution` */
export interface ProviderContribution {
  /** `slug` null + `kind` "sab_server": a SABnzbd server without MSO provider (passive figures only). */
  source_id: string; label: string; slug: string | null; kind: "unlimited" | "block" | "sab_server";
  articles_checked: number; available: number; unique_available: number | null; compared_articles: number;
  bytes_downloaded: number | null; failures_by_reason: Record<string, number>; coverage: number;
  confidence: ConfidenceLevel; method: string; nzbs_tested: number;
  passive: { server: string | null; articles_tried: number | null; articles_success: number | null; success_ratio: number | null };
  /** The one availability figure per row: own STAT sample, else SABnzbd's counters ("passief uit SABnzbd"). */
  availability: { ratio: number | null; source: "stat" | "passive" | null; label: string | null; articles: number };
  has_credentials: boolean;
}
export interface ContributionOverview {
  period_days: number; items: ProviderContribution[]; tests: number; notes: string[];
  sources?: { stat: number; passive: number };
  credentials_hint?: { providers: string[]; text: string } | null;
}

export interface UsenetNzb {
  id: number; nzo_id: string | null; origin: "send_url" | "send_nzb" | "upload"; title: string; category: string | null;
  release_key: string | null; source_id: string | null; groups: string[]; posted_at: string | null; files: number;
  segment_total: number; bytes_total: number; segments_known: number; testable: boolean; created_at: string;
}
export interface ArticleTestProvider {
  checked: number; available: number; missing: number; status: ProviderStatus; message: string | null;
  unique_available: number | null; availability: number | null;
}
export interface ArticleTest {
  id: number; nzb_id: number; nzb_title?: string; trigger: "failure" | "manual" | "auto_send"; status: "queued" | "running" | "done" | "failed";
  providers: string[]; sample_size: number; error: string | null; requested_by: string | null; job_id: number | null;
  created_at: string; finished_at: string | null;
  summary: { sample?: number; providers?: Record<string, ArticleTestProvider>; comparable_articles?: number; missing_everywhere?: number | null };
}

/** `GET /usenet/recommendations` */
export interface PriorityOp { path: string; label: string; before: number; after: number; reason?: string }
export interface ServerIssue {
  server: string; name: string; status: ProviderStatus; label: string; title: string; explanation: string;
  message: string | null; source: "sabnzbd" | "mso"; risk: "advies";
}
export interface PrioritySuggestion {
  ops: PriorityOp[]; method: "current_order" | "explicit_test"; notes: string[];
  insights: Array<{ slug: string; label: string; kind: "unique" | "fill" | "low_success"; text: string; confidence: ConfidenceLevel }>;
  issues: ServerIssue[];
  /** One-click proposal "Voorstel: N kapotte servers uitschakelen" → `POST /usenet/recommendations/disable-failing`. */
  disable: { count: number; ops: PriorityOp[]; servers: string[] };
  servers: Array<Omit<SabServer, "provider" | "period" | "daily" | "total_bytes" | "sab_status" | "low_article_success"> & { provider: string | null }>;
  risk: "toestemming_nodig"; period_days: number;
}

/** Downloads → Usenet */
export type CategorySource = "handmatig" | "automatisch" | "geen" | "nzb";
export interface SabQueueItem {
  nzo_id: string; name: string; category: string | null; media_category: string | null; category_source: CategorySource; status: string;
  percentage: number | null; size_mb: number | null; left_mb: number | null; timeleft: string | null; priority: string | null;
}
export interface SabQueue { items: SabQueueItem[]; status: string | null; paused: boolean; speed_kbps: number | null; left_mb: number | null; total: number; observed_at: string }
export interface SabHistoryItem {
  nzo_id: string; name: string; category: string | null; media_category: string | null;
  status: "active" | "success" | "failed"; sab_status: string; failure_reason: string | null; failure_label: string | null;
  failure_detail: string | null; certainty: "zeker" | "waarschijnlijk" | "onbekend" | null; evidence: string | null;
  likely_cause: {
    reason: string; label: string; certainty: "waarschijnlijk";
    servers: Array<{ server: string; name: string; status: ProviderStatus; label: string }>;
  } | null;
  category_source: CategorySource;
  size_bytes: number | null; finished_at: string | null; nzb_id: number | null; testable: boolean;
}
/** `GET /usenet/sab/categories` (and `PUT /usenet/sab/category-map`) */
export type MediaCategory = "tv" | "tv-anime" | "movies" | "movies-anime";
export interface SabCategory { name: string; media_category: MediaCategory | null; source: "handmatig" | "automatisch" | "geen"; auto: MediaCategory | null }
export interface SabCategories { items: SabCategory[]; mapping: Record<string, MediaCategory | null>; media_categories: MediaCategory[] }

export interface SabHistory { items: SabHistoryItem[]; total: number; observed_at: string }

/** `GET /usenet/headerindex` */
export interface HeaderIndexStatus {
  config: {
    enabled: boolean; groups: string[]; provider: string; retention_days: number; max_storage_mb: number;
    max_headers_per_run: number; max_runs_per_day: number; max_download_mb_per_run: number; max_run_seconds: number;
  };
  usage: { headers: number; estimated_bytes: number; physical_bytes: number; budget_bytes: number; ratio: number | null; budget_reached: boolean };
  metrics: {
    releases: number; headers: number; obfuscated_ratio: number | null; obfuscated_subject_ratio: number | null;
    new_releases_48h: number; usable_ratio_new: number | null;
  };
  runs: Array<{
    id: number; started_at: string; finished_at: string | null; status: "running" | "done" | "budget_reached" | "failed" | "skipped";
    headers_fetched: number; headers_stored: number; bytes_received: number; obfuscated: number; usable: number; message: string | null;
  }>;
  groups: Array<{ name: string; last_article: number | null; last_run_at: string | null; last_error: string | null }>;
  runs_today: number; source_id: string; disclaimer: string;
}

/** `GET /usenet/stat-budget` — automatic STAT sample after every NZB MSO sends. */
export interface StatUsage { connections: number; commands: number; bytes: number }
export interface StatBudget {
  enabled: boolean; sample_size: number;
  limits: { daily: StatUsage; monthly: StatUsage };
  providers: Array<{ slug: string; label: string; eligible: boolean; usage: { daily: StatUsage; monthly: StatUsage }; fit: number }>;
  last_auto_test_at: string | null;
}
