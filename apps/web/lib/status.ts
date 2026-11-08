// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/lib/status.ts — internally versioned copy, see PROVENANCE.md
// (Replaced the library-specific mappings with MSO status → tone/label helpers.)
import type { Tone } from "@/components/mg/primitives";
import type { ChangeStatus, JobStatus } from "@/lib/api/contracts";
import type { MessageKey } from "@/lib/i18n";

/** Generic health/connection status (`healthy|degraded|offline` plus common connector variants). */
export function healthTone(status: string | null | undefined): Tone {
  switch ((status ?? "").toLowerCase()) {
    case "healthy": case "ok": case "online": case "connected": return "success";
    case "degraded": case "warning": case "slow": return "warning";
    case "offline": case "error": case "failed": case "unreachable": case "auth_failed": return "danger";
    default: return "neutral";
  }
}

export function healthLabel(status: string | null | undefined): MessageKey {
  switch (healthTone(status)) {
    case "success": return "status.healthy";
    case "warning": return "status.degraded";
    case "danger": return "status.offline";
    default:
      return status === "unconfigured" || status === "not_configured" ? "status.notConfigured" : status === "disabled" ? "status.disabled" : "status.unknown";
  }
}

/** Job statuses as stored by `mso.jobs`: queued · running · done · failed · cancelled (`succeeded` kept as alias). */
export const JOB_TONE: Record<string, Tone> = {
  queued: "neutral", running: "accent", done: "success", succeeded: "success", failed: "danger", cancelled: "neutral", retry_scheduled: "warning",
};
const JOB_LABEL: Record<string, MessageKey> = {
  queued: "status.queued", running: "status.running", done: "status.succeeded", succeeded: "status.succeeded",
  failed: "status.failed", cancelled: "status.cancelled", retry_scheduled: "status.retry_scheduled",
};
export function jobTone(status: JobStatus): Tone { return JOB_TONE[status] ?? "neutral"; }
export function jobLabel(status: JobStatus): MessageKey { return JOB_LABEL[status] ?? "status.unknown"; }
/** Terminal job states (polling can stop). */
export function jobFinished(status: JobStatus): boolean { return status === "done" || status === "succeeded" || status === "failed" || status === "cancelled"; }

export const CHANGE_STATUS: Record<ChangeStatus, { tone: Tone; label: MessageKey }> = {
  preview: { tone: "info", label: "change.status.preview" },
  approved: { tone: "accent", label: "change.status.approved" },
  applied: { tone: "success", label: "change.status.applied" },
  rolled_back: { tone: "neutral", label: "change.status.rolled_back" },
  failed: { tone: "danger", label: "change.status.failed" },
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
