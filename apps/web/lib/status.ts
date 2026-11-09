// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/lib/status.ts — internally versioned copy, see PROVENANCE.md
// (Replaced the library-specific mappings with MSO status → tone/label helpers.)
import {
  Check, Clock, FileDiff, Hourglass, LoaderCircle, OctagonX, RotateCcw, X, type LucideIcon, ClipboardCheck,
} from "lucide-react";

import type { Tone } from "@/components/mg/primitives";
import type { ChangeStatus, JobStatus } from "@/lib/api/contracts";
import type { MessageKey } from "@/lib/i18n";

/**
 * Generic health status (`healthy|degraded|offline` plus common connector variants) → Peil health axis.
 * Only measured health is `healthy` (green). "connected" is a link state, not health (Peil §4.3): `linked`.
 * not_configured → `unlinked` (Niet gekoppeld); disabled → neutral label without a status icon; anything else → `unknown`.
 */
export function healthTone(status: string | null | undefined): Tone {
  switch ((status ?? "").toLowerCase()) {
    case "healthy": case "ok": case "online": return "healthy";
    case "connected": return "linked";
    case "degraded": case "warning": case "slow": return "warning";
    case "offline": case "error": case "failed": case "unreachable": case "auth_failed": return "danger";
    case "unconfigured": case "not_configured": return "unlinked";
    case "disabled": return "neutral";
    default: return "unknown";
  }
}

export function healthLabel(status: string | null | undefined): MessageKey {
  switch (healthTone(status)) {
    case "healthy": return "status.healthy";
    case "linked": return "status.linked";
    case "warning": return "status.degraded";
    case "danger": return "status.offline";
    case "unlinked": return "status.notConfigured";
    default:
      return status === "disabled" ? "status.disabled" : "status.unknown";
  }
}

/**
 * Backend `message` texts that are only a status word (ADR-0006 punt 3): the UI shows the Peil word from the status
 * code instead and uses `message` only as a reason when it says more.
 */
export const BARE_STATUS_WORDS = new Set(["gezond", "verstoord", "offline", "onbekend", "healthy", "degraded", "ok", "storing", "beperkt"]);

/**
 * Backendteksten van de vorm "<naam>: <oud statuswoord>" (overview.health.issues, "Prowlarr: Verstoord") tonen het
 * Peil-woord (Beperkt/Storing). De API en de opgeslagen tekst blijven ongewijzigd (ADR-0006 punt 3); alleen de weergave.
 */
const LEGACY_STATUS: Record<string, MessageKey> = { verstoord: "status.degraded", offline: "status.offline" };
export function peilStatusText(text: string, t: (key: MessageKey) => string): string {
  const m = /^(.+?):\s*(Verstoord|Offline)\s*$/i.exec(text);
  return m ? `${m[1]}: ${t(LEGACY_STATUS[m[2].toLowerCase()])}` : text;
}

/** True when a component counts as a problem for the sidebar/overview (everything that is not measured healthy). */
export function isHealthProblem(status: string | null | undefined): boolean {
  const tone = healthTone(status);
  return tone !== "healthy" && tone !== "linked" && tone !== "neutral";
}

/**
 * Job statuses as stored by `mso.jobs`: queued · running · done · failed · cancelled (`succeeded` kept as alias).
 * Peil (PEIL_MIGRATIE §6.2): a finished job is neutral "Afgerond" (check), never green; failed = Storing.
 */
