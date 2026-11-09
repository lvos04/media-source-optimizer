"use client";

import {
  ArrowRight, Bot, CalendarDays, CircleCheck, CircleX, Eye, Hand, History, Info, type LucideIcon, Power, RefreshCw, RotateCcw, Search, Server,
  SlidersHorizontal, Sparkles, Trash2, Undo2, Workflow, Wrench,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { DangerDialog } from "@/components/mg/domain";
import { Button, Card, Dot, LinkButton, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { useToast } from "@/components/mg/toast";
import { RiskChip } from "@/components/mso/risk-chip";
import { useWaiting } from "@/components/shell/waiting";
import { api, errorMessage } from "@/lib/api/client";
import { NOT_ACTION_CATEGORIES, type AutopilotAction, type CalmAutopilotSummary, type CalmSyncs, type WaitingInbox } from "@/lib/api/calm";
import { useResource } from "@/lib/api/hooks";
import { localizeAuditSummary, type AutopilotCategory, type AutopilotCategoryKey } from "@/lib/api/insights";
import { useI18n } from "@/lib/i18n";
import { useMode, useWriteGate } from "@/lib/mode";

const ENDPOINT = "GET /api/v1/overview/autopilot";
const WAIT_ENDPOINT = "GET /api/v1/overview/waiting";
const ICON: Record<AutopilotCategoryKey, LucideIcon> = {
  applied: SlidersHorizontal, switched_off: Power, rolled_back: Undo2, servers: Server, retries: RotateCcw, remedies: Wrench, searches: Search,
  prepared: Hand, evaluated: Sparkles, syncs: RefreshCw, failed: CircleX, other: Workflow,
};
const ACTIONS_SHOWN = 5;

function ModeLine({ s }: { s: CalmAutopilotSummary }) {
  const { t } = useI18n();
  const { mode, paused } = useMode();
  const m = mode ?? s.mode;
  const p = mode === null ? s.paused : paused;
  if (m === "autopilot" && !p) return <StatusChip tone="accent" icon={Bot}>{t("autopilot.modeOn")}</StatusChip>;
  if (m === "autopilot") return <StatusChip tone="warning">{t("autopilot.modePaused")}</StatusChip>;
  if (m === "manage") return <StatusChip tone="neutral">{p ? t("autopilot.modeManagePaused") : t("autopilot.modeManage")}</StatusChip>;
  return <StatusChip tone="neutral" icon={Eye}>{t("autopilot.modeReadOnly")}</StatusChip>;
}

/** All synchronisations as ONE status line, linking to Systeem → Automatische taken. */
function SyncLine({ syncs }: { syncs: CalmSyncs }) {
  const { t, num } = useI18n();
  const failing = syncs.failing_now.length;
  const text = failing ? t("calm.syncFailing", { count: num(failing) })
    : syncs.state === "idle" ? t("calm.syncIdle")
      : syncs.failed ? t("calm.syncOkRecovered", { count: num(syncs.failed) }) : t("calm.syncOk");
  return (
    <Link href={syncs.link} className="row t-2" style={{ gap: 6, fontSize: 12.5 }} title={failing ? syncs.failing_now.join(", ") : t("calm.syncLinkTitle")} data-testid="sync-line">
      <Dot tone={failing ? "danger" : syncs.state === "idle" ? "neutral" : "success"} />{text}{failing ? <ArrowRight className="ico ico-sm" aria-hidden="true" /> : null}
    </Link>
  );
}

/** Per category one compact chip (counts at a glance), without the bookkeeping categories. */
function CategoryChips({ categories }: { categories: AutopilotCategory[] }) {
  const { num } = useI18n();
  const shown = categories.filter((c) => !NOT_ACTION_CATEGORIES.includes(c.key));
  if (!shown.length) return null;
  return (
    <div className="row wrap" style={{ gap: 6, padding: "4px 16px 10px" }}>
      {shown.map((c) => (
        <Link key={c.key} href={c.link} data-testid={`autopilot-cat-${c.key}`} title={c.examples[0] ? localizeAuditSummary(c.examples[0].summary) ?? undefined : undefined}>
          <StatusChip tone={c.tone} icon={ICON[c.key] ?? Workflow}>{c.label}: {num(c.count)}</StatusChip>
        </Link>
      ))}
    </div>
  );
}

/** One change MSO made: Wat · Waarom · Effect, with an inline "Terugdraaien" when it can be undone. */
function ActionRow({ a, onRollback }: { a: AutopilotAction; onRollback: (a: AutopilotAction) => void }) {
  const { t, relative } = useI18n();
  const gate = useWriteGate("operator");
  const Icon = ICON[a.category] ?? Workflow;
  const tone = a.category === "rolled_back" ? "list-icon--warning" : a.category === "switched_off" ? "list-icon--warning" : "list-icon--success";
  return (
    <div className="list-row list-row--wrap" data-testid="autopilot-action">
      <span className={`list-icon ${tone}`}><Icon className="ico" aria-hidden="true" /></span>
      <div className="list-main" style={{ minWidth: 0 }}>
        <div className="list-title" style={{ whiteSpace: "normal" }}>{a.what} <span className="t-3" style={{ fontWeight: 400, fontSize: 12 }}>· {a.target_label} · {relative(a.ts)}</span></div>
        {a.why ? <div className="list-sub" style={{ whiteSpace: "normal" }}><strong>{t("calm.why")}:</strong> {a.why}</div> : null}
        <div className="list-sub" style={{ whiteSpace: "normal", display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginTop: 2 }}>
          <strong>{t("calm.effect")}:</strong>
          {a.effect.label ? <StatusChip tone={a.effect.tone} icon={null}>{a.effect.label}</StatusChip> : null}
          <span>{a.effect.text ?? t("calm.effectNone")}</span>
        </div>
      </div>
      <div className="list-aside">
        {a.rollback.possible ? (
          <Button size="sm" variant="danger" icon={Undo2} disabled={!gate.allowed} title={gate.reason} onClick={() => onRollback(a)} data-testid="action-rollback">{t("calm.rollback")}</Button>
        ) : null}
        <LinkButton size="sm" href={a.link}>{t("calm.details")}</LinkButton>
      </div>
    </div>
  );
}

/** Overzicht → "Wat MSO deed": real actions in the last 24 h / 7 days (GET /overview/autopilot?days=1|7). */
export function AutopilotCard({ className = "span-12" }: { className?: string }) {
  const { t, num, relative } = useI18n();
  const toast = useToast();
  const [days, setDays] = useState<"1" | "7">("1");
  const res = useResource<CalmAutopilotSummary>("/overview/autopilot", { days }, 60_000);
  const [confirm, setConfirm] = useState<AutopilotAction | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rollback = async () => {
    if (!confirm) return;
    setPending(true);
    setError(null);
    try {
      await api.post(`/changes/${encodeURIComponent(String(confirm.change_id))}/rollback`);
      toast.push("success", t("change.rolledBack"), confirm.what);
      setConfirm(null);
      await res.reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  };
  return (
    <Card className={className} title={t("autopilot.didTitle")} sub={t("autopilot.didSub")} icon={Bot} flush
      actions={<Segmented label={t("autopilot.period")} value={days} onChange={setDays} options={[
        { value: "1", label: t("autopilot.last24h") }, { value: "7", label: t("autopilot.last7d") },
      ]} />}>
      <ResourceView resource={res} endpoint={ENDPOINT} compact>
        {(s) => (
          <>
            <div className="row wrap" style={{ gap: 10, padding: "12px 16px 4px" }}>
              <ModeLine s={s} />
              <span className="t-2" style={{ fontSize: 12.5 }} data-testid="actions-total">{t("calm.actionsTotal", { count: num(s.actions_total) })}</span>
              <SyncLine syncs={s.syncs} />
            </div>
            {s.actions_total === 0 ? (
              <StateView kind="empty" compact title={t("autopilot.nothingTitle")}
                body={s.mode === "read_only" ? t("autopilot.nothingReadOnly") : s.paused ? t("autopilot.nothingPaused") : t("autopilot.nothingBody")} />
            ) : (
              <>
                <CategoryChips categories={s.categories} />
                {s.actions.length ? (
                  <div className="list" aria-label={t("calm.changedTitle")}>
                    {s.actions.slice(0, ACTIONS_SHOWN).map((a) => <ActionRow key={a.change_id} a={a} onRollback={(x) => { setError(null); setConfirm(x); }} />)}
                  </div>
                ) : null}
                {s.actions.length > ACTIONS_SHOWN ? (
                  <div style={{ padding: "8px 16px 0" }}>
                    <Link className="link-more" href="/automatisering?tab=samenvatting">{t("calm.moreActions", { count: num(s.actions.length - ACTIONS_SHOWN) })} <ArrowRight className="ico ico-sm" aria-hidden="true" /></Link>
                  </div>
                ) : null}
              </>
            )}
            <div className="row-between wrap" style={{ padding: "8px 16px 12px", gap: 8 }}>
              <span className="t-3" style={{ fontSize: 12 }}>{t("autopilot.updated", { age: relative(s.generated_at) })}</span>
              <Link className="link-more" href="/automatisering?tab=samenvatting">{t("autopilot.digestLink")} <ArrowRight className="ico ico-sm" aria-hidden="true" /></Link>
            </div>
          </>
        )}
      </ResourceView>
      <DangerDialog open={confirm !== null} onClose={() => setConfirm(null)} onConfirm={() => void rollback()} icon={Undo2}
        title={t("calm.rollbackTitle")} description={t("calm.rollbackBody", { target: confirm?.target_label ?? "" })}
        facts={confirm ? [{ label: t("change.setting"), value: confirm.what }, ...(confirm.why ? [{ label: t("calm.why"), value: confirm.why }] : [])] : undefined}
        confirmLabel={t("calm.rollback")} pending={pending} error={error} />
    </Card>
  );
}

function WaitingRows({ w }: { w: WaitingInbox }) {
  const { t, num, relative } = useI18n();
  return (
    <div className="list">
      {w.items.slice(0, 8).map((i) => (
        <div className="list-row list-row--wrap" key={String(i.id)} data-testid="waiting-item">
          <span className={`list-icon ${i.deletes ? "list-icon--danger" : "list-icon--warning"}`}>
            {i.deletes ? <Trash2 className="ico" aria-hidden="true" /> : <Hand className="ico" aria-hidden="true" />}
          </span>
          <div className="list-main" style={{ minWidth: 0 }}>
            <div className="list-title" style={{ whiteSpace: "normal" }}>{i.title}</div>
            <div className="list-sub" style={{ whiteSpace: "normal" }}>{i.reason} · {relative(i.created_at)}{i.decisions > 1 ? ` · ${t("calm.waitDecisionsInGroup", { count: num(i.decisions) })}` : ""}</div>
            {i.group && i.examples.length ? (
              <div className="list-sub" style={{ whiteSpace: "normal" }}>{i.examples.join(" · ")}{i.decisions > i.examples.length ? ` · ${t("calm.waitGroupMore", { count: num(i.decisions - i.examples.length) })}` : ""}</div>
            ) : null}
          </div>
          <div className="list-aside">
            <RiskChip risk={i.risk} />
            <LinkButton size="sm" href={i.link}>{t("autopilot.view")}</LinkButton>
          </div>
        </div>
      ))}
      {w.count > 8 ? (
        <div style={{ padding: "8px 16px 12px" }}>
          <Link className="link-more" href="/automatisering?tab=wijzigingen">{t("autopilot.waitMore", { count: num(w.count - 8) })} <ArrowRight className="ico ico-sm" aria-hidden="true" /></Link>
        </div>
      ) : null}
    </div>
  );
}

function InfoRows({ w }: { w: WaitingInbox }) {
  const { t, relative } = useI18n();
  if (!w.info.length) return null;
  return (
    <div style={{ borderTop: "1px solid var(--mg-border-soft)" }}>
      <div className="eyebrow" style={{ padding: "10px 16px 0" }}>{t("calm.infoTitle")}</div>
      <div className="list">
        {w.info.slice(0, 3).map((i) => (
          <Link className="list-row list-row--wrap list-row--hover" key={i.id} href={i.link} data-testid="waiting-info">
            <span className="list-icon list-icon--info"><Info className="ico" aria-hidden="true" /></span>
            <div className="list-main" style={{ minWidth: 0 }}>
              <div className="list-title" style={{ whiteSpace: "normal" }}>{i.title}</div>
              <div className="list-sub">{t("calm.infoNoAction")} · {relative(i.created_at)}</div>
            </div>
            <ArrowRight className="ico ico-sm t-3" aria-hidden="true" />
          </Link>
        ))}
      </div>
    </div>
  );
}

/** Overzicht → "Wacht op jou" (first on the page): one row per decision, or one calm line when nothing waits.
 *  Same source as the topbar counter (GET /overview/waiting via the shell). */
export function WaitingCard({ className = "span-12" }: { className?: string }) {
  const { t, num } = useI18n();
  const shared = useWaiting();
  const own = useResource<WaitingInbox>(shared ? null : "/overview/waiting", undefined, 60_000);
  const res = shared ?? own;
  const w = res.data;
  if (w && w.decisions === 0) {
    return (
      <section className={`card ${className}`} id="wacht-op-jou" aria-label={t("autopilot.waitTitle")} data-testid="waiting-none">
        <div className="row wrap" style={{ gap: 10, padding: "12px 16px" }}>
          <span className="list-icon list-icon--success"><CircleCheck className="ico" aria-hidden="true" /></span>
          <div style={{ minWidth: 0, flex: "1 1 240px" }}>
            <strong>{t("calm.waitNothing")}</strong>
            <div className="t-3" style={{ fontSize: 12.5 }}>{t("calm.waitNothingBody")}</div>
          </div>
          {w.info_count ? <Link className="link-more" href={w.info[0]?.link ?? "/automatisering?tab=aanbevelingen"}><Info className="ico ico-sm" aria-hidden="true" />{t("calm.infoCount", { count: num(w.info_count) })}</Link> : null}
        </div>
      </section>
    );
  }
  return (
    <Card className={className} id="wacht-op-jou" title={t("autopilot.waitTitle")} sub={t("autopilot.waitSub")} icon={Hand} flush
      actions={w?.decisions ? <StatusChip tone="warning">{t("calm.waitDecisions", { count: num(w.decisions) })}</StatusChip> : undefined}>
      <ResourceView resource={res} endpoint={WAIT_ENDPOINT} compact>
        {(data) => <><WaitingRows w={data} /><InfoRows w={data} /></>}
      </ResourceView>
    </Card>
  );
}

/** Automatisering → "Samenvatting": weekly digest grouped by local day (GET /overview/autopilot?days=7). */
export function WeeklyDigest() {
  const { t, num, date, dateTime } = useI18n();
  const res = useResource<CalmAutopilotSummary>("/overview/autopilot", { days: "7" }, 120_000);
  return (
    <>
      <Card className="span-12" title={t("autopilot.digestTitle")} sub={t("autopilot.digestSub")} icon={CalendarDays} flush>
        <ResourceView resource={res} endpoint={ENDPOINT} compact>
          {(s) => (
            <>
              <div className="row wrap" style={{ gap: 10, padding: "12px 16px 4px" }}>
                <ModeLine s={s} />
                <span className="t-2" style={{ fontSize: 12.5 }}>{t("calm.actionsTotalWeek", { count: num(s.actions_total) })}</span>
                <SyncLine syncs={s.syncs} />
                {s.waiting.decisions ? <Link href="/#wacht-op-jou"><StatusChip tone="warning" icon={Hand}>{t("autopilot.waitingChip", { count: num(s.waiting.decisions) })}</StatusChip></Link> : null}
              </div>
              {s.actions_total === 0
                ? <StateView kind="empty" compact title={t("autopilot.nothingWeek")} body={s.mode === "read_only" ? t("autopilot.nothingReadOnly") : t("autopilot.nothingBody")} />
                : <CategoryChips categories={s.categories} />}
            </>
          )}
        </ResourceView>
      </Card>
      {res.data ? res.data.by_day.map((d) => {
        const cats = res.data!.categories.filter((c) => d.categories[c.key] && !NOT_ACTION_CATEGORIES.includes(c.key));
        const actions = d.actions ?? 0;
        return (
          <Card key={d.date} className="span-6" title={date(d.date)} icon={History} flush
            actions={<StatusChip tone={actions ? "accent" : "neutral"} icon={null}>{t("calm.dayActions", { count: num(actions) })}</StatusChip>}>
            {actions === 0 ? <StateView kind="empty" compact title={t("autopilot.dayEmpty")} /> : (
              <div className="stack-sm" style={{ padding: "12px 16px" }}>
                <div className="row wrap" style={{ gap: 6 }}>
                  {cats.map((c) => {
                    const Icon = ICON[c.key] ?? Workflow;
                    return <StatusChip key={c.key} tone={c.tone} icon={Icon}>{c.label}: {num(d.categories[c.key] ?? 0)}</StatusChip>;
                  })}
                </div>
                {d.items.length ? (
                  <ul className="t-2" style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.55 }}>
                    {d.items.slice(0, 8).map((i) => (
                      <li key={i.id}><Link href={i.link}>{localizeAuditSummary(i.summary)}</Link> <span className="t-3">· {dateTime(i.ts)}</span></li>
                    ))}
                    {d.items.length > 8 ? <li className="t-3">{t("autopilot.moreItems", { count: num(d.items.length - 8) })}</li> : null}
                  </ul>
                ) : null}
              </div>
            )}
          </Card>
        );
      }) : null}
    </>
  );
}
