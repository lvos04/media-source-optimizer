"use client";

import {
  ArrowRight, Bot, CalendarDays, CircleX, Eye, Hand, History, type LucideIcon, Power, RefreshCw, RotateCcw, Search, Server,
  SlidersHorizontal, Sparkles, Trash2, Undo2, Workflow, Wrench,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Card, LinkButton, Segmented, StatusChip } from "@/components/mg/primitives";
import { ResourceView, StateView } from "@/components/mg/states";
import { RiskChip } from "@/components/mso/risk-chip";
import { useResource } from "@/lib/api/hooks";
import { localizeAuditSummary, type AutopilotCategory, type AutopilotCategoryKey, type AutopilotSummary } from "@/lib/api/insights";
import { useI18n } from "@/lib/i18n";
import { useMode } from "@/lib/mode";

const ENDPOINT = "GET /api/v1/overview/autopilot";
const ICON: Record<AutopilotCategoryKey, LucideIcon> = {
  applied: SlidersHorizontal, switched_off: Power, rolled_back: Undo2, servers: Server, retries: RotateCcw, remedies: Wrench, searches: Search,
  prepared: Hand, evaluated: Sparkles, syncs: RefreshCw, failed: CircleX, other: Workflow,
};
const ICON_TONE: Record<AutopilotCategory["tone"], string> = {
  success: "list-icon--success", warning: "list-icon--warning", info: "list-icon--info", danger: "list-icon--danger", neutral: "",
};

function ModeLine({ s }: { s: AutopilotSummary }) {
  const { t } = useI18n();
  const { mode, paused } = useMode();
  const m = mode ?? s.mode;
  const p = mode === null ? s.paused : paused;
  if (m === "autopilot" && !p) return <StatusChip tone="accent" icon={Bot}>{t("autopilot.modeOn")}</StatusChip>;
  if (m === "autopilot") return <StatusChip tone="warning">{t("autopilot.modePaused")}</StatusChip>;
  if (m === "manage") return <StatusChip tone="neutral">{p ? t("autopilot.modeManagePaused") : t("autopilot.modeManage")}</StatusChip>;
  return <StatusChip tone="neutral" icon={Eye}>{t("autopilot.modeReadOnly")}</StatusChip>;
}

function CategoryRows({ categories }: { categories: AutopilotCategory[] }) {
  const { num } = useI18n();
  return (
    <div className="list">
      {categories.map((c) => {
        const Icon = ICON[c.key] ?? Workflow;
        const example = c.examples[0];
        return (
          <Link className="list-row list-row--wrap list-row--hover" key={c.key} href={c.link} data-testid={`autopilot-cat-${c.key}`}>
            <span className={`list-icon ${ICON_TONE[c.tone]}`}><Icon className="ico" aria-hidden="true" /></span>
            <div className="list-main" style={{ minWidth: 0 }}>
              <div className="list-title" style={{ whiteSpace: "normal" }}>{c.label}</div>
              {example ? <div className="list-sub" style={{ whiteSpace: "normal" }}>{localizeAuditSummary(example.summary)}</div> : null}
            </div>
            <div className="list-aside"><strong className="num">{num(c.count)}</strong><ArrowRight className="ico ico-sm t-3" aria-hidden="true" /></div>
          </Link>
        );
      })}
    </div>
  );
}

/** Overzicht → "Wat MSO deed": automatic actions in the last 24 h / 7 days (GET /overview/autopilot?days=1|7). */
export function AutopilotCard({ className = "span-7" }: { className?: string }) {
  const { t, num, relative } = useI18n();
  const [days, setDays] = useState<"1" | "7">("1");
  const res = useResource<AutopilotSummary>("/overview/autopilot", { days }, 60_000);
  return (
    <Card className={className} title={t("autopilot.didTitle")} sub={t("autopilot.didSub")} icon={Bot} flush
      actions={<Segmented label={t("autopilot.period")} value={days} onChange={setDays} options={[
        { value: "1", label: t("autopilot.last24h") }, { value: "7", label: t("autopilot.last7d") },
      ]} />}>
      <ResourceView resource={res} endpoint={ENDPOINT} compact>
        {(s) => (
          <>
            <div className="row wrap" style={{ gap: 8, padding: "12px 16px 4px" }}>
              <ModeLine s={s} />
              <span className="t-2" style={{ fontSize: 12.5 }}>{t("autopilot.total", { count: num(s.total) })}</span>
              {s.syncs.failed ? <StatusChip tone="danger">{t("autopilot.syncsFailed", { count: num(s.syncs.failed) })}</StatusChip> : null}
            </div>
            {s.categories.length === 0 ? (
              <StateView kind="empty" compact title={t("autopilot.nothingTitle")}
                body={s.mode === "read_only" ? t("autopilot.nothingReadOnly") : s.paused ? t("autopilot.nothingPaused") : t("autopilot.nothingBody")} />
            ) : <CategoryRows categories={s.categories} />}
            <div className="row-between wrap" style={{ padding: "8px 16px 12px", gap: 8 }}>
              <span className="t-3" style={{ fontSize: 12 }}>{t("autopilot.updated", { age: relative(s.generated_at) })}</span>
              <Link className="link-more" href="/automatisering?tab=samenvatting">{t("autopilot.digestLink")} <ArrowRight className="ico ico-sm" aria-hidden="true" /></Link>
            </div>
          </>
        )}
      </ResourceView>
    </Card>
  );
}

