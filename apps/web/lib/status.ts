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

export const JOB_TONE: Record<string, Tone> = {
  queued: "neutral", running: "accent", succeeded: "success", failed: "danger", cancelled: "neutral", retry_scheduled: "warning",
};
export function jobTone(status: JobStatus): Tone { return JOB_TONE[status] ?? "neutral"; }
export function jobLabel(status: JobStatus): MessageKey {
  return (["queued", "running", "succeeded", "failed", "cancelled", "retry_scheduled"].includes(status) ? `status.${status}` : "status.unknown") as MessageKey;
}

export const CHANGE_STATUS: Record<ChangeStatus, { tone: Tone; label: MessageKey }> = {
  preview: { tone: "info", label: "change.status.preview" },
  approved: { tone: "accent", label: "change.status.approved" },
  applied: { tone: "success", label: "change.status.applied" },
  rolled_back: { tone: "neutral", label: "change.status.rolled_back" },
  failed: { tone: "danger", label: "change.status.failed" },
};
