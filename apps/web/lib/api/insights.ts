// API types for Slice E: overview, costs, automation, AI advisor (backend: apps/core/mso/features/{overview,costs,automation,ai}).
import type { ChangeSet, OperatingMode, Risk } from "@/lib/api/contracts";

// ------------------------------------------------------------------ overview  GET /overview
export interface Unavailable { available: boolean; reason?: string | null }
export interface OverviewConnector {
  id: string; kind: string; label: string; configured: boolean; status: string; version: string | null;
  last_checked_at: string | null; stale_s: number | null; message: string | null;
}
export interface FailureLocation { kind: "source" | "provider" | "client"; id: string; label: string; count: number; reasons: Record<string, number> }
export interface Overview {
  mode: OperatingMode; paused: boolean; automation_allowed: boolean;
  health: { status: "healthy" | "degraded" | "offline" | "unknown"; issues: string[] };
  connectors: Unavailable & { items: OverviewConnector[]; counts: Record<string, number> };
  downloads: Unavailable & { active: { usenet: number; torrent: number }; total_active?: number; stale_s: number | null };
  failures: Unavailable & {
    last_24h: number; last_7d: number; finished_7d?: number; failure_rate_7d?: number | null;
    by_reason: Array<{ reason: string; label: string; last_24h: number; last_7d: number }>; by_location: FailureLocation[];
  };
  recommendations: Unavailable & { open: number; by_risk: Record<Risk, number>; top: Array<{ id: number; area: string; title: string; risk: Risk; confidence: number | null; updated_at: string }> };
  changes: Unavailable & { open: number; failed_7d: number; items: Array<{ id: number; target: string; title: string; risk: Risk; status: string; created_at: string }> };
  costs: Unavailable & { monthly_cents?: number | null; currency?: string; services?: number; unknown?: Array<{ id: number; label: string; reason: string | null }> };
  jobs: Unavailable & { failing_24h: number; failing: Array<{ kind: string; n: number; last_failed: string; last_error: string | null }>; queued: number; running: number; worker: { seen: boolean; stale_s: number | null; healthy: boolean } };
  mediaguard: Unavailable & {
    mode: "standalone" | "degraded" | "connected"; mode_label: string; version?: string | null;
    /** Age of the MediaGuard status check (probe). */
    stale_s: number | null;
    /** Age of the last wanted-list sync (also a partial run). */
    wanted_synced_at?: string | null; wanted_sync_s?: number | null; wanted_sync_partial?: boolean;
  };
  backup: Unavailable & { age_s: number | null; stale?: boolean };
  recent_audit: Array<{ id: number; ts: string; actor: string; action: string; target: string | null; summary: string }>;
  generated_at: string;
}

