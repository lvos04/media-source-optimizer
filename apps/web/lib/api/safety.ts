// 1.6.0 WP-E1 — core safety of changes and Autopilot. Contracts for:
//   GET  /api/v1/automation/safety          (viewer)   noodrem, afkoeling, onderbroken wijzigingen
//   GET  /api/v1/automation/hold            (viewer)   alleen de noodremstand (de app-brede banner)
//   POST /api/v1/automation/hold/resume     (operator) Hervatten na de noodrem (idempotent)
//   POST /api/v1/automation/failures/reset  (operator) Opnieuw automatisch proberen
//   GET  /api/v1/automation/undo?window=24h|7d (viewer)   Autopilot terugdraaien: voorbeeld
//   POST /api/v1/automation/undo            (operator) Autopilot terugdraaien: uitvoeren (niet in alleen-lezen)

export interface AutopilotHold {
  active: boolean; since: string; reason: string; mode: string;
  rollbacks: number | null; failures: number | null; rec_id: number | null;
}

export interface CooldownItem {
  key: string; title: string; target: string; area: string | null; change_id: number | null;
  failures: number; parked: boolean; last_error: string | null; last_failed_at: string | null;
  next_attempt_at: string; reason: string;
}

export interface SafetyStatus {
  mode: string; paused: boolean;
  hold: AutopilotHold | null;
  brake: { since: string; rollbacks: number; failures: number; max_rollbacks: number; max_failures: number };
  cooldowns: CooldownItem[];
  applying: Array<{ id: number; target: string; title: string; apply_started_at: string | null; applied_auto: boolean }>;
  undo: Record<UndoWindow, number>;
}

/** GET /automation/hold — the banner polls only this (one small query on every page). */
export interface HoldState { mode: string; hold: AutopilotHold | null }

/** Fired after "Hervatten" anywhere, so the banner and Automatisering → Veiligheid refresh together. */
export const HOLD_CHANGED_EVENT = "mso:hold-changed";

export function notifyHoldChanged() {
  window.dispatchEvent(new Event(HOLD_CHANGED_EVENT));
}

export type UndoWindow = "24h" | "7d";

/** ``switches_on``: MSO switched it off, undoing switches it on again. ``switches_off``: MSO switched it on (e.g.
 * autoheal re-enabled a server that tested fine), undoing switches a working thing off again. */
export interface UndoOp { label: string | null; path: string; now: unknown; back_to: unknown; switches_on: boolean; switches_off: boolean }
export interface UndoItem { id: number; title: string; target: string; applied_at: string; area: string | null; ops: UndoOp[]; reason?: string }
export interface UndoPreview { window: UndoWindow; label: string; items: UndoItem[]; not_undoable: UndoItem[]; override_days: number }

export type UndoResultKind = "teruggedraaid" | "overgeslagen" | "mislukt";
export interface UndoResult {
  window: UndoWindow;
  counts: Record<UndoResultKind, number>;
  results: Array<{ id: number; title: string; result: UndoResultKind; detail: string | null }>;
}
