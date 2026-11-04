// Usenet engine (Slice B) API types — backend: apps/core/mso/features/usenet/api.py (/api/v1/usenet)

export type ProviderStatus =
  | "ok" | "auth_failed" | "account_expired" | "rate_limited" | "block_exhausted" | "unreachable"
  | "tls_error" | "timeout" | "error" | "unknown" | "missing_credentials"
  // only from SABnzbd's own server status:
  | "access_denied" | "sab_blocked" | "disabled";

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
  /** Primary health: MSO's own check when it has credentials, otherwise SABnzbd's view. */
  health: { status: ProviderStatus; detail: string | null; source: "mso" | "sabnzbd" };
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
  provider: { slug: string; label: string; kind: "unlimited" | "block"; status: ProviderStatus } | null;
  period: { bytes: number; articles_tried: number | null; articles_success: number | null; success_ratio: number | null };
  daily: Array<{ day: string; bytes: number }>; total_bytes: number | null;
  sab_status: SabServerStatus | null; low_article_success: boolean;
}
export interface SabServerList { items: SabServer[]; period_days: number; last_stats_at: string | null }

/** `GET /usenet/contribution` */
export interface ProviderContribution {
  source_id: string; label: string; slug: string; kind: "unlimited" | "block";
  articles_checked: number; available: number; unique_available: number | null; compared_articles: number;
  bytes_downloaded: number | null; failures_by_reason: Record<string, number>; coverage: number;
  confidence: ConfidenceLevel; method: string; nzbs_tested: number;
  passive: { server: string | null; articles_tried: number | null; articles_success: number | null; success_ratio: number | null };
}
export interface ContributionOverview { period_days: number; items: ProviderContribution[]; tests: number; notes: string[] }

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
  id: number; nzb_id: number; nzb_title?: string; trigger: "failure" | "manual"; status: "queued" | "running" | "done" | "failed";
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
    servers: Array<{ server: string; name: string; status: ProviderStatus }>;
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