// ------------------------------------------------------------------ costs
export type Billing = "monthly" | "yearly" | "block" | "free";
export type ServiceKind = "usenet_provider" | "block_account" | "nzb_indexer" | "torrent_indexer" | "tracker" | "vpn" | "other";
export interface MonthlyCost { monthly_cents: number | null; method: string; reason?: string; per_gb_cents?: number; per_success_cents?: number }
export interface Service {
  id: number; source_id: string | null; source_label: string | null; kind: ServiceKind; kind_label?: string; label: string;
  billing: Billing; amount_cents: number; currency: string; block_size_gb: number | null; block_used_gb: number | null;
  renewal_date: string | null; notes: string; active: boolean; backbone: string | null; monthly?: MonthlyCost;
  /** `import` = added automatically from a provider/indexer source; `price_missing` until the user fills in a price. */
  origin?: "manual" | "import"; price_missing?: boolean;
  metrics?: ServiceMetrics | null;
  /** billing === "free" (public torrent indexers are imported as free and never ask for a price). */
  free?: boolean;
}
export type ServiceInput = Omit<Service, "id" | "source_label" | "kind_label" | "monthly" | "origin" | "price_missing" | "metrics" | "free">;
/** `GET /costs/services` */
export interface ServiceList { items: Service[]; period_days: number; price_missing: number; free?: number; paid?: number }
/** Retrospective ratios over the period (entered cost ÷ observed successes); never a savings promise. */
export interface ServiceMetrics {
  period_days: number; linked: boolean; available: boolean; reason?: string;
  successful_downloads?: number; successful_gb?: number; period_cost_cents?: number | null;
  per_success_cents?: number | null; per_gb_cents?: number | null; cost_reason?: string; bytes_method?: string; coverage?: number | null;
  unique?: { available: boolean; reason?: string; pct?: number; unique_articles?: number; compared_articles?: number; confidence?: string; note?: string };
}
export interface PriceInput { billing: Billing; amount_cents: number; currency?: string; renewal_date: string | null; block_size_gb?: number | null }
export interface SourceOption { id: string; kind: string; label: string; origin: string; enabled: boolean }
export interface Range { low: number; high: number; low_pct: number | null; high_pct: number | null }
export interface Coverage { with_evidence: number; total: number; ratio: number | null }
export type ConfidenceLabel = "onbekend" | "laag" | "middel" | "hoog";
export interface Simulation {
  service_id: number; label: string; period_days: number; available: boolean; reason?: string; method: string;
  observed_successes?: number; involved?: number;
  buckets?: Array<{ key: string; label: string; count: number; explanation: string }>;
  affected_range?: Range; coverage?: Coverage; confidence?: ConfidenceLabel;
  examples?: Array<{ title: string; bucket: string; ts: string; also_from?: string[] }>;
  method_notes?: string[]; disclaimers: string[]; never_auto_cancel: boolean;
  entered_cost?: { monthly_cents: number | null; currency: string; note: string };
}
export interface Analysis {
  service: Service; period_days: number; available: boolean; reason: string | null; cost: MonthlyCost;
  usage: { bytes: number; gb: number; successful_downloads: number; failed_downloads: number; bytes_method: string };
  unique: { value: number | null; method: string | null; confidence: ConfidenceLabel; note: string; range?: Range; coverage?: number | null;
    articles?: Record<string, unknown>; articles_note?: string };
  latency: { available: boolean; n: number; p50_ms?: number; p95_ms?: number; reason?: string };
  failures_by_reason: Array<{ reason: string; label: string; count: number }>;
  recovered: { count: number; available: boolean; method?: string };
  block: { size_gb: number | null; used_gb: number | null; remaining_gb: number | null; observed_gb_per_month: number | null; est_months_left: number | null; note: string } | null;
  renewal: { date: string; days_left: number } | null;
  disclaimers: string[];
}
export interface Scenario {
  label: string; service_ids: number[]; services: string[]; monthly_cents: number; monthly_complete: boolean;
  backbones: Array<{ label: string; backbone: string | null }>; distinct_known_backbones: number; unknown_backbones: number;
  observed: { successes_with_evidence: number; fully_within_set: number; needed_excluded_provider: number; needed_excluded_pct: number | null; unobserved_candidates: number };
  notes: string[];
}
export interface ScenarioList { period_days: number; items: Scenario[]; disclaimers: string[]; available: boolean; reason: string | null }
export interface CostSummary {
  total: { monthly_cents: number; currency: string; mixed_currencies: boolean; services: number; price_missing?: number; unknown: Array<{ id: number; label: string; reason: string | null }>; by_kind: Record<string, number> };
  renewals: Array<{ id: number; label: string; date: string; days_left: number; billing: Billing; amount_cents: number; currency: string }>;
  reminder_days: number; disclaimers: string[];
}

// ------------------------------------------------------------------ automation
export interface Recommendation {
  id: number; area: string; subject: string | null; title: string; explanation: string; impact: string; risk: Risk;
  confidence: number | null; status: "open" | "accepted" | "dismissed" | "resolved"; change_id: number | null;
  evidence: Record<string, unknown>; created_at: string; updated_at: string;
  change: (Pick<ChangeSet, "id" | "target" | "title" | "risk" | "status"> & { applied_at: string | null; evaluate_after: string | null; impact: unknown }) | null;
  can_create_change: boolean;
}
export interface AutomationStatus {
  mode: OperatingMode; paused: boolean; automation_allowed: boolean;
  /** Autopilot active (mode autopilot, not paused): every area is on, regardless of its own toggle. */
  autopilot?: boolean;
  areas: Array<{ area: string; label: string; auto_apply_safe: boolean; setting_key: string; pending_safe_changes: number; effective?: boolean; via_autopilot?: boolean }>;
  /** Effective (true in Autopilot); `auto_rollback_setting` is the stored setting itself. */
  max_per_hour: number; max_age_h: number; auto_rollback: boolean; auto_rollback_setting?: boolean; applied_last_hour: number; evaluators: string[];
  evaluator_fallback?: boolean;
  evaluation?: { hours: number; hysteresis_pct: number; min_samples: number; torrent_auto_rollback: boolean | null };
  risk_explanation: Record<Risk, string>;
  log: Array<{ id: number; ts: string; kind: string; change_id: number | null; area: string | null; result: string; message: string }>;
  jobs: Array<{ kind: string; failed_24h: number; pending: number; last_update: string | null }>;
}

// ------------------------------------------------------------------ AI advisor
export interface AiStatus {
  enabled: boolean; active: boolean; reason: string | null; provider: string; model: string; effort: string; max_output_tokens: number;
  monthly_budget_eur: number; key: "ingesteld" | "leeg";
  price: { input_cents_per_mtok: number; output_cents_per_mtok: number } | null;
  usage_month: { spent_cents: number; calls: number; refused_or_failed: number; input_tokens: number; output_tokens: number };
  remaining_cents: number;
}
export interface AiAdvice { mode: "ai" | "deterministic"; fallback_reason: string | null; title: string; text: string; recommendation_id: number; model: string | null }

