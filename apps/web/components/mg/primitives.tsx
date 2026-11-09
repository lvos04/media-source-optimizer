// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/components/mg/primitives.tsx — internally versioned copy, see PROVENANCE.md
"use client";

import {
  Check, CircleCheck, CircleHelp, CirclePause, ClipboardCheck, Copy, Info, Link2, Link2Off, Loader, LoaderCircle, Lock, OctagonX,
  TriangleAlert, type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState, useSyncExternalStore, type ButtonHTMLAttributes, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Peil (ONTWERPTAAL §4): five axes, five shapes. Only `healthy` is green and only for MEASURED health; a positive
 * outcome that is not health ("Aan", "Gelukt", "Afgerond", "Ingesteld") is `success`: a neutral label with a check.
 *   healthy  → Gezond      (pill without fill, circle-check)       warning → Beperkt (fill + 2 px border, triangle-alert)
 *   danger   → Storing     (octagon-x)                              unknown → Onbekend (dashed, circle-help)
 *   paused   → Gepauzeerd  (circle-pause, info tokens)              linked / unlinked → Gekoppeld / Niet gekoppeld
 *   approval → goedkeuring (violet, clipboard-check)                success / neutral / accent / info → neutral labels
 */
export type Tone = "healthy" | "success" | "warning" | "danger" | "info" | "accent" | "neutral" | "unknown" | "paused" | "linked" | "unlinked" | "approval";

export const TONE_ICON: Record<Tone, LucideIcon | null> = {
  healthy: CircleCheck,
  success: Check,
  warning: TriangleAlert,
  danger: OctagonX,
  info: Info,
  accent: LoaderCircle,
  neutral: null, // never the pause icon for "unknown/off/quiet" (Peil §7)
  unknown: CircleHelp,
  paused: CirclePause,
  linked: Link2,
  unlinked: Link2Off,
  approval: ClipboardCheck,
};

const TONE_CLASS: Record<Tone, string | null> = {
  healthy: "chip--healthy", success: "chip--done", warning: "chip--warning", danger: "chip--danger", info: "chip--info",
  accent: "chip--accent", neutral: null, unknown: "chip--unknown", paused: "chip--paused", linked: "chip--linked",
  unlinked: "chip--unknown", approval: "chip--approval",
};
/** Health axis for forced-colors and tests (`[data-status]`); other axes carry no data-status. */
const TONE_STATUS: Partial<Record<Tone, string>> = { healthy: "healthy", warning: "degraded", danger: "down", unknown: "unknown", unlinked: "unknown", paused: "paused" };

/** Status = shape + icon + word (never colour alone). */
export function StatusChip({ tone, children, icon, pending = false, size, outline = false, title }: {
  tone: Tone; children: ReactNode; icon?: LucideIcon | null; pending?: boolean; size?: "lg"; outline?: boolean; title?: string;
}) {
  const Icon = icon === null ? null : icon ?? (pending ? LoaderCircle : TONE_ICON[tone]);
  return (
    <span className={cn("chip", TONE_CLASS[tone], pending && "chip--pending", size === "lg" && "chip--lg", outline && "chip--outline")} title={title} data-status={TONE_STATUS[tone]}>
      {Icon ? <Icon className="ico" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

export function LockedChip() {
  const { t } = useI18n();
  return <StatusChip tone="neutral" icon={Lock}>{t("status.locked")}</StatusChip>;
}

/** Decorative dot next to a word; `healthy` is the only green one (measured health). */
export function Dot({ tone }: { tone: "healthy" | "success" | "warning" | "danger" | "accent" | "neutral" | "unknown" }) {
  const cls = tone === "healthy" ? "dot--success" : tone === "warning" || tone === "danger" ? `dot--${tone}` : tone === "unknown" ? "dot--unknown" : null;
  return <span className={cn("dot", cls)} aria-hidden="true" />;
}

export function Card({ title, icon: Icon, sub, actions, children, className, bodyClass, footer, flush, id, tone }: {
  title?: ReactNode; icon?: LucideIcon; sub?: ReactNode; actions?: ReactNode; children?: ReactNode; className?: string;
  bodyClass?: string; footer?: ReactNode; flush?: boolean; id?: string; tone?: "danger" | "warning";
}) {
  const autoId = useId();
  const headingId = id ?? `card-${autoId}`;
  return (
    <section className={cn("card", tone && `card--${tone}`, className)} aria-labelledby={title ? headingId : undefined}>
      {title || actions ? (
        <div className="card-header">
          {title ? <h2 className="card-title" id={headingId}>{Icon ? <Icon className="ico" aria-hidden="true" /> : null}{title}{sub ? <span className="card-sub">{sub}</span> : null}</h2> : <span />}
          {actions ? <div className="row wrap">{actions}</div> : null}
        </div>
      ) : null}
      {flush ? children : <div className={cn("card-body", bodyClass)}>{children}</div>}
      {footer ? <div className="card-footer">{footer}</div> : null}
    </section>
  );
}

export function PageHeader({ title, sub, actions, crumbs }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; crumbs?: Array<{ label: string; href?: string }> }) {
  return (
    <div className="page-header">
      <div>
        {crumbs?.length ? (
          <nav className="crumbs" aria-label="breadcrumb">
            {crumbs.map((c, i) => (
              <span key={`${c.label}-${i}`} className="row" style={{ gap: 6 }}>
                {i > 0 ? <span aria-hidden="true">›</span> : null}
                {c.href ? <Link href={c.href}>{c.label}</Link> : <span>{c.label}</span>}
              </span>
            ))}
          </nav>
        ) : null}
        <h1 className="page-title">{title}</h1>
        {sub ? <p className="page-sub">{sub}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  );
}

type BtnVariant = "primary" | "secondary" | "ghost" | "danger" | "danger-solid";
export function Button({ variant = "secondary", size, icon: Icon, pending, children, className, iconOnly, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: BtnVariant; size?: "sm"; icon?: LucideIcon; pending?: boolean; iconOnly?: boolean;
}) {
  return (
    <button
      type="button"
      {...rest}
      aria-busy={pending || undefined}
      disabled={rest.disabled || pending}
      className={cn("btn", variant !== "secondary" && `btn--${variant}`, size === "sm" && "btn--sm", iconOnly && "btn--icon", className)}
    >
      {pending ? <Loader className="ico" aria-hidden="true" style={{ animation: "mg-spin 1.2s linear infinite" }} /> : Icon ? <Icon className="ico" aria-hidden="true" /> : null}
      {iconOnly ? <span className="sr-only">{children}</span> : children}
    </button>
  );
}

export function LinkButton({ href, variant = "secondary", size, icon: Icon, children }: { href: string; variant?: BtnVariant; size?: "sm"; icon?: LucideIcon; children: ReactNode }) {
  return (
    <Link href={href} className={cn("btn", variant !== "secondary" && `btn--${variant}`, size === "sm" && "btn--sm")}>
      {Icon ? <Icon className="ico" aria-hidden="true" /> : null}{children}
    </Link>
  );
}

export function Switch({ checked, onChange, label, disabled, id }: { checked: boolean; onChange?: (v: boolean) => void; label: string; disabled?: boolean; id?: string }) {
  return (
    <button id={id} type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} className="switch" onClick={() => onChange?.(!checked)} />
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: Array<{ value: T; label: ReactNode; count?: number | string; disabled?: boolean; title?: string }>; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} disabled={o.disabled} title={o.title} onClick={() => onChange(o.value)}>
          {o.label}{o.count !== undefined ? <span className="count">{o.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

/** Route-based tabs (each tab is a link) or state tabs (onSelect). */
export function Tabs<T extends string>({ items, value, onSelect, label }: {
  items: Array<{ value: T; label: ReactNode; count?: number | string; href?: string }>; value: T; onSelect?: (v: T) => void; label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const onKey = (e: React.KeyboardEvent) => {
    if (!onSelect || (e.key !== "ArrowRight" && e.key !== "ArrowLeft")) return;
    const idx = items.findIndex((i) => i.value === value);
    const next = items[(idx + (e.key === "ArrowRight" ? 1 : items.length - 1)) % items.length];
    onSelect(next.value);
    window.setTimeout(() => ref.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus(), 0);
  };
  return (
    <div className="tabs" role={onSelect ? "tablist" : undefined} aria-label={label} ref={ref} onKeyDown={onKey}>
      {items.map((item) =>
        item.href ? (
          <Link key={item.value} href={item.href} className="tab" aria-current={item.value === value ? "page" : undefined}>
            {item.label}{item.count !== undefined ? <span className="count">{item.count}</span> : null}
          </Link>
        ) : (
          <button key={item.value} type="button" role="tab" className="tab" aria-selected={item.value === value} tabIndex={item.value === value ? 0 : -1} onClick={() => onSelect?.(item.value)}>
            {item.label}{item.count !== undefined ? <span className="count">{item.count}</span> : null}
          </button>
        ),
      )}
    </div>
  );
}

export function Field({ label, hint, error, htmlFor, children }: { label: ReactNode; hint?: ReactNode; error?: ReactNode; htmlFor: string; children: ReactNode }) {
  return (
    <div>
      <label className="label" htmlFor={htmlFor}>{label}</label>
      {children}
      {error ? <div className="field-error" id={`${htmlFor}-error`} role="alert"><OctagonX className="ico ico-sm" aria-hidden="true" />{error}</div> : hint ? <p className="hint" id={`${htmlFor}-hint`}>{hint}</p> : null}
    </div>
  );
}

/** Inline notice. `success` = something went as asked (neutral with a check, not "healthy"); danger = octagon-x. */
export function InlineState({ tone = "info", icon: Icon, children, role }: { tone?: "info" | "warning" | "danger" | "success" | "neutral"; icon?: LucideIcon; children: ReactNode; role?: "status" | "alert" }) {
  const I = Icon ?? (tone === "danger" ? OctagonX : tone === "warning" ? TriangleAlert : tone === "success" ? Check : Info);
  return (
    <div className={cn("inline-state", tone !== "neutral" && `inline-state--${tone}`)} role={role}>
      <I className="ico" aria-hidden="true" />
      <div>{children}</div>
    </div>
  );
}

export function Progress({ value, tone, thin, label, indeterminate }: { value: number | null | undefined; tone?: "success" | "warning" | "danger" | "cpu"; thin?: boolean; label: string; indeterminate?: boolean }) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div className={cn("progress", tone && `progress--${tone}`, thin && "progress--thin", indeterminate && "progress--indeterminate")} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={indeterminate ? undefined : Math.round(v)}>
      <span style={{ width: `${v}%` }} />
    </div>
  );
}

export function Skeleton({ lines = 3, height }: { lines?: number; height?: number }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton skeleton-line" style={{ width: `${90 - i * 12}%`, height }} />
      ))}
    </div>
  );
}

/**
 * Peil KopieerVeld (C-15): mono, selectable, wraps (never an ellipsis). The clipboard is only used in a secure context;
 * otherwise the text is selected and the user is told to copy via the browser menu. Feedback via aria-live.
 */
export function CopyField({ label, value }: { label: string; value: string }) {
  const { t } = useI18n();
  const ref = useRef<HTMLElement>(null);
  const [msg, setMsg] = useState("");
  const copy = async () => {
    const select = () => {
      const sel = window.getSelection();
      if (ref.current && sel) { const range = document.createRange(); range.selectNodeContents(ref.current); sel.removeAllRanges(); sel.addRange(range); }
    };
    if (window.isSecureContext && navigator.clipboard) {
      try { await navigator.clipboard.writeText(value); setMsg(t("copy.done")); return; } catch { /* fall back to selecting */ }
    }
    select();
    setMsg(t("copy.selected"));
  };
  return (
    <span className="copyfield">
      <span className="copyfield-label">{label}</span>
      <code ref={ref} className="copyfield-value">{value}</code>
      <button type="button" className="btn btn--sm" onClick={() => void copy()}><Copy className="ico" aria-hidden="true" />{t("copy.button")}<span className="sr-only"> {label}</span></button>
      <span className="copyfield-msg" aria-live="polite">{msg}</span>
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <span className="kbd">{children}</span>;
}

const noopSubscribe = () => () => undefined;

/**
 * Simple modal dialog with focus management (Esc closes, focus restored).
 * Rendered in a portal on `document.body`, so an ancestor with `backdrop-filter`/`transform` (the sticky topbar)
 * can never become the containing block of the fixed overlay (which clipped dialogs opened from the topbar).
 * Initial focus is set ONCE per opening; re-renders of the parent (polling, typing in a field inside the dialog,
 * a new `onClose` identity on every render) never move focus again.
 */
export function Modal({ open, onClose, title, children, footer, danger, icon: Icon, labelledBy, role = "dialog", description, wide }: {
  open: boolean; onClose: () => void; title: ReactNode; children?: ReactNode; footer?: ReactNode; danger?: boolean; icon?: LucideIcon;
  labelledBy?: string; role?: "dialog" | "alertdialog"; description?: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLElement>(null);
  const titleId = useId();
  const descId = useId();
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);
  const mounted = useSyncExternalStore(noopSubscribe, () => true, () => false);
  useEffect(() => {
    if (!open || !mounted) return;
    const previous = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const focusables = () => Array.from(node?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])') ?? []);
    // only when focus is not already inside the dialog (never steal focus from a field the user is typing in)
    if (!node?.contains(document.activeElement)) (node?.querySelector<HTMLElement>("[data-autofocus]") ?? focusables()[0])?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onCloseRef.current(); }
      if (e.key === "Tab") {
        const items = focusables();
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      previous?.focus?.();
    };
  }, [open, mounted]);
  if (!open || !mounted) return null;
  return createPortal(
    <div className="overlay" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onCloseRef.current(); }}>
      <section ref={ref} className={cn("dialog", danger && "dialog--danger", wide && "dialog--wide")} role={role} aria-modal="true" aria-labelledby={labelledBy ?? titleId} aria-describedby={description ? descId : undefined}>
        <div className="dialog-head">
          {Icon ? <span className={cn("list-icon", danger ? "list-icon--danger" : "list-icon--accent")}><Icon className="ico" aria-hidden="true" /></span> : null}
          <div>
            <h2 className="dialog-title" id={titleId}>{title}</h2>
            {description ? <p className="t-2" id={descId} style={{ fontSize: 13, marginTop: 4 }}>{description}</p> : null}
          </div>
        </div>
        {children ? <div className="dialog-body">{children}</div> : null}
        {footer ? <div className="dialog-foot">{footer}</div> : null}
      </section>
    </div>,
    document.body,
  );
}
