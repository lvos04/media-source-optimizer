"use client";

// 1.6.0 WP-G1 (C35) — Automatisering → Voetafdruk: per system what MSO changed (origineel → MSO → nu), the owner's own
// choices that MSO respects, what cannot be put back, and "Alles terugzetten" (list first, then a DangerDialog).

import { Ban, CircleCheck, Footprints, Info, RefreshCw, Undo2, UserCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, InlineState, Modal, PageHeader, StatusChip, type Tone } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { api, errorMessage } from "@/lib/api/client";
import {
  FOOTPRINT_STATUSES, type FootprintEntry, type FootprintStatus, type FootprintSystem, type FootprintView, type RestorePlan, type RestoreResult,
} from "@/lib/api/calm";
import { useResource } from "@/lib/api/hooks";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { useWriteGate } from "@/lib/mode";
import { useSession } from "@/lib/session";

const ENDPOINT = "GET /api/v1/automation/footprint";
const STATUS_TONE: Record<FootprintStatus, Tone> = { actief: "success", gewijzigd: "warning", onbereikbaar: "danger", verdwenen: "neutral" };

function StatusCell({ e }: { e: FootprintEntry }) {
  const { t, date } = useI18n();
  return (
    <div className="stack-sm" style={{ gap: 4 }}>
      <StatusChip tone={STATUS_TONE[e.status]} title={e.detail ?? undefined}>{t(`calm.fp.status.${e.status}` as MessageKey)}</StatusChip>
      {e.override ? <span className="t-3">{t("calm.fp.overrideUntil", { date: date(e.override.until) })}</span> : null}
      {e.status === "onbereikbaar" && e.detail ? <span className="t-3" style={{ overflowWrap: "anywhere" }}>{e.detail}</span> : null}
    </div>
  );
}

