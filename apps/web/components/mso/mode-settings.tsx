"use client";

import { Bot, Check, Eye, Lock, ShieldCheck, ShieldX } from "lucide-react";
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
 * PUT /system/mode (admin). Three choices: Alleen-lezen · Beheermodus · Autopilot. Switching to Beheermodus or Autopilot
 * asks for confirmation (Autopilot lists exactly what it does and never does; the backend also lifts a pause);
 * switching to Alleen-lezen is immediate.
 * Also shows the per-area "automatisch veilig toepassen" state (GET /automation/status) with a link to Automatisering.
 */
export function ModeSettingsCard() {
  const { t } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const { mode, paused, loading, error, setMode } = useMode();
  const automation = useResource<AutomationStatus>("/automation/status", undefined, 60_000);
  const { reload: reloadAutomation } = automation;
  const [confirm, setConfirm] = useState<OperatingMode | null>(null);
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
    if (!admin || mode === null || pending) return;
    setPending(true);
    setFailure(null);
    try {
      await setMode(next);
      toast.push("success", next === "autopilot" ? t("mode.section.switchedAutopilot") : next === "manage" ? t("mode.section.switchedManage") : t("mode.section.switchedReadOnly"));
      setConfirm(null);
    } catch (e) {
      setFailure(errorMessage(e));
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setPending(false);
    }
  };
  const choose = (next: OperatingMode | "") => {
    if (!next || !admin || mode === null || next === mode || pending) return;
    if (next === "read_only") void apply("read_only");
    else { setFailure(null); setConfirm(next); }
  };

  const current = automation.error ? null : automation.data;
  const areas = current?.areas ?? [];
  const autoOn = areas.filter((a) => a.auto_apply_safe);
  const isAuto = current?.autopilot === true && current.automation_allowed === true;
  const disabledReason = !admin ? t("mode.section.adminOnly") : mode === null ? (error ? t("mode.unknownReason") : t("mode.loadingReason")) : undefined;
  const opt = (value: OperatingMode, Icon: typeof Eye, label: string) => ({
    value, label: <><Icon className="ico ico-sm" aria-hidden="true" /> {label}</>, disabled: Boolean(disabledReason) || pending,
  });
  const cap = current?.max_per_hour;
  const age = current?.max_age_h;

  return (
    <div id="modus" ref={ref} style={{ scrollMarginTop: 72 }}>
      <Card title={t("mode.section.title")} sub={t("mode.section.sub")} icon={ShieldCheck}>
        <div className="stack">
          <div className="row wrap" style={{ gap: 8 }}>
            <span className="t-2">{t("mode.section.current")}:</span>
            {mode === "autopilot" ? <StatusChip tone="accent" icon={Bot}>{t("mode.autopilot")}</StatusChip>
              : mode === "manage" ? <StatusChip tone="neutral" icon={ShieldCheck}>{t("mode.manage")}</StatusChip>
              : mode === "read_only" ? <StatusChip tone="info" icon={Eye}>{t("mode.read_only")}</StatusChip>
                : <StatusChip tone={loading ? "neutral" : "unknown"} pending={loading}>{loading ? t("mode.loadingReason") : t("mode.unknown")}</StatusChip>}
            {mode !== null ? (
              paused ? <StatusChip tone="paused">{t("mode.section.pausedChip")}</StatusChip>
                : mode === "read_only" ? <StatusChip tone="neutral" icon={Lock}>{t("mode.section.blockedChip")}</StatusChip>
                  : current?.automation_allowed === false ? <StatusChip tone="paused">{t("mode.section.stopped")}</StatusChip>
                    : current?.mode === mode && current.automation_allowed === true ? <StatusChip tone="success">{t("mode.section.runningChip")}</StatusChip>
                      : <StatusChip tone="unknown">{t("status.unknown")}</StatusChip>
            ) : null}
          </div>
          <span title={disabledReason} style={{ alignSelf: "flex-start" }}>
            <Segmented label={t("mode.section.choose")} value={mode ?? ""} onChange={choose}
              options={[
                opt("read_only", Eye, t("mode.read_only")),
                opt("manage", ShieldCheck, t("mode.manage")),
                opt("autopilot", Bot, t("mode.autopilot")),
              ]} />
          </span>
          {!admin ? <InlineState tone="neutral">{t("mode.section.adminOnly")}</InlineState> : null}
          {failure && confirm === null ? <InlineState tone="danger" role="alert">{failure}</InlineState> : null}
          <dl className="kv">
            <dt><strong>{t("mode.section.readOnlyTitle")}</strong></dt><dd>{t("mode.section.readOnlyBody")}</dd>
            <dt><strong>{t("mode.section.manageTitle")}</strong></dt><dd>{t("mode.section.manageBody")}</dd>
            <dt><strong>{t("mode.section.autopilotTitle")}</strong></dt><dd>{t("mode.section.autopilotBody")}</dd>
          </dl>
          <AutopilotExplainer cap={cap} age={age} />

          <div className="stack-sm" data-testid="mode-auto-apply">
            <div className="row-between wrap">
              <strong><Bot className="ico ico-sm" aria-hidden="true" /> {t("mode.section.autoTitle")}</strong>
              <Link href="/automatisering?tab=automatisch" className="link-more">{t("mode.section.autoLink")}</Link>
            </div>
            {current ? (
              <>
                <p className="hint" style={{ margin: 0 }}>{isAuto ? t("mode.section.autoHintAutopilot") : t("mode.section.autoHint")}</p>
                <div className="row wrap" style={{ gap: 6 }}>
                  {areas.map((a) => {
                    if (current.automation_allowed === false || a.effective === false) {
                      return <StatusChip key={a.area} tone="neutral">{a.label}: {t("automation.off")}</StatusChip>;
                    }
                    if (current.automation_allowed !== true || a.effective !== true || typeof a.via_autopilot !== "boolean") {
                      return <StatusChip key={a.area} tone="unknown">{a.label}: {t("status.unknown")}</StatusChip>;
                    }
                    return a.via_autopilot
                      ? <StatusChip key={a.area} tone="accent" icon={Bot}>{a.label}: {t("mode.section.autoViaAutopilot")}</StatusChip>
                      : <StatusChip key={a.area} tone="warning">{a.label}: {t("automation.on")}</StatusChip>;
                  })}
                </div>
              </>
            ) : automation.error ? <InlineState tone="unknown">{t("mode.section.autoUnavailable")}</InlineState> : null}
          </div>
        </div>
      </Card>

      <Modal open={confirm === "manage"} onClose={() => setConfirm(null)} icon={ShieldCheck} title={t("mode.section.confirmTitle")} description={t("mode.section.confirmBody")}
        footer={<><Button onClick={() => setConfirm(null)} data-autofocus>{t("action.cancel")}</Button><Button variant="primary" pending={pending} disabled={Boolean(disabledReason)} title={disabledReason} onClick={() => void apply("manage")} data-testid="mode-manage-confirm">{t("mode.section.confirm")}</Button></>}>
        <div className="stack-sm">
          {current ? (
            autoOn.length
              ? <InlineState tone="warning">{t("mode.section.confirmAuto", { areas: autoOn.map((a) => a.label).join(", ") })}</InlineState>
              : <InlineState tone="info">{t("mode.section.confirmAutoNone")}</InlineState>
          ) : null}
          {paused ? <InlineState tone="info">{t("mode.section.confirmPaused")}</InlineState> : null}
          {failure ? <InlineState tone="danger" role="alert">{failure}</InlineState> : null}
        </div>
      </Modal>

      <Modal open={confirm === "autopilot"} onClose={() => setConfirm(null)} icon={Bot} title={t("mode.section.confirmAutopilotTitle")} description={t("mode.section.confirmAutopilotBody")}
        footer={<><Button onClick={() => setConfirm(null)} data-autofocus>{t("action.cancel")}</Button><Button variant="primary" icon={Bot} pending={pending} disabled={Boolean(disabledReason)} title={disabledReason} onClick={() => void apply("autopilot")} data-testid="mode-autopilot-confirm">{t("mode.section.confirmAutopilot")}</Button></>}>
        <div className="stack-sm">
          <AutopilotExplainer cap={cap} age={age} compact />
          {paused ? <InlineState tone="info">{t("mode.section.confirmAutopilotUnpause")}</InlineState> : null}
          {failure ? <InlineState tone="danger" role="alert">{failure}</InlineState> : null}
        </div>
      </Modal>
    </div>
  );
}

