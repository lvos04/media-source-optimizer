// API types for the goals card (backend: apps/core/mso/features/overview/goals.py, GET /api/v1/overview/goals).

export type GoalKey = "failure_ratio" | "suspicious_torrents" | "failing_servers";
export type GoalStatus = "met" | "not_met" | "insufficient" | "unavailable";
export type GoalDirection = "better" | "worse" | "same" | null;
/** computed = from download_outcomes as known at the end of that day; snapshot = stored daily value;
 * reconstructed = derived from history before the first daily snapshot; live = measured now. */
export type TrendSource = "computed" | "snapshot" | "reconstructed" | "live";

export interface GoalTrendPoint {
  date: string; value: number | null; source: TrendSource;
  n?: number; gross?: number | null; gross_n?: number; insufficient?: boolean;
}

export interface GoalServer {
  server: string; name: string; status: string; label: string; since: string | null;
  reason?: string | null; reason_label?: string; disabled_at?: string | null; given_up?: boolean;
}

export interface Goal {
  key: GoalKey;
  label: string;
  unit: "ratio" | "count";
  target: number;
  comparison: "lt" | "eq";
  status: GoalStatus;
  direction: GoalDirection;
  available: boolean;
  reason: string | null;
  now: {
    value: number | null;
    // failure ratio (netto = per episode/film, bruto = per attempt)
    n?: number; failed?: number; insufficient?: boolean;
    gross?: number | null; gross_n?: number; gross_failed?: number; gross_insufficient?: boolean;
    // suspicious torrents
    new_7d?: number; awaiting_approval?: number; change_id?: number | null;
    // failing servers
    disabled_by_mso?: number; probing?: number;
  };
  start: { value: number | null; date: string; source: "computed" | "snapshot" | "reconstructed" | null; n?: number; gross?: number | null; gross_n?: number };
  trend: GoalTrendPoint[];
  blocker: { text: string; link: string; reason?: string } | null;
  min_n?: number;
  window_days?: number;
  reasons?: Array<{ reason: string; label: string; count: number }>;
  failing?: GoalServer[];
  disabled_by_mso?: GoalServer[];
  probing?: GoalServer[];
  disabled_text?: string | null;
}

export interface GoalsTrial {
  start: string; start_date: string; start_source: "setting" | "autopilot" | "setup" | "today"; start_warning: string | null;
  length_days: number; day: number; days_since_start: number; not_started: boolean; ended: boolean; end_date: string;
  timezone: string; today: string;
}

export interface Goals {
  available: boolean;
  trial: GoalsTrial;
  goals: Goal[];
  met: number;
  total: number;
  mode: string;
  snapshot_days: number;
  generated_at: string;
}
