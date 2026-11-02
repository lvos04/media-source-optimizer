"use client";

import { CircleHelp, Gauge, KeyRound, PlugZap, ShieldAlert, Timer, Unplug } from "lucide-react";

import { StatusChip, type Tone } from "@/components/mg/primitives";
import type { ConfidenceLevel, ProviderStatus } from "@/lib/api/usenet";
import { useI18n } from "@/lib/i18n";
import type { MessageKey } from "@/lib/i18n/messages";

const STATUS_TONE: Record<ProviderStatus, Tone> = {
  ok: "success", auth_failed: "danger", account_expired: "danger", rate_limited: "warning", block_exhausted: "danger",
  unreachable: "danger", tls_error: "danger", timeout: "warning", error: "warning", unknown: "neutral",
};
const STATUS_ICON: Partial<Record<ProviderStatus, typeof KeyRound>> = {
  auth_failed: KeyRound, account_expired: KeyRound, rate_limited: Gauge, block_exhausted: Gauge,
  unreachable: Unplug, tls_error: ShieldAlert, timeout: Timer, unknown: CircleHelp,
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

const CONF_TONE: Record<ConfidenceLevel, Tone> = { high: "success", medium: "info", low: "warning", none: "neutral" };

export function ConfidenceChip({ level }: { level: ConfidenceLevel }) {
  const { t } = useI18n();
  const l = (level in CONF_TONE ? level : "none") as ConfidenceLevel;
  return <StatusChip tone={CONF_TONE[l]} icon={l === "none" ? PlugZap : undefined}>{t(`usenet.conf.${l}` as MessageKey)}</StatusChip>;
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