export const JOB_TONE: Record<string, Tone> = {
  queued: "neutral", running: "accent", done: "success", succeeded: "success", failed: "danger", cancelled: "neutral", retry_scheduled: "neutral",
};
export const JOB_ICON: Record<string, LucideIcon> = {
  queued: Clock, running: LoaderCircle, done: Check, succeeded: Check, failed: OctagonX, cancelled: X, retry_scheduled: RotateCcw,
};
const JOB_LABEL: Record<string, MessageKey> = {
  queued: "status.queued", running: "status.running", done: "status.succeeded", succeeded: "status.succeeded",
  failed: "status.failed", cancelled: "status.cancelled", retry_scheduled: "status.retry_scheduled",
};
export function jobTone(status: JobStatus): Tone { return JOB_TONE[status] ?? "unknown"; }
export function jobIcon(status: JobStatus): LucideIcon | undefined { return JOB_ICON[status]; }
export function jobLabel(status: JobStatus): MessageKey { return JOB_LABEL[status] ?? "status.unknown"; }
/** "Waiting for a condition" (schedule that does not run): hourglass, never the pause icon. */
export const WAITING_ICON = Hourglass;
/** Terminal job states (polling can stop). */
export function jobFinished(status: JobStatus): boolean { return status === "done" || status === "succeeded" || status === "failed" || status === "cancelled"; }

/** Change-set status → Peil: preview = info (file-diff), approved = approval (violet), applied = neutral (check). */
export const CHANGE_STATUS: Record<ChangeStatus, { tone: Tone; label: MessageKey; icon: LucideIcon }> = {
  preview: { tone: "info", label: "change.status.preview", icon: FileDiff },
  approved: { tone: "approval", label: "change.status.approved", icon: ClipboardCheck },
  applying: { tone: "accent", label: "change.status.applying", icon: LoaderCircle },
  applied: { tone: "success", label: "change.status.applied", icon: Check },
  rolled_back: { tone: "neutral", label: "change.status.rolled_back", icon: RotateCcw },
  failed: { tone: "danger", label: "change.status.failed", icon: OctagonX },
};

/**
 * Dutch label for a backend enum value via `${prefix}.${value}`; falls back to the raw value when no
 * translation exists (never hides information, but never shows a raw key either).
 */
export function enumLabel(t: (key: MessageKey) => string, prefix: string, value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const key = `${prefix}.${value}` as MessageKey;
  const label = t(key);
  return label === key ? value : label;
}

/** Dutch names for background job kinds (the technical kind stays visible next to it). */
export const JOB_KIND_LABEL: Record<string, string> = {
  "automation.apply_safe": "Automatisch veilig toepassen",
  "automation.evaluate": "Wijzigingen evalueren",
  "changes.reconcile": "Onderbroken wijzigingen afronden",
  "backup.create": "Back-up maken",
  "backup.restore": "Back-up terugzetten",
  "costs.renewal_reminders": "Verlengingsherinneringen",
  "indexers.health": "Indexer-gezondheid",
  "indexers.reliability": "Indexer-betrouwbaarheid",
  "mediaguard.sync": "MediaGuard synchroniseren",
  "prowlarr.sync": "Prowlarr synchroniseren",
  "recovery.analyze": "Ontbrekende items analyseren",
  "system.retention": "Onderhoud (opruimen)",
  "torrent.cross_seed_scan": "Cross-seed zoeken",
  "torrent.ingest": "Torrents inlezen",
  "torrent.storage_scan": "Opslag scannen",
  "torrent.tune": "Torrent-tuning",
  "torrent.tune_evaluate": "Torrent-tuning evalueren",
  "usenet.article_test": "Artikeltest",
  "usenet.headerindex_purge": "Headerindex opschonen",
  "usenet.headerindex_run": "Headerindex bijwerken",
  "usenet.provider_checks": "Providers controleren",
  "usenet.sab_ingest": "SABnzbd-historie inlezen",
  "usenet.sab_server_status": "SABnzbd-serverstatus",
  "usenet.sab_stats": "SABnzbd-statistieken",
};
export function jobKindLabel(kind: string): string { return JOB_KIND_LABEL[kind] ?? kind; }

/** Evaluation verdict of an applied change (automation). */
export const EVALUATION_LABEL: Record<string, string> = {
  keep: "Behouden", rollback: "Terugdraaien", rolled_back: "Teruggedraaid", unknown: "Onbeslist", niet_beschikbaar: "Niet beschikbaar",
};
