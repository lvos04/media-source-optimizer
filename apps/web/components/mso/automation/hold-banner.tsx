"use client";

import { OctagonPause, PlayCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, LinkButton } from "@/components/mg/primitives";
import { useToast } from "@/components/mg/toast";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import { HOLD_CHANGED_EVENT, notifyHoldChanged, type HoldState, type SafetyStatus } from "@/lib/api/safety";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/**
 * Noodrem banner (1.6.0): shown app-wide while MSO has paused its OWN automatic writes after too many automatic
 * rollbacks or failed automatic applies (GET /automation/hold — one small query — polled every minute and refreshed
 * on {@link HOLD_CHANGED_EVENT}). "Hervatten" (operator+) → POST /automation/hold/resume, then the event, so
 * Automatisering → Veiligheid updates too. Renders nothing while loading, on an error or when there is no hold.
 */
export function AutopilotHoldBanner() {
  const { t, relative } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const state = useResource<HoldState>("/automation/hold", undefined, 60_000);
  const [pending, setPending] = useState(false);
  const { reload } = state;
  useEffect(() => {
    const onChange = () => void reload();
    window.addEventListener(HOLD_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(HOLD_CHANGED_EVENT, onChange);
  }, [reload]);
  const hold = state.data?.hold;
  if (!hold?.active) return null;
  const resume = async () => {
    setPending(true);
    try {
      const next = await api.post<SafetyStatus>("/automation/hold/resume");
      state.setData(() => ({ mode: next.mode, hold: next.hold }));
      notifyHoldChanged();
      toast.push("success", t("safety.hold.resumed"));
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
      void reload();
    } finally {
      setPending(false);
    }
  };
  const title = hold.mode === "autopilot" ? t("safety.hold.titleAutopilot") : t("safety.hold.titleManage");
  const reason = hold.reason ? hold.reason.charAt(0).toUpperCase() + hold.reason.slice(1) : "";
  return (
    <div className="banner banner--danger" role="status" data-testid="hold-banner">
      <OctagonPause className="ico" aria-hidden="true" />
      <span className="grow">
        <strong>{title}</strong> {t("safety.hold.body", { reason })}{" "}
        <span className="t-3">{t("safety.hold.since", { when: relative(hold.since) })}</span>
      </span>
      <span className="banner-actions">
        <LinkButton size="sm" href="/automatisering?tab=veiligheid">{t("safety.hold.review")}</LinkButton>
        <span title={can("operator") ? undefined : t("safety.hold.operatorOnly")}>
          <Button size="sm" icon={PlayCircle} pending={pending} disabled={!can("operator")} onClick={() => void resume()}>{t("safety.hold.resume")}</Button>
        </span>
      </span>
    </div>
  );
}