/** Overzicht → "Wacht op jou": open changes that need a person (malware cleanup etc.) with one-click "Bekijken". */
export function WaitingCard({ className = "span-5" }: { className?: string }) {
  const { t, num, relative } = useI18n();
  const res = useResource<AutopilotSummary>("/overview/autopilot", { days: "1" }, 60_000);
  const count = res.data?.waiting.count ?? 0;
  return (
    <Card className={className} title={t("autopilot.waitTitle")} sub={t("autopilot.waitSub")} icon={Hand} flush
      actions={count ? <StatusChip tone="warning">{num(count)}</StatusChip> : undefined}>
      <ResourceView resource={res} endpoint={ENDPOINT} compact isEmpty={(s) => s.waiting.count === 0}
        empty={<StateView kind="empty" compact title={t("autopilot.waitEmpty")} body={t("autopilot.waitEmptyBody")} />}>
        {(s) => (
          <div className="list">
            {s.waiting.items.slice(0, 8).map((w) => (
              <div className="list-row list-row--wrap" key={String(w.id)} data-testid="waiting-item">
                <span className={`list-icon ${w.deletes ? "list-icon--danger" : "list-icon--warning"}`}>
                  {w.deletes ? <Trash2 className="ico" aria-hidden="true" /> : <Hand className="ico" aria-hidden="true" />}
                </span>
                <div className="list-main" style={{ minWidth: 0 }}>
                  <div className="list-title" style={{ whiteSpace: "normal" }}>{w.title}</div>
                  <div className="list-sub" style={{ whiteSpace: "normal" }}>{w.reason} · {relative(w.created_at)}</div>
                </div>
                <div className="list-aside">
                  <RiskChip risk={w.risk} />
                  <LinkButton size="sm" href={w.link}>{t("autopilot.view")}</LinkButton>
                </div>
              </div>
            ))}
            {s.waiting.count > 8 ? (
              <div style={{ padding: "8px 16px 12px" }}>
                <Link className="link-more" href="/automatisering?tab=wijzigingen">{t("autopilot.waitMore", { count: num(s.waiting.count - 8) })} <ArrowRight className="ico ico-sm" aria-hidden="true" /></Link>
              </div>
            ) : null}
          </div>
        )}
      </ResourceView>
    </Card>
  );
}

/** Automatisering → "Samenvatting": weekly digest grouped by day (GET /overview/autopilot?days=7). */
export function WeeklyDigest() {
  const { t, num, date, dateTime } = useI18n();
  const res = useResource<AutopilotSummary>("/overview/autopilot", { days: "7" }, 120_000);
  return (
    <>
      <Card className="span-12" title={t("autopilot.digestTitle")} sub={t("autopilot.digestSub")} icon={CalendarDays} flush>
        <ResourceView resource={res} endpoint={ENDPOINT} compact>
          {(s) => (
            <>
              <div className="row wrap" style={{ gap: 8, padding: "12px 16px 4px" }}>
                <ModeLine s={s} />
                <span className="t-2" style={{ fontSize: 12.5 }}>{t("autopilot.totalWeek", { count: num(s.total) })}</span>
                {s.waiting.count ? <Link href="/automatisering?tab=wijzigingen"><StatusChip tone="warning" icon={Hand}>{t("autopilot.waitingChip", { count: num(s.waiting.count) })}</StatusChip></Link> : null}
              </div>
              {s.categories.length === 0
                ? <StateView kind="empty" compact title={t("autopilot.nothingWeek")} body={s.mode === "read_only" ? t("autopilot.nothingReadOnly") : t("autopilot.nothingBody")} />
                : <CategoryRows categories={s.categories} />}
            </>
          )}
        </ResourceView>
      </Card>
      {res.data ? res.data.by_day.map((d) => {
        const cats = res.data!.categories.filter((c) => d.categories[c.key]);
        return (
          <Card key={d.date} className="span-6" title={date(d.date)} icon={History} flush
            actions={<StatusChip tone={d.total ? "accent" : "neutral"} icon={null}>{t("autopilot.dayTotal", { count: num(d.total) })}</StatusChip>}>
            {d.total === 0 ? <StateView kind="empty" compact title={t("autopilot.dayEmpty")} /> : (
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
