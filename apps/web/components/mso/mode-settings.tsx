"use client";

import { Bot, Eye, PauseCircle, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button, Card, InlineState, Modal, Segmented, StatusChip } from "@/components/mg/primitives";
import { useToast } from "@/components/mg/toast";
import { errorMessage } from "@/lib/api/client";
import type { OperatingMode } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { AutomationStatus } from "@/lib/api/insights";
import { useI18n } from "@/lib/i18n";
import { useMode } from "@/lib/mode";
import { useSession } from "@/lib/session";

/**
 * Instellingen → Systeem → "Bedrijfsmodus" (anchor `#modus`, deep link from the topbar mode chip).
 * PUT /system/mode (admin). Switching to Beheermodus asks for confirmation; switching to Alleen-lezen is immediate.
 * Also shows the per-area "automatisch veilig toepassen" state (GET /automation/status) with a link to Automatisering.
 */
export function ModeSettingsCard() {
  const { t } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const { mode, paused, loading, error, setMode } = useMode();
  const automation = useResource<AutomationStatus>("/automation/status");
  const { reload: reloadAutomation } = automation;
  const [confirm, setConfirm] = useState(false);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const admin = can("admin");

  // deep link: /instellingen?tab=systeem#modus scrolls this card into view
  useEffect(() => {
    if (window.location.hash === "#modus") ref.current?.scrollIntoView({ block: "start" });
  }, []);
  // keep the auto-apply summary in step with the live mode/pause state
  useEffect(() => { void reloadAutomation(); }, [mode, paused, reloadAutomation]);

  const apply = async (next: OperatingMode) => {
    setPending(true);
    setFailure(null);
    try {
      await setMode(next);
      toast.push("success", next === "manage" ? t("mode.section.switchedManage") : t("mode.section.switchedReadOnly"));
      setConfirm(false);
    } catch (e) {
      setFailure(errorMessage(e));
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(false);
    }
  };
  const choose = (next: OperatingMode) => {
    if (next === mode || pending) return;
    if (next === "manage") { setFailure(null); setConfirm(true); } else void apply("read_only");
  };

  const areas = automation.data?.areas ?? [];
  const autoOn = areas.filter((a) => a.auto_apply_safe);
  const disabledReason = !admin ? t("mode.section.adminOnly") : mode === null ? (error ? t("mode.unknownReason") : t("mode.loadingReason")) : undefined;

  return (
    <div id="modus" ref={ref} style={{ scrollMarginTop: 72 }}>
      <Card title={t("mode.section.title")} sub={t("mode.section.sub")} icon={ShieldCheck}>
        <div className="stack">
          <div className="row wrap" style={{ gap: 8 }}>
            <span className="t-2" style={{ fontSize: 13 }}>{t("mode.section.current")}:</span>
            {mode === "manage" ? <StatusChip tone="warning" icon={ShieldCheck}>{t("mode.manage")}</StatusChip>
              : mode === "read_only" ? <StatusChip tone="info" icon={Eye}>{t("mode.read_only")}</StatusChip>
                : <StatusChip tone="neutral" pending={loading}>{loading ? t("mode.loadingReason") : t("mode.unknown")}</StatusChip>}
            {mode !== null ? (
              paused ? <StatusChip tone="warning" icon={PauseCircle}>{t("mode.section.pausedChip")}</StatusChip>
                : mode === "manage" ? <StatusChip tone="success">{t("mode.section.runningChip")}</StatusChip>
                  : <StatusChip tone="neutral">{t("mode.section.blockedChip")}</StatusChip>
            ) : null}
          </div>
          <span title={disabledReason} style={{ alignSelf: "flex-start" }}>
            <Segmented<OperatingMode> label={t("mode.section.choose")} value={mode ?? "read_only"} onChange={choose}
              options={[
                { value: "read_only", label: <><Eye className="ico ico-sm" aria-hidden="true" /> {t("mode.read_only")}</>, disabled: Boolean(disabledReason) || pending },
                { value: "manage", label: <><ShieldCheck className="ico ico-sm" aria-hidden="true" /> {t("mode.manage")}</>, disabled: Boolean(disabledReason) || pending },
              ]} />
          </span>
          {!admin ? <InlineState tone="neutral">{t("mode.section.adminOnly")}</InlineState> : null}
          {failure && !confirm ? <InlineState tone="danger" role="alert">{failure}</InlineState> : null}
          <dl className="kv">
            <dt><strong>{t("mode.section.readOnlyTitle")}</strong></dt><dd>{t("mode.section.readOnlyBody")}</dd>
            <dt><strong>{t("mode.section.manageTitle")}</strong></dt><dd>{t("mode.section.manageBody")}</dd>
          </dl>

          <div className="stack-sm" data-testid="mode-auto-apply">
            <div className="row-between wrap">
              <strong style={{ fontSize: 13 }}><Bot className="ico ico-sm" aria-hidden="true" /> {t("mode.section.autoTitle")}</strong>
              <Link href="/automatisering?tab=automatisch" className="link-more">{t("mode.section.autoLink")}</Link>
            </div>
            <p className="hint" style={{ margin: 0 }}>{t("mode.section.autoHint")}</p>
            {automation.data ? (
              <div className="row wrap" style={{ gap: 6 }}>
                {areas.map((a) => (
                  <StatusChip key={a.area} tone={a.auto_apply_safe ? (mode === "manage" && !paused ? "warning" : "neutral") : "neutral"} outline={!a.auto_apply_safe}>
                    {a.label}: {a.auto_apply_safe ? t("automation.on") : t("automation.off")}
                  </StatusChip>
                ))}
              </div>
            ) : automation.error ? <InlineState tone="warning">{t("mode.section.autoUnavailable")}</InlineState> : null}
          </div>
        </div>
      </Card>

      <Modal open={confirm} onClose={() => setConfirm(false)} icon={ShieldCheck} title={t("mode.section.confirmTitle")} description={t("mode.section.confirmBody")}
        footer={<><Button onClick={() => setConfirm(false)} data-autofocus>{t("action.cancel")}</Button><Button variant="primary" pending={pending} onClick={() => void apply("manage")} data-testid="mode-manage-confirm">{t("mode.section.confirm")}</Button></>}>
        <div className="stack-sm">
          {automation.data ? (
            autoOn.length
              ? <InlineState tone="warning">{t("mode.section.confirmAuto", { areas: autoOn.map((a) => a.label).join(", ") })}</InlineState>
              : <InlineState tone="info">{t("mode.section.confirmAutoNone")}</InlineState>
          ) : null}
          {paused ? <InlineState tone="info">{t("mode.section.confirmPaused")}</InlineState> : null}
          {failure ? <InlineState tone="danger" role="alert">{failure}</InlineState> : null}
        </div>
      </Modal>
    </div>
  );
}
