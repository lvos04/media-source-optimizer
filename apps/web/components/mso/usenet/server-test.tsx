"use client";

import { CalendarClock, PlugZap, Repeat } from "lucide-react";
import { useState } from "react";

import { Button, Progress, StatusChip, type Tone } from "@/components/mg/primitives";
import { useToast } from "@/components/mg/toast";
import { useWriteGate } from "@/components/mso/mode-gate";
import { useRatio } from "@/components/mso/usenet/shared";
import { api, errorMessage } from "@/lib/api/client";
import type { BlockInfo, ExpiryInfo, ServerTest, ServerTestResponse, ServerTestResult } from "@/lib/api/usenet";
import { useI18n } from "@/lib/i18n";

const TEST_TONE: Record<ServerTestResult, Tone> = {
  ok: "success", auth_failed: "danger", account_expired: "danger", access_denied: "danger", block_exhausted: "danger",
  rate_limited: "warning", unreachable: "danger", tls_error: "danger", unknown: "neutral", unsupported: "neutral",
};

/** "Testen": SABnzbd's own server test (read-only, every mode, operator+). A passing test for a server MSO switched
 *  off lets Autopilot switch it back on (or leaves a proposal). */
export function TestServerButton({ server, name, onDone }: { server: string; name: string; onDone: () => void | Promise<void> }) {
  const { t } = useI18n();
  const toast = useToast();
  const gate = useWriteGate("operator", false);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const res = await api.post<ServerTestResponse>(`/usenet/sab/servers/${encodeURIComponent(server)}/test`, {});
      const label = res.test.label ?? res.test.result;
      if (res.change?.auto_applied) toast.push("success", t("usenetserver.testOn", { name }));
      else if (res.change) toast.push("info", t("usenetserver.testProposal", { name }), res.change.auto_reason ?? undefined);
      else if (res.test.ok) toast.push("success", t("usenetserver.testOk", { name }));
      else toast.push(TEST_TONE[res.test.result] === "danger" ? "danger" : "info", t("usenetserver.testFail", { name, label }), res.test.message ?? undefined);
      await onDone();
    } catch (e) {
      toast.push("danger", errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Button size="sm" variant="ghost" icon={PlugZap} onClick={() => void run()} pending={busy} disabled={!gate.allowed}
      title={gate.reason ?? t("usenetserver.testTitle")}>{t("usenetserver.test")}</Button>
  );
}

/** Latest SABnzbd server test: chip + when; SABnzbd's answer as tooltip; flapping hint. */
export function ServerTestLine({ test, flapping = false }: { test: ServerTest | null | undefined; flapping?: boolean }) {
  const { t, relative } = useI18n();
  if (!test) return <span className="t-3" style={{ fontSize: 12 }}>{t("usenetserver.sabTestNone")}</span>;
  return (
    <span className="stack-sm" style={{ gap: 2 }}>
      <span className="row wrap" style={{ gap: 6 }}>
        <StatusChip tone={TEST_TONE[test.result] ?? "neutral"} title={test.message ?? undefined}>{test.label ?? test.result}</StatusChip>
        <span className="t-3" style={{ fontSize: 11.5 }}>{t("usenetserver.sabTest")} · {relative(test.at)}</span>
      </span>
      {flapping ? (
        <StatusChip tone="warning" icon={Repeat}>{t("usenetserver.flapping")}</StatusChip>
      ) : null}
    </span>
  );
}

/** SABnzbd's `expire_date` ("YYYY-MM-DD") → days left counted in local calendar days. */
export function expiryFromDate(isoDate: string | null | undefined): ExpiryInfo | null {
  if (!isoDate) return null;
  const [y, m, d] = isoDate.split("-").map(Number);
  if (!y || !m || !d) return null;
  const now = new Date();
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return { expire_date: isoDate, days_left: Math.round((Date.UTC(y, m - 1, d) - today) / 86_400_000) };
}

/** Account expiry from SABnzbd: chip that turns warning ≤ 14 days, danger ≤ 1 day or past. */
export function ExpiryNote({ expiry }: { expiry: ExpiryInfo | null | undefined }) {
  const { t, date, relative } = useI18n();
  if (!expiry) return null;
  const d = expiry.days_left;
  const tone: Tone = d <= 1 ? "danger" : d <= 14 ? "warning" : "neutral";
  const when = d < 0 ? t("usenetserver.expiredOn", { date: date(expiry.expire_date) })
    : d === 0 ? t("usenetserver.expiresToday") : t("usenetserver.expiresIn", { when: relative(`${expiry.expire_date}T00:00:00`) });
  return <StatusChip tone={tone} icon={CalendarClock} title={date(expiry.expire_date)}>{when}</StatusChip>;
}

/** Block remaining with run-out forecast. */
export function BlockMeter({ block }: { block: BlockInfo }) {
  const { t, bytes, date, num } = useI18n();
  const ratio = useRatio();
  const pct = block.used_ratio === null ? null : block.used_ratio * 100;
  return (
    <div className="meter-row">
      <span>{t("usenetserver.block")}</span>
      <span className="t-2">
        {block.remaining_bytes === null ? t("usenetserver.blockUnknown")
          : t("usenetserver.blockLeft", { left: bytes(block.remaining_bytes), size: bytes(block.size_bytes), pct: ratio(block.used_ratio) })}
      </span>
      {pct !== null ? (
        <Progress value={pct} tone={block.low || pct > 90 ? "danger" : pct > 70 ? "warning" : undefined} label={t("usenetserver.block")} thin />
      ) : null}
      {block.remaining_bytes !== null ? (
        <span className="t-3" style={{ fontSize: 12, whiteSpace: "normal" }}>
          {block.days_left !== null && block.run_out_date
            ? t("usenetserver.blockForecast", { days: num(Math.floor(block.days_left)), date: date(block.run_out_date) })
            : t("usenetserver.blockNoForecast")}
        </span>
      ) : null}
      <span className="t-3" style={{ fontSize: 11.5, whiteSpace: "normal" }}>
        {block.source === "sabnzbd" ? t("usenetserver.blockSourceSab")
          : block.baseline_at ? t("usenetserver.blockSourceMso", { date: date(block.baseline_at) }) : t("usenetserver.blockSourceLegacy")}
      </span>
    </div>
  );
}