const DOES = ["mode.section.autopilotDoes1", "mode.section.autopilotDoes2", "mode.section.autopilotDoes3", "mode.section.autopilotDoes4", "mode.section.autopilotDoes5", "mode.section.autopilotDoes6"] as const;
const NEVER = ["mode.section.autopilotNever1", "mode.section.autopilotNever2"] as const;

/** What Autopilot does automatically and what it never does (settings card + confirmation dialog). */
export function AutopilotExplainer({ cap, age, compact = false }: { cap?: number; age?: number; compact?: boolean }) {
  const { t } = useI18n();
  return (
    <div className={compact ? "stack-sm" : "grid"} style={compact ? undefined : { gap: 12 }} data-testid="autopilot-explainer">
      <div className={compact ? "stack-sm" : "span-6 stack-sm"}>
        <strong><Check className="ico ico-sm" aria-hidden="true" /> {t("mode.section.autopilotDoesTitle")}</strong>
        <ul className="t-2" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.5 }}>
          {DOES.map((k) => <li key={k}>{t(k)}</li>)}
        </ul>
      </div>
      <div className={compact ? "stack-sm" : "span-6 stack-sm"}>
        <strong><ShieldX className="ico ico-sm" aria-hidden="true" /> {t("mode.section.autopilotNeverTitle")}</strong>
        <ul className="t-2" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.5 }}>
          {NEVER.map((k) => <li key={k}>{t(k)}</li>)}
        </ul>
        {cap !== undefined && age !== undefined ? <p className="hint" style={{ margin: 0 }}>{t("mode.section.autopilotLimits", { cap, age })}</p> : null}
      </div>
    </div>
  );
}