// ------------------------------------------------------------------ controle na een wijziging  GET /automation/evaluations
export type EvaluationOutcome = "beter" | "gelijk" | "slechter" | "onvoldoende" | "niet_van_toepassing";
export interface EvaluationMeasure {
  key: string; label: string; unit: string; scope?: string; verdict: string; outcome?: string; stat?: "median" | "mean";
  baseline: { n: number; value: number | null }; current: { n: number; value: number | null };
  delta_pct: number | null; hysteresis_pct?: number; higher_is_better?: boolean; why?: string;
}
export interface StoredEvaluation {
  /** Current vocabulary (EvaluationOutcome) or legacy values from before 1.4.0 (keep/rollback/unknown/niet_beschikbaar). */
  result: string; label?: string; at?: string; message?: string; action?: string; reason?: string; setting?: string;
  evaluator?: string; measures?: EvaluationMeasure[];
  /** Set when the change was rolled back before it was measured ("n.v.t. (teruggedraaid)"). */
  rolled_back?: boolean;
}
export interface ChangeEvaluation {
  id: number; target: string; title: string; risk: Risk; status: string; applied_at: string | null; applied_by: string | null;
  rolled_back_at: string | null; evaluate_at: string | null; applicable: boolean;
  evaluation: StoredEvaluation | null; interim: StoredEvaluation | null; auto_rollback: { enabled: boolean; setting: string };
}

/** Audit summaries written before 1.4.0 (and by modules that still pass raw statuses) → Dutch labels. */
const AUDIT_STATUS_NL: Record<string, string> = {
  healthy: "Gezond", degraded: "Verstoord", offline: "Offline", auth_failed: "Inloggen geweigerd", unknown: "Onbekend",
  error: "Fout", ok: "Gezond", unconfigured: "Niet ingesteld", missing_credentials: "Inloggegevens ontbreken",
  inconclusive: "Onbeslist", kept: "Behouden", rolled_back: "Teruggedraaid", degraded_pending: "Verslechterd (wacht op besluit)",
  discarded: "Verworpen", evaluating: "Wordt gemeten",
};
export function localizeAuditSummary(summary: string | null | undefined): string | null {
  if (!summary) return summary ?? null;
  return summary.replace(/(:\s*)([a-z_]+)(?=\s*(?:\(|$|[.,;]))/g, (m, sep: string, word: string) => (AUDIT_STATUS_NL[word] ? `${sep}${AUDIT_STATUS_NL[word]}` : m));
}

// ------------------------------------------------------------------ autopilot  GET /overview/autopilot?days=1|7
export type AutopilotCategoryKey = "applied" | "switched_off" | "rolled_back" | "servers" | "retries" | "searches" | "prepared" | "evaluated" | "syncs" | "failed" | "other";
export interface AutopilotItem {
  id: number; ts: string; category: AutopilotCategoryKey; action: string; summary: string; target: string | null;
  change_id: number | null; actor: string; link: string;
}
export interface AutopilotCategory {
  key: AutopilotCategoryKey; label: string; link: string; tone: "success" | "warning" | "info" | "neutral" | "danger";
  count: number; examples: AutopilotItem[];
}
export interface WaitingItem {
  id: number | string; title: string; target: string; risk: Risk; status: string; created_at: string;
  kind: "goedkeuren" | "toepassen" | "bekijken"; reason: string; deletes: boolean; ops: number; link: string;
}
export interface AutopilotSummary {
  days: number; since: string; generated_at: string; mode: OperatingMode; paused: boolean; autopilot: boolean; automation_allowed: boolean;
  total: number; categories: AutopilotCategory[]; items: AutopilotItem[];
  by_day: Array<{ date: string; total: number; categories: Partial<Record<AutopilotCategoryKey, number>>; items: AutopilotItem[] }>;
  syncs: { jobs: Array<{ kind: string; done: number; failed: number }>; failed: number };
  waiting: { count: number; items: WaitingItem[] };
}

// ------------------------------------------------------------------ automatische taken  GET /automation/schedules
export interface ScheduleItem {
  kind: string; label: string; area: string; every_s: number; enabled: boolean; reason: string | null;
  status: "ok" | "mislukt" | "bezig" | "nog_niet" | "uit";
  last_run_at: string | null; last_status: string | null; last_error: string | null; last_summary: string | null;
  failed_24h: number; next_run_at: string | null;
}
export interface ScheduleList { items: ScheduleItem[]; worker_hint: string }
