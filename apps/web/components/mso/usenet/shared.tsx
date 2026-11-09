"use client";

import { Ban, CircleHelp, Gauge, KeyRound, PowerOff, ShieldAlert, Timer, Unplug, UserRoundPen } from "lucide-react";

import { StatusChip, type Tone } from "@/components/mg/primitives";
import type { ConfidenceLevel, ProviderStatus, SabServerStatus } from "@/lib/api/usenet";
import { useI18n, type MessageKey } from "@/lib/i18n";

// provider checks are real measurements: "ok" is measured health (Gezond)
const STATUS_TONE: Record<ProviderStatus, Tone> = {
  ok: "healthy", auth_failed: "danger", account_expired: "danger", rate_limited: "warning", block_exhausted: "danger",
  unreachable: "danger", tls_error: "danger", timeout: "warning", error: "warning", unknown: "unknown",
  missing_credentials: "warning", access_denied: "danger", sab_blocked: "warning", disabled: "neutral",
  budget_exhausted: "warning",
};
const STATUS_ICON: Partial<Record<ProviderStatus, typeof KeyRound>> = {
  auth_failed: KeyRound, account_expired: KeyRound, rate_limited: Gauge, block_exhausted: Gauge,
  unreachable: Unplug, tls_error: ShieldAlert, timeout: Timer, unknown: CircleHelp,
  missing_credentials: UserRoundPen, access_denied: Ban, sab_blocked: Ban, disabled: PowerOff, budget_exhausted: Gauge,
};

/** Provider status: colour + icon + text; detail (provider's own message) as tooltip. */
export function ProviderStatusChip({ status, detail }: { status: ProviderStatus; detail?: string | null }) {
  const { t } = useI18n();
  const s = (status in STATUS_TONE ? status : "error") as ProviderStatus;
  return (
    <StatusChip tone={STATUS_TONE[s]} icon={STATUS_ICON[s]} title={detail ?? undefined}>
      {t(`usenet.status.${s}` as MessageKey)}
    </StatusChip>
  );
}

// Peil ZekerheidLabel (T-24): certainty is ink with 1–3 dots, never a health colour; no data = Onbekend.
const CONF_DOTS: Record<ConfidenceLevel, number> = { high: 3, medium: 2, low: 1, none: 0 };

export function ConfidenceChip({ level }: { level: ConfidenceLevel }) {
  const { t } = useI18n();
  const l = (level in CONF_DOTS ? level : "none") as ConfidenceLevel;
  if (l === "none") return <StatusChip tone="unknown">{t(`usenet.conf.${l}` as MessageKey)}</StatusChip>;
  return (
    <span className="chip zekerheid" data-as="zekerheid">
      <span className="zekerheid-dots" aria-hidden="true">{[1, 2, 3].map((i) => <i key={i} data-on={i <= CONF_DOTS[l] || undefined} />)}</span>
      {t(`usenet.conf.${l}` as MessageKey)}
    </span>
  );
}

/** Ratio 0..1 → "97,5%" (i18n pct expects a percentage value). */
export function useRatio() {
  const { pct } = useI18n();
  return (ratio: number | null | undefined, digits = 1) => (ratio === null || ratio === undefined ? "—" : pct(ratio * 100, digits));
}

/** Read a browser File as base64 (without the data: prefix). */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("Bestand kon niet worden gelezen"));
    reader.onload = () => {
      const result = String(reader.result ?? "");
      resolve(result.includes(",") ? result.slice(result.indexOf(",") + 1) : result);
    };
    reader.readAsDataURL(file);
  });
}

/** SABnzbd's own status for one of its servers: chip + Dutch explanation + SABnzbd's message + "bron: SABnzbd". */
export function SabStatusCell({ st, compact = false }: { st: SabServerStatus | null; compact?: boolean }) {
  const { t, relative } = useI18n();
  if (!st) return <span className="t-3" style={{ fontSize: 12 }}>{t("usenet.prio.statusUnknown")}</span>;
  const failing = STATUS_TONE[st.status in STATUS_TONE ? st.status : "error"] === "danger";
  return (
    <div className="stack-sm" style={{ gap: 2 }}>
      <span className="row wrap" style={{ gap: 6 }}>
        <ProviderStatusChip status={st.status} detail={st.message} />
        <span className="t-3" style={{ fontSize: 11.5 }}>{t("usenet.prio.source")}</span>
      </span>
      {!compact && (failing || st.status !== "ok") ? (
        <span className="t-2" style={{ fontSize: 12, whiteSpace: "normal" }}>{st.explanation}</span>
      ) : null}
      {st.message ? (
        <span className="t-3 mono" style={{ fontSize: 11.5, wordBreak: "break-word", whiteSpace: "normal" }}>
          {st.message}{st.message_at ? ` · ${relative(st.message_at)}` : ""}
        </span>
      ) : null}
    </div>
  );
}
