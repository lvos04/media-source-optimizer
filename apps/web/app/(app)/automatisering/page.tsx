"use client";

import { Play } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, Card, PageHeader, Tabs } from "@/components/mg/primitives";
import { AiCard } from "@/components/mso/automation/ai-card";
import { WeeklyDigest } from "@/components/mso/automation/autopilot-summary";
import { ChangesPanel } from "@/components/mso/automation/changes-panel";
import { EvaluationsPanel } from "@/components/mso/automation/evaluation-view";
import { RecommendationsList } from "@/components/mso/automation/recommendations-list";
import { TogglesPanel } from "@/components/mso/automation/toggles-panel";
import { RiskChip } from "@/components/mso/risk-chip";
import { useAction } from "@/lib/api/actions";
import type { Risk } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { AutomationStatus } from "@/lib/api/insights";
import { useI18n } from "@/lib/i18n";
import { useMode } from "@/lib/mode";
import { useSession } from "@/lib/session";

const TABS = ["aanbevelingen", "wijzigingen", "controle", "samenvatting", "automatisch", "ai"] as const;
type Tab = (typeof TABS)[number];
const RISKS: Risk[] = ["advies", "automatisch_veilig", "toestemming_nodig"];

export default function AutomationPage() {
  const { t } = useI18n();
  const { can } = useSession();
  const [tab, setTab] = useState<Tab>("aanbevelingen");
  const [focusChange, setFocusChange] = useState<number | null>(null);
  const status = useResource<AutomationStatus>("/automation/status", undefined, 60_000);
  const { run, pending } = useAction();
  const { mode, paused } = useMode();
  const { reload: reloadStatus } = status;
  // pause/resume or a mode switch anywhere (topbar, banner, Instellingen) → refresh the automation status
  useEffect(() => { void reloadStatus(); }, [mode, paused, reloadStatus]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const q = params.get("tab");
    if (q && (TABS as readonly string[]).includes(q)) setTab(q as Tab);
    // deep link from "Wat MSO deed" / "Wacht op jou": ?tab=wijzigingen&change=<id>
    const change = Number(params.get("change"));
    if (Number.isInteger(change) && change > 0) setFocusChange(change);
  }, []);
  const select = (v: Tab) => {
    setTab(v);
    const url = new URL(window.location.href);
    url.searchParams.set("tab", v);
    window.history.replaceState(null, "", url.toString());
  };
  const s = status.data;
  const allowed = mode !== null ? (mode === "manage" || mode === "autopilot") && !paused : !!s?.automation_allowed;
  const runAllowed = can("operator") && allowed;
  const runReason = !can("operator") ? t("automation.operatorOnly") : !allowed ? t("automation.runReason") : undefined;

  return (
    <>
      <PageHeader title={t("nav.automation")} sub={t("automation.sub")}
        actions={<Button icon={Play} disabled={!runAllowed} title={runReason} pending={pending === "run"}
          onClick={async () => { await run("run", "post", "/automation/run", undefined, t("automation.runQueued")); void status.reload(); }}>{t("automation.runNow")}</Button>} />

      <Card title={t("automation.riskTitle")}>
        <div className="grid" style={{ gap: 12 }}>
          {RISKS.map((r) => (
            <div className="span-4 stack-sm" key={r}>
              <RiskChip risk={r} size="lg" />
              <p className="t-2" style={{ margin: 0, fontSize: 12.5 }}>{s?.risk_explanation[r] ?? t(`risk.${r}Help`)}</p>
            </div>
          ))}
        </div>
      </Card>

      <div style={{ marginTop: 16 }}>
        <Tabs label={t("nav.automation")} value={tab} onSelect={select} items={[
          { value: "aanbevelingen", label: t("automation.tab.recommendations") },
          { value: "wijzigingen", label: t("automation.tab.changes") },
          { value: "controle", label: t("automation.tab.evaluations") },
          { value: "samenvatting", label: t("automation.tab.summary") },
          { value: "automatisch", label: t("automation.tab.automation") },
          { value: "ai", label: t("automation.tab.ai") },
        ]} />
      </div>
      <div className="grid" style={{ marginTop: 16 }}>
        {tab === "samenvatting" ? <WeeklyDigest /> : null}
        {tab === "aanbevelingen" ? <RecommendationsList onOpenChange={(id) => { setFocusChange(id); select("wijzigingen"); }} /> : null}
        {tab === "wijzigingen" ? <ChangesPanel focusId={focusChange} /> : null}
        {tab === "controle" ? <EvaluationsPanel onOpenChange={(id) => { setFocusChange(id); select("wijzigingen"); }} /> : null}
        {tab === "automatisch" ? <TogglesPanel status={status} /> : null}
        {tab === "ai" ? <AiCard /> : null}
      </div>
    </>
  );
}
