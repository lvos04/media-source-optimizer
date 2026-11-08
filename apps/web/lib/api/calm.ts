// 1.6.0 WP-G1 "Rust in de app" — contracts for GET /overview/waiting, the 1.6.0 additions to GET /overview/autopilot
// and Automatisering → Voetafdruk (GET/POST /automation/footprint…). Extends the 1.5.0 types in ./insights.
import type { Tone } from "@/components/mg/primitives";
import type { AutopilotCategoryKey, AutopilotItem, AutopilotSummary, WaitingItem } from "@/lib/api/insights";

// ------------------------------------------------------------------ wacht op jou  GET /overview/waiting
export interface InboxItem extends WaitingItem {
  /** Real decisions behind this row: the malware bundle is 1, a group of 3 recommendations is 3. */
  decisions: number;
  group: boolean;
  examples: string[];
  change_ids: number[];
  new_since_yesterday: number | null;
  bundle?: boolean;
  change_title?: string;
}
export interface InboxInfo { id: string; title: string; target: string; created_at: string; link: string }
export interface WaitingInbox {
  /** Rows (as in 1.5.0). */
  count: number;
  /** The number shown everywhere: real decisions waiting for a person. */
  decisions: number;
  items: InboxItem[];
  /** Calm notes (e.g. "je hebt dit zelf teruggezet"); never a decision. */
  info: InboxInfo[];
  info_count: number;
}

// ------------------------------------------------------------------ wat MSO deed  GET /overview/autopilot
export interface ActionEffect {
  state: "measured" | "pending" | "not_measured" | "rolled_back" | "none";
  result: string | null; label: string | null; text: string | null; tone: Tone; due_at: string | null;
}
export interface AutopilotAction {
  change_id: number; ts: string; category: AutopilotCategoryKey; link: string;
  what: string; target: string; target_label: string; why: string | null; effect: ActionEffect; status: string;
  rollback: { possible: boolean; reason: string | null };
}
export interface CalmSyncs {
  jobs: Array<{ kind: string; done: number; failed: number }>; failed: number;
  runs: number; failing_now: string[]; state: "ok" | "failing" | "idle"; link: string;
}
export interface CalmDay {
  date: string; total: number; actions: number; categories: Partial<Record<AutopilotCategoryKey, number>>; items: AutopilotItem[];
}
export interface CalmAutopilotSummary extends Omit<AutopilotSummary, "waiting" | "syncs" | "by_day"> {
  /** Real actions only (no synchronisations, no evaluations); `total` keeps the 1.5.0 meaning. */
  actions_total: number;
  actions: AutopilotAction[];
  timezone: string;
  syncs: CalmSyncs;
  by_day: CalmDay[];
  waiting: WaitingInbox;
}
/** Categories that are bookkeeping, not an action on your systems (shown as one status line / per action). */
export const NOT_ACTION_CATEGORIES: AutopilotCategoryKey[] = ["syncs", "evaluated"];

// ------------------------------------------------------------------ voetafdruk  /automation/footprint
export type FootprintStatus = "actief" | "gewijzigd" | "onbereikbaar" | "verdwenen";
export const FOOTPRINT_STATUSES: FootprintStatus[] = ["actief", "gewijzigd", "onbereikbaar", "verdwenen"];
export interface FootprintEntry {
  target: string; path: string; label: string; change_id: number | null; change_ids: number[];
  applied_at: string | null; applied_by: string | null; automatic: boolean;
  original_text: string; mso_text: string; current_text: string;
  status: FootprintStatus; status_label: string; detail: string | null; checked_at: string; status_since: string;
  override: { until: string; detected_at: string } | null;
}
export interface FootprintSystem { target: string; label: string; entries: FootprintEntry[]; counts: Record<FootprintStatus, number> }
export interface FootprintOverride {
  target: string; target_label: string; path: string; label: string; mso_text: string; current_text: string; change_id: number | null;
  detected_at: string; until: string;
}
export interface IrreversibleItem { key: string; label: string; count: number }
export interface FootprintView {
  last_run: { at: string; checked: number } | null;
  systems: FootprintSystem[];
  totals: Record<FootprintStatus, number>;
  overrides: FootprintOverride[];
  override_days: number;
  irreversible: IrreversibleItem[];
  mode: string;
  restore_allowed: boolean;
}
interface PlanBase {
  target: string; target_label: string; path: string; label: string; change_id: number | null; change_ids: number[];
  original_text: string; mso_text: string; current_text: string | null;
}
export interface RestoreItem extends PlanBase { after_text: string; removes: boolean; disables: boolean; needs_approval: boolean }
export interface SkippedItem extends PlanBase { reason_code: "onbereikbaar" | "verdwenen" | "gewijzigd" | "al_terug" | "beleid"; reason: string }
export interface RestorePlan {
  restore: RestoreItem[]; skipped: SkippedItem[]; irreversible: IrreversibleItem[]; fingerprint: string;
  override_days: number; mode: string; allowed: boolean;
}
export interface RestoreResult {
  results: Array<{ target: string; target_label: string; status: "teruggezet" | "wacht_op_goedkeuring" | "mislukt"; change_id: number | null; count: number; message: string | null }>;
  skipped: SkippedItem[]; irreversible: IrreversibleItem[]; override_days: number; footprint: FootprintView;
}
