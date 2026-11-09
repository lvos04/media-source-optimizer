"use client";

import { Footprints, Play } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Button, Card, LinkButton, PageHeader, Tabs } from "@/components/mg/primitives";
import { AiCard } from "@/components/mso/automation/ai-card";
import { WeeklyDigest } from "@/components/mso/automation/autopilot-summary";
import { ChangesPanel } from "@/components/mso/automation/changes-panel";
import { EvaluationsPanel } from "@/components/mso/automation/evaluation-view";
import { RecommendationsList } from "@/components/mso/automation/recommendations-list";
import { SafetyPanel } from "@/components/mso/automation/safety-panel";
import { TogglesPanel } from "@/components/mso/automation/toggles-panel";
import { RiskChip } from "@/components/mso/risk-chip";
import { useAction } from "@/lib/api/actions";
import type { Risk } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import type { AutomationStatus } from "@/lib/api/insights";
import { useI18n } from "@/lib/i18n";
import { useMode } from "@/lib/mode";
import { useSession } from "@/lib/session";

const TABS = ["aanbevelingen", "wijzigingen", "controle", "veiligheid", "samenvatting", "automatisch", "ai"] as const;
type Tab = (typeof TABS)[number];
const RISKS: Risk[] = ["advies", "automatisch_veilig", "toestemming_nodig"];

/** Follows `?tab=` — also on a soft navigation to this same route (e.g. the noodrem banner's "Bekijken" while already
 * here), where the page stays mounted and a mount-only read would miss it. Own Suspense boundary for useSearchParams. */
function TabFromUrl({ onTab, onChange }: { onTab: (tab: Tab) => void; onChange: (id: number) => void }) {
  const params = useSearchParams();
  const q = params.get("tab");
  const change = Number(params.get("change"));
  useEffect(() => {
    if (q && (TABS as readonly string[]).includes(q)) onTab(q as Tab);
  }, [q, onTab]);
  // deep link ?tab=wijzigingen&change=<id> (Samenvatting, "Wacht op jou", "Wat MSO deed"), also while mounted
  useEffect(() => {
    if (Number.isInteger(change) && change > 0) onChange(change);
  }, [change, onChange]);
  return null;
}

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

  const select = (v: Tab) => {
    setTab(v);
    const url = new URL(window.location.href);
    url.searchParams.set("tab", v);
    url.searchParams.delete("change"); // switching tabs by hand: the deep-linked change no longer applies
    url.searchParams.delete("rec");
    window.history.replaceState(null, "", url.toString());
  };
  const s = status.data;
  const allowed = mode !== null ? (mode === "manage" || mode === "autopilot") && !paused : !!s?.automation_allowed;
  const runAllowed = can("operator") && allowed;
  const runReason = !can("operator") ? t("automation.operatorOnly") : !allowed ? t("automation.runReason") : undefined;

  return (
    <>
      <Suspense fallback={null}><TabFromUrl onTab={setTab} onChange={setFocusChange} /></Suspense>
      <PageHeader title={t("nav.automation")} sub={t("automation.sub")}
        actions={<><LinkButton href="/automatisering/voetafdruk" icon={Footprints}>{t("calm.fp.link")}</LinkButton><Button icon={Play} disabled={!runAllowed} title={runReason} pending={pending === "run"}
          onClick={async () => { await run("run", "post", "/automation/run", undefined, t("automation.runQueued")); void status.reload(); }}>{t("automation.runNow")}</Button></>} />

      <Card title={t("automation.riskTitle")}>
        <div className="grid" style={{ gap: 12 }}>
          {RISKS.map((r) => (
            <div className="span-4 stack-sm" key={r}>
              <RiskChip risk={r} size="lg" />
              <p className="t-2" style={{ margin: 0, fontSize: 12.5 }}><strong>{t(`risk.mso.${r}`)}.</strong> {s?.risk_explanation[r] ?? t(`risk.${r}Help`)}</p>
            </div>
          ))}
        </div>
      </Card>

      <div style={{ marginTop: 16 }}>
        <Tabs label={t("nav.automation")} value={tab} onSelect={select} items={[
          { value: "aanbevelingen", label: t("automation.tab.recommendations") },
          { value: "wijzigingen", label: t("automation.tab.changes") },
          { value: "controle", label: t("automation.tab.evaluations") },
          { value: "veiligheid", label: t("safety.tab") },
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
        {tab === "veiligheid" ? <SafetyPanel onOpenChange={(id) => { setFocusChange(id); select("wijzigingen"); }} /> : null}
        {tab === "automatisch" ? <TogglesPanel status={status} /> : null}
        {tab === "ai" ? <AiCard /> : null}
      </div>
    </>
  );
}
