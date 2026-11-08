"use client";

import { OctagonPause, PlayCircle } from "lucide-react";
import { useState } from "react";

import { Button, LinkButton } from "@/components/mg/primitives";
import { useToast } from "@/components/mg/toast";
import { api, errorMessage } from "@/lib/api/client";
import { useResource } from "@/lib/api/hooks";
import type { SafetyStatus } from "@/lib/api/safety";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/**
 * Noodrem banner (1.6.0): shown app-wide while MSO has paused its OWN automatic writes after too many automatic
 * rollbacks or failed automatic applies (GET /automation/safety, polled every minute). "Hervatten" (operator+)
 * → POST /automation/hold/resume. Renders nothing while loading, on an error or when there is no hold.
 */
export function AutopilotHoldBanner() {
  const { t, relative } = useI18n();
  const { can } = useSession();
  const toast = useToast();
  const safety = useResource<SafetyStatus>("/automation/safety", undefined, 60_000);
  const [pending, setPending] = useState(false);
  const hold = safety.data?.hold;
  if (!hold?.active) return null;
  const resume = async () => {
    setPending(true);
    try {
      const next = await api.post<SafetyStatus>("/automation/hold/resume");
      safety.setData(() => next);
      toast.push("success", t("safety.hold.resumed"));
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
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