function SystemCard({ s }: { s: FootprintSystem }) {
  const { t, num, relative } = useI18n();
  return (
    <Card className="span-12" title={s.label} icon={Footprints} flush
      actions={<div className="row wrap" style={{ gap: 6 }}>{FOOTPRINT_STATUSES.filter((k) => s.counts[k]).map((k) => (
        <StatusChip key={k} tone={STATUS_TONE[k]} icon={null}>{t(`calm.fp.count.${k}` as MessageKey, { count: num(s.counts[k]) })}</StatusChip>
      ))}</div>}>
      <div className="table-wrap" tabIndex={0} role="region" aria-label={`${s.label}: ${t("calm.fp.title")}`}>
        <table className="table table--compact table--stack">
          <thead>
            <tr>
              <th>{t("calm.fp.col.setting")}</th><th>{t("calm.fp.col.original")}</th><th>{t("calm.fp.col.mso")}</th>
              <th>{t("calm.fp.col.now")}</th><th>{t("calm.fp.col.status")}</th><th>{t("calm.fp.col.change")}</th>
            </tr>
          </thead>
          <tbody>
            {s.entries.map((e) => (
              <tr key={e.path} data-testid="footprint-row" data-status={e.status} data-inactive={e.status === "verdwenen" ? "true" : undefined}>
                <td className="cell-primary"><span style={{ overflowWrap: "anywhere" }}>{e.label}</span><span className="change-ops-path">{e.path}</span></td>
                <td data-label={t("calm.fp.col.original")}>{e.original_text}</td>
                <td data-label={t("calm.fp.col.mso")}>{e.mso_text}</td>
                <td data-label={t("calm.fp.col.now")}>{e.current_text} <span className="t-3">· {relative(e.checked_at)}</span></td>
                <td data-label={t("calm.fp.col.status")} className="cell-block"><StatusCell e={e} /></td>
                <td data-label={t("calm.fp.col.change")}>
                  {e.change_id ? <Link href={`/automatisering?tab=wijzigingen&change=${e.change_id}`}>#{e.change_id}</Link> : "—"}
                  <span className="t-3" style={{ display: "block" }}>{e.automatic ? t("calm.fp.byAutopilot") : t("calm.fp.byUser", { user: e.applied_by ?? "—" })}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function OwnChoices({ v }: { v: FootprintView }) {
  const { t, date } = useI18n();
  if (!v.overrides.length) return null;
  return (
    <Card className="span-6" title={t("calm.fp.ownTitle")} sub={t("calm.fp.ownSub")} icon={UserCheck} flush>
      <div className="list">
        {v.overrides.map((o) => (
          <div className="list-row list-row--wrap" key={`${o.target}:${o.path}`} data-testid="footprint-override">
            <span className="list-icon list-icon--info"><UserCheck className="ico" aria-hidden="true" /></span>
            <div className="list-main" style={{ minWidth: 0 }}>
              <div className="list-title" style={{ whiteSpace: "normal" }}>{o.target_label} · {o.label}<span className="change-ops-path">{o.path}</span></div>
              <div className="list-sub" style={{ whiteSpace: "normal" }}>{t("calm.fp.ownRow", { mso: o.mso_text, current: o.current_text, date: date(o.until) })}</div>
            </div>
          </div>
        ))}
      </div>
      <p className="hint" style={{ margin: 0, padding: "8px 16px 12px" }}>{t("calm.fp.ownSetting", { days: v.override_days })}</p>
    </Card>
  );
}

function Irreversible({ items, className = "span-6" }: { items: FootprintView["irreversible"]; className?: string }) {
  const { t, num } = useI18n();
  return (
    <Card className={className} title={t("calm.fp.irrevTitle")} sub={t("calm.fp.irrevSub")} icon={Ban} flush>
      {items.length === 0 ? <StateView kind="empty" compact title={t("calm.fp.irrevEmpty")} /> : (
        <div className="list">
          {items.map((i) => (
            <div className="list-row" key={i.key} data-testid="footprint-irreversible">
              <div className="list-main"><div className="list-title" style={{ whiteSpace: "normal" }}>{i.label}</div></div>
              <strong className="num">{num(i.count)}</strong>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function PlanLists({ plan }: { plan: RestorePlan }) {
  const { t } = useI18n();
  // an admin's confirmation in the dialog is the approval itself (applied at once); an operator only prepares it
  const isAdmin = useSession().can("admin");
  return (
    <div className="stack-sm" style={{ marginTop: 12 }}>
      {plan.restore.length ? (
        <div>
          <div className="eyebrow">{t("calm.fp.restoreItems")}</div>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18, lineHeight: 1.55 }} data-testid="restore-list">
            {plan.restore.map((r) => (
              <li key={`${r.target}:${r.path}`}>
                <strong>{r.target_label}</strong> · {r.label}: {t("calm.fp.restoreItem", { now: r.current_text ?? "—", back: r.after_text })}
                {r.needs_approval ? <span className="t-3"> ({t(isAdmin ? "calm.fp.needsApprovalAdmin" : "calm.fp.needsApproval")})</span> : r.disables ? <span className="t-3"> ({t("calm.fp.disables")})</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {plan.skipped.length ? (
        <div>
          <div className="eyebrow">{t("calm.fp.skipItems")}</div>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18, lineHeight: 1.55 }} data-testid="skip-list">
            {plan.skipped.map((s) => <li key={`${s.target}:${s.path}`}><strong>{s.target_label}</strong> · {s.label}: {s.reason}</li>)}
          </ul>
        </div>
      ) : null}
      {plan.irreversible.length ? (
        <div>
          <div className="eyebrow">{t("calm.fp.irrevTitle")}</div>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18, lineHeight: 1.55 }}>
            {plan.irreversible.map((i) => <li key={i.key}>{i.count}× {i.label}</li>)}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/** "Alles terugzetten": fetch the live list (GET …/restore), show it, then POST exactly that list (fingerprint). */
function RestoreAll({ onDone }: { onDone: (v: FootprintView) => void }) {
  const { t, num } = useI18n();
  const gate = useWriteGate("operator");
  const toast = useToast();
  const [plan, setPlan] = useState<RestorePlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = async () => {
    setLoading(true);
    setError(null);
    try {
      setPlan(await api.get<RestorePlan>("/automation/footprint/restore"));
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setLoading(false);
    }
  };
  const confirm = async () => {
    if (!plan) return;
    setPending(true);
    setError(null);
    try {
      const out = await api.post<RestoreResult>("/automation/footprint/restore", { fingerprint: plan.fingerprint });
      const lines = out.results.map((r) => t(`calm.fp.result.${r.status}` as MessageKey, { system: r.target_label, count: num(r.count), message: r.message ?? "" }));
      toast.push(out.results.some((r) => r.status === "mislukt") ? "danger" : "success", t("calm.fp.done"), lines.join(" · "));
      setPlan(null);
      onDone(out.footprint);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  };
  const close = () => { setPlan(null); setError(null); };
  return (
    <>
      <Button variant="danger" icon={Undo2} pending={loading} disabled={!gate.allowed} title={gate.reason} onClick={() => void open()} data-testid="restore-all">{t("calm.fp.restore")}</Button>
      {plan && plan.restore.length > 0 ? (
        <DangerDialog open onClose={close} onConfirm={() => void confirm()} icon={Undo2} title={t("calm.fp.restoreTitle")}
          description={t("calm.fp.restoreBody", { days: plan.override_days })}
          impact={[{ label: t("calm.fp.restoreCount"), value: num(plan.restore.length) }, { label: t("calm.fp.skipCount"), value: num(plan.skipped.length) }]}
          confirmLabel={t("calm.fp.confirm")} pending={pending} error={error}>
          <PlanLists plan={plan} />
        </DangerDialog>
      ) : null}
      {plan && plan.restore.length === 0 ? (
        <Modal open onClose={close} icon={Info} title={t("calm.fp.nothingTitle")} description={t("calm.fp.nothingBody")}
          footer={<Button onClick={close} data-autofocus>{t("calm.fp.close")}</Button>}>
          <PlanLists plan={plan} />
        </Modal>
      ) : null}
    </>
  );
}

/** The whole page body (GET /automation/footprint). */
export function FootprintPage() {
  const { t, num, relative } = useI18n();
  const toast = useToast();
  const res = useResource<FootprintView>("/automation/footprint", undefined, 120_000);
  const refreshGate = useWriteGate("operator", false);
  const [refreshing, setRefreshing] = useState(false);
  const refresh = async () => {
    setRefreshing(true);
    try {
      const v = await api.post<FootprintView>("/automation/footprint/refresh");
      res.setData(() => v);
      toast.push("success", t("calm.fp.refreshed"));
    } catch (e) {
      toast.push("danger", t("toast.failed", { message: errorMessage(e) }));
    } finally {
      setRefreshing(false);
    }
  };
  const v = res.data;
  return (
    <>
      <PageHeader title={t("calm.fp.title")} sub={t("calm.fp.sub")}
        crumbs={[{ label: t("calm.fp.crumb"), href: "/automatisering" }, { label: t("calm.fp.title") }]}
        actions={<>
          <Button icon={RefreshCw} pending={refreshing} disabled={!refreshGate.allowed} title={refreshGate.reason} onClick={() => void refresh()} data-testid="footprint-refresh">{t("calm.fp.refresh")}</Button>
          <RestoreAll onDone={(nv) => res.setData(() => nv)} />
        </>} />
      <div className="grid">
        <Card className="span-12" title={t("calm.fp.summary")} icon={CircleCheck} flush>
          <ResourceView resource={res} endpoint={ENDPOINT} compact>
            {(data) => (
              <div className="stack-sm" style={{ padding: "12px 16px" }}>
                <div className="row wrap" style={{ gap: 6 }}>
                  {FOOTPRINT_STATUSES.map((k) => <StatusChip key={k} tone={data.totals[k] ? STATUS_TONE[k] : "neutral"} icon={null}>{t(`calm.fp.count.${k}` as MessageKey, { count: num(data.totals[k]) })}</StatusChip>)}
                </div>
                <span className="t-3">{data.last_run?.at ? t("calm.fp.lastRun", { age: relative(data.last_run.at) }) : t("calm.fp.neverRun")}</span>
                <InlineState tone="info" icon={Info}>{t("calm.fp.exitHint")}</InlineState>
              </div>
            )}
          </ResourceView>
        </Card>
        {v ? (
          <>
            {v.systems.length === 0 ? (
              <Card className="span-12"><StateView kind="empty" compact title={t("calm.fp.emptyTitle")} body={t("calm.fp.emptyBody")} /></Card>
            ) : v.systems.map((s) => <SystemCard key={s.target} s={s} />)}
            <OwnChoices v={v} />
            <Irreversible items={v.irreversible} className={v.overrides.length ? "span-6" : "span-12"} />
          </>
        ) : null}
      </div>
    </>
  );
}
