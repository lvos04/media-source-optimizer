// 1.6.0 WP-A: types for the stage-log facts, the reclassification and the retry efficacy/brake/coverage fields.
// Kept apart from ./usenet.ts; the components combine them with the existing types (intersection).
import type { SabHistoryItem, UsenetAutopilot } from "@/lib/api/usenet";

/** `facts` per failed download in `GET /usenet/sab/history` (from SABnzbd's stage_log). */
export interface StageFacts {
  data_complete: boolean | null;
  missing_articles: number | null;
  malformed_articles: number | null;
  aborted: boolean;
  repair: "gelukt" | "mislukt" | "niet_nodig" | null;
  blocks_short: number | null;
  unpack: "gelukt" | "mislukt" | null;
  unpack_line: string | null;
  problem: string | null;
  problem_label: string | null;
  environment: boolean;
  age_days: number | null;
  servers: Array<{ name: string; bytes: number }>;
  summary: string | null;
}
export type SabHistoryItemWithFacts = SabHistoryItem & { facts?: StageFacts | null };

/** `GET/POST /usenet/sab/reclassify` */
export interface ReclassifyResult {
  checked: number; changed: number; unchanged: number; not_in_sab: number; data_complete: number;
  server_cause_removed: number; server_cause_kept: number; archive_read: boolean; sab_failed_jobs: number;
  reasons_before: Record<string, number>; reasons_after: Record<string, number>; trigger: string; at: string;
}
export interface ReclassifyStatus { last: ReclassifyResult | null }

export interface RetryEfficacy {
  attempts: number; verified: number; failed: number; running: number; ratio: number | null; window_days: number;
  since: string; min_attempts: number; min_ratio: number;
}
export interface RetryBrake {
  paused: boolean; paused_at: string | null; reason: string | null; attempts: number | null; verified: number | null;
  ratio: number | null; resumed_at: string | null; resume_trigger: "user" | "coverage" | null;
}
export interface CoverageGain { at: string; server: string; name: string; kind: "recovered" | "new_server" }
export interface RetryDiag {
  efficacy?: RetryEfficacy;
  brake?: RetryBrake;
  coverage?: { last_gain: CoverageGain | null; description: string | null };
}
/** `GET /usenet/autopilot` with the 1.6.0 retry fields. */
export type UsenetAutopilotDiag = UsenetAutopilot & { retry: UsenetAutopilot["retry"] & RetryDiag };
