// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/components/mg/domain.tsx — internally versioned copy, see PROVENANCE.md
// (MSO: kept the generic components; InstanceCard → ConnectorCard; DistributionBar takes generic
// segments; media-library specific components (Poster, SeasonHeatmap, QualityRibbon, JobLaneCard) dropped.)
"use client";

import { ArrowRight, Check, CircleCheck, CircleHelp, CircleX, Info, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { Button, Field, Modal, StatusChip } from "@/components/mg/primitives";
import type { Connector } from "@/lib/api/contracts";
import { useI18n } from "@/lib/i18n";
import { healthLabel, healthTone } from "@/lib/status";
import { cn } from "@/lib/utils";

// ------------------------------------------------------------------ KPI
export function KpiCard({ label, icon: Icon, value, unit, meta, href, drill, tone, spark, unavailable }: {
  label: string; icon: LucideIcon; value: ReactNode; unit?: string; meta?: ReactNode; href?: string; drill?: string;
  tone?: "success" | "warning" | "danger"; spark?: number[]; unavailable?: boolean;
}) {
  const { t } = useI18n();
  const body = (
    <>
      <span className="kpi-label"><Icon className="ico" aria-hidden="true" />{label}</span>
      <span className="kpi-value num">{unavailable ? "—" : value}{unit && !unavailable ? <small>{unit}</small> : null}</span>
      <span className="kpi-meta">{unavailable ? t("state.unavailableTitle") : meta}</span>
      {spark && spark.length > 1 && !unavailable ? <Sparkline values={spark} /> : null}
      {href && drill ? <span className="kpi-drill">{drill} <ArrowRight className="ico ico-sm" aria-hidden="true" /></span> : null}
    </>
  );
  const cls = cn("kpi", tone && !unavailable && `kpi--${tone}`);
  return href ? <Link href={href} className={cls}>{body}</Link> : <div className={cls}>{body}</div>;
}

export function Sparkline({ values }: { values: number[] }) {
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 92},${30 - ((v - min) / span) * 26}`).join(" ");
  return <svg className="kpi-spark" viewBox="0 0 92 32" fill="none" aria-hidden="true"><polyline points={pts} stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

// ------------------------------------------------------------------ Connector (was: Instance)
export function ConnectorStatusChip({ connector }: { connector: Pick<Connector, "status" | "enabled"> & { url?: string | null } }) {
  const { t } = useI18n();
  if (connector.url === "" || connector.url === null) return <StatusChip tone="neutral" icon={CircleHelp}>{t("status.notConfigured")}</StatusChip>;
  if (!connector.enabled) return <StatusChip tone="neutral">{t("status.disabled")}</StatusChip>;
  const tone = healthTone(connector.status);
  return <StatusChip tone={tone} icon={tone === "neutral" ? CircleHelp : undefined}>{t(healthLabel(connector.status))}</StatusChip>;
}

const KNOWN_LOGOS = ["prowlarr", "sabnzbd", "qbittorrent", "mediaguard"];
export function ConnectorLogo({ connector }: { connector: Pick<Connector, "id" | "label" | "kind"> }) {
  const initials = connector.label.split(/\s+/).map((p) => p[0]).join("").slice(0, 2).toUpperCase();
  const key = [connector.kind, connector.id].map((v) => String(v).toLowerCase()).find((v) => KNOWN_LOGOS.includes(v)) ?? "generic";
  return <span className={cn("instance-logo", `instance-logo--${key}`)} aria-hidden="true">{initials.length > 1 ? initials : connector.label.slice(0, 2).toUpperCase()}</span>;
}

function capabilityList(value: Connector["capabilities"]): string[] {
  if (!value) return [];
  if (Array.isArray(value)) return value.map(String);
  return Object.entries(value).filter(([, v]) => v !== false && v !== null).map(([k]) => k);
}

/** Read-only summary card for a connector (status, version, URL, secrets status, last check). */
export function ConnectorCard({ connector, actions, children }: { connector: Connector; actions?: ReactNode; children?: ReactNode }) {
  const { t, relative } = useI18n();
  const tone = connector.enabled ? healthTone(connector.status) : "neutral";
  const caps = capabilityList(connector.capabilities);
  return (
    <article className={cn("instance", tone === "warning" && "instance--degraded", tone === "danger" && "instance--offline")} data-testid={`connector-${connector.id}`}>
      <div className="instance-head">
        <ConnectorLogo connector={connector} />
        <div className="grow"><div className="instance-name">{connector.label}</div><div className="instance-id">{connector.id}</div></div>
        <ConnectorStatusChip connector={connector} />
      </div>
      <dl className="kv" style={{ fontSize: 12 }}>
        <dt>{t("connector.url")}</dt><dd className="mono" style={{ overflowWrap: "anywhere" }}>{connector.url || "—"}</dd>
        <dt>{t("connector.version")}</dt><dd className="mono">{connector.version ?? "—"}</dd>
        <dt>{t("connector.checked")}</dt><dd>{connector.last_checked_at ? relative(connector.last_checked_at) : "—"}</dd>
        {connector.secret_fields.map((f) => <div key={f.name} style={{ display: "contents" }}><dt>{f.label}</dt><dd>{f.status === "ingesteld" ? t("status.set") : t("status.empty")}</dd></div>)}
        {caps.length ? <><dt>{t("connector.capabilities")}</dt><dd className="row wrap" style={{ gap: 4 }}>{caps.map((c) => <span key={c} className="tag">{c}</span>)}</dd></> : null}
      </dl>
      {connector.last_error ? <p className="t-danger" style={{ fontSize: 12 }}>{connector.last_error}</p> : null}
      {children}
      {actions ? <div className="row wrap">{actions}</div> : null}
    </article>
  );
}

// ------------------------------------------------------------------ Evidence / trace / diff
export interface EvidenceView {
  source: string; verdict: "confirms" | "contradicts" | "neutral"; confidence: number | null;
  matching_id?: string | null; claim?: string | null; method?: string | null; trace?: string[] | null;
  checked_at?: string | null; raw?: unknown;
}

export function Confidence({ value, tone = "success" }: { value: number | null; tone?: "success" | "danger" | "warning" }) {
  const { num } = useI18n();
  if (value === null) return null;
  const color = tone === "danger" ? "var(--mg-danger)" : tone === "warning" ? "var(--mg-warning)" : "var(--mg-success)";
  return <span className="confidence">{num(value, 2)}<span className="bar"><span style={{ width: `${Math.round(value * 100)}%`, background: color }} /></span></span>;
}

export function EvidenceCard({ item }: { item: EvidenceView }) {
  const { t, relative } = useI18n();
  const verdict = item.verdict === "confirms"
    ? <StatusChip tone="success">{t("evidence.confirms")}</StatusChip>
    : item.verdict === "contradicts" ? <StatusChip tone="danger">{t("evidence.contradicts")}</StatusChip>
      : <StatusChip tone="neutral" icon={Info}>{t("evidence.neutral")}</StatusChip>;
  return (
    <article className="evidence">
      <div className="evidence-head">{verdict}<span className="evidence-source">{item.source}</span><span className="grow" /><Confidence value={item.confidence} tone={item.verdict === "contradicts" ? "danger" : "success"} /></div>
      <dl className="evidence-body">
        {item.matching_id ? <><dt>{t("evidence.matchingId")}</dt><dd className="mono">{item.matching_id}</dd></> : null}
        {item.claim ? <><dt>{t("evidence.claim")}</dt><dd>{item.claim}</dd></> : null}
        {item.method ? <><dt>{t("evidence.method")}</dt><dd>{item.method}</dd></> : null}
        {item.trace?.length ? <><dt>{t("evidence.trace")}</dt><dd><Trace steps={item.trace.map((s, i) => ({ label: s, active: i === item.trace!.length - 1 }))} /></dd></> : null}
        {item.checked_at ? <><dt>{t("evidence.checked")}</dt><dd>{relative(item.checked_at)}</dd></> : null}
      </dl>
      {item.raw !== undefined && item.raw !== null ? (
        <details className="raw"><summary>{t("evidence.raw")}</summary><pre>{JSON.stringify(item.raw, null, 2)}</pre></details>
      ) : null}
    </article>
  );
}

export function Trace({ steps }: { steps: Array<{ label: ReactNode; active?: boolean; unset?: boolean }> }) {
  return (
    <span className="trace">
      {steps.map((s, i) => (
        <span key={i} className="row" style={{ gap: 6 }}>
          {i > 0 ? <span className="trace-arrow" aria-hidden="true">→</span> : null}
          <span className={cn("trace-step", s.active && "trace-step--active", s.unset && "trace-step--unset")} aria-current={s.active ? "true" : undefined}>{s.label}</span>
        </span>
      ))}
    </span>
  );
}

export function DiffViewer({ rows, headers, keyWidth = 120 }: {
  rows: Array<{ key: string; label?: ReactNode; before: ReactNode; after: ReactNode; change?: "changed" | "removed" | "added" | "same" }>;
  headers: [string, string, string]; keyWidth?: number;
}) {
  return (
    <div className="diff" style={{ gridTemplateColumns: `${keyWidth}px 1fr 1fr` }} role="table" aria-label={headers.join(" / ")}>
      {headers.map((h) => <div key={h} className="diff-head" role="columnheader">{h}</div>)}
      {rows.map((r) => {
        const changed = r.change && r.change !== "same";
        return (
          <div key={r.key} style={{ display: "contents" }} role="row">
            <div className="diff-key" role="rowheader">{r.label ?? r.key}</div>
            <div className={cn(changed && r.change === "removed" && "diff-removed", changed && r.change === "changed" && "")} role="cell">{r.before ?? "—"}</div>
            <div className={cn(r.change === "changed" && "diff-changed", r.change === "added" && "diff-added", r.change === "removed" && "diff-added")} role="cell">{r.after ?? "—"}</div>
          </div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ Distribution
const VIZ = ["var(--mg-viz-1)", "var(--mg-viz-2)", "var(--mg-viz-3)", "var(--mg-viz-4)", "var(--mg-viz-5)", "var(--mg-viz-6)"];

/** Stacked bar + legend. Colours default to the categorical --mg-viz-* palette in order. */
export function DistributionBar({ segments }: { segments: Array<{ key: string; label: string; value: number; color?: string }> }) {
  const { num } = useI18n();
  const total = segments.reduce((sum, s) => sum + (s.value ?? 0), 0) || 1;
  const aria = segments.map((s) => `${s.label} ${num(s.value)}`).join(", ");
  return (
    <div>
      <div className="stackbar" role="img" aria-label={aria}>
        {segments.map((s, i) => <span key={s.key} style={{ width: `${(s.value / total) * 100}%`, background: s.color ?? VIZ[i % VIZ.length] }} />)}
      </div>
      <div className="legend" style={{ marginTop: 10 }}>
        {segments.map((s, i) => <span className="legend-item" key={s.key}><span className="legend-swatch" style={{ background: s.color ?? VIZ[i % VIZ.length] }} />{s.label} <strong className="num">{num(s.value)}</strong></span>)}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Stepper / timeline
export function Stepper({ steps }: { steps: Array<{ label: string; status: "done" | "current" | "warning" | "blocked" | "todo"; meta?: ReactNode; onClick?: () => void }> }) {
  return (
    <ol className="stepper" style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {steps.map((s, i) => (
        <li key={s.label} className={cn("step", s.status !== "todo" && `step--${s.status}`)} aria-current={s.status === "current" ? "step" : undefined}>
          <span className="step-no">{s.status === "done" ? <Check className="ico ico-sm" aria-hidden="true" /> : i + 1}</span>
          {s.onClick ? <button type="button" className="btn--ghost" style={{ border: 0, background: "none", padding: 0, textAlign: "left", font: "inherit", color: "inherit" }} onClick={s.onClick}>{s.label}</button> : <span>{s.label}</span>}
          <span>{s.meta}</span>
        </li>
      ))}
    </ol>
  );
}

export function Timeline({ items }: { items: Array<{ title: ReactNode; meta?: ReactNode; tone?: "success" | "danger" | "warning" | "accent" | "neutral"; icon?: LucideIcon }> }) {
  return (
    <div className="timeline">
      {items.map((item, i) => {
        const Icon = item.icon ?? (item.tone === "success" ? CircleCheck : item.tone === "danger" ? CircleX : Info);
        return (
          <div className="tl-item" key={i}>
            <span className={cn("tl-dot", item.tone && item.tone !== "neutral" && `tl-dot--${item.tone}`)}><Icon className="ico" aria-hidden="true" /></span>
            <div><div className="tl-title">{item.title}</div>{item.meta ? <div className="tl-meta">{item.meta}</div> : null}</div>
          </div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ Danger confirm
export function DangerDialog({ open, onClose, onConfirm, title, description, impact, facts, confirmLabel, confirmWord, pending, error, icon, children }: {
  open: boolean; onClose: () => void; onConfirm: () => void; title: string; description: ReactNode;
  impact?: Array<{ label: string; value: ReactNode }>; facts?: Array<{ label: string; value: ReactNode }>;
  confirmLabel: string; confirmWord?: string; pending?: boolean; error?: string | null; icon?: LucideIcon; children?: ReactNode;
}) {
  const { t } = useI18n();
  const [typed, setTyped] = useState("");
  const ok = !confirmWord || typed.trim() === confirmWord;
  const close = () => { setTyped(""); onClose(); };
  return (
    <Modal
      open={open}
      onClose={close}
      danger
      role="alertdialog"
      icon={icon}
      title={title}
      description={description}
      footer={<><Button onClick={close} data-autofocus>{t("action.cancel")}</Button><Button variant="danger-solid" onClick={onConfirm} disabled={!ok} pending={pending}>{confirmLabel}</Button></>}
    >
      {impact?.length ? <div className="impact">{impact.map((i) => <div key={i.label}><span>{i.label}</span><strong>{i.value}</strong></div>)}</div> : null}
      {facts?.length ? <dl className="kv">{facts.map((f) => <div key={f.label} style={{ display: "contents" }}><dt>{f.label}</dt><dd>{f.value}</dd></div>)}</dl> : null}
      {children}
      {confirmWord ? (
        <div style={{ marginTop: 14 }}>
          <Field label={t("dialog.typeToConfirm", { word: confirmWord })} htmlFor="danger-confirm">
            <input id="danger-confirm" className="input" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          </Field>
        </div>
      ) : null}
      {error ? <div className="field-error" role="alert" style={{ marginTop: 12 }}><CircleX className="ico ico-sm" aria-hidden="true" />{error}</div> : null}
    </Modal>
  );
}

/** Button that is disabled when the endpoint it needs is not yet offered by the backend. */
export function PendingAction({ endpoint, children, icon, variant = "secondary", size }: { endpoint: string; children: ReactNode; icon?: LucideIcon; variant?: "primary" | "secondary" | "ghost" | "danger"; size?: "sm" }) {
  const { t } = useI18n();
  return <Button variant={variant} size={size} icon={icon} disabled title={t("state.backendRequired", { endpoint })} aria-disabled="true">{children}</Button>;
}
