// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/components/mg/primitives.tsx — internally versioned copy, see PROVENANCE.md
// Peil fase 2 (docs/ontwerp/PEIL_MIGRATIE.md §6.3): same exports and props as before, now built on the shadcn/ui
// components in components/ui (Tailwind 4 utilities, Radix Dialog). Hook classes used by tests and CSS selectors
// (.btn, .chip, .skeleton) stay on the elements; data-testid's, roles and accessible names are unchanged.
"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  Check, ChevronRight, CircleCheck, CircleHelp, CirclePause, ClipboardCheck, ClockAlert, Copy, Info, Link2, Link2Off, Loader, LoaderCircle, Lock,
  OctagonX, TriangleAlert, type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import {
  Children, cloneElement, isValidElement, useId, useRef, useState, type ButtonHTMLAttributes, type KeyboardEvent, type ReactElement,
  type ReactNode,
} from "react";

import { badgeVariants } from "@/components/ui/badge";
import { buttonVariants, type ButtonVariantProps } from "@/components/ui/button";
import { useI18n, type MessageKey } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Peil (ONTWERPTAAL §4): five axes, five shapes. Only `healthy` is green and only for MEASURED health; a positive
 * outcome that is not health ("Aan", "Gelukt", "Afgerond", "Ingesteld") is `success`: a neutral label with a check.
 *   healthy  → Gezond      (pill without fill, circle-check)       warning → Beperkt (fill + 2 px border, triangle-alert)
 *   danger   → Storing     (octagon-x)                              unknown → Onbekend (dashed, circle-help)
 *   paused   → Gepauzeerd  (circle-pause, info tokens)              linked / unlinked → Gekoppeld / Niet gekoppeld
 *   approval → goedkeuring (violet, clipboard-check)                success / neutral / accent / info → neutral labels
 */
export type Tone = "healthy" | "success" | "warning" | "danger" | "info" | "accent" | "neutral" | "unknown" | "stale" | "paused" | "linked" | "unlinked" | "approval";

export const TONE_ICON: Record<Tone, LucideIcon | null> = {
  healthy: CircleCheck,
  success: Check,
  warning: TriangleAlert,
  danger: OctagonX,
  info: Info,
  accent: LoaderCircle,
  neutral: null, // never the pause icon for "unknown/off/quiet" (Peil §7)
  unknown: CircleHelp,
  stale: ClockAlert,
  paused: CirclePause,
  linked: Link2,
  unlinked: Link2Off,
  approval: ClipboardCheck,
};

/** Health axis for forced-colors and tests (`[data-status]`); other axes carry no data-status. */
const TONE_STATUS: Partial<Record<Tone, string>> = { healthy: "healthy", warning: "degraded", danger: "down", unknown: "unknown", stale: "stale", unlinked: "unknown", paused: "paused" };

/** StatusBadge / Label (C-03): status = shape + icon + word (never colour alone). */
export function StatusChip({ tone, children, icon, pending = false, size, outline = false, title }: {
  tone: Tone; children: ReactNode; icon?: LucideIcon | null; pending?: boolean; size?: "lg"; outline?: boolean; title?: string;
}) {
  const Icon = icon === null ? null : icon ?? (pending ? LoaderCircle : TONE_ICON[tone]);
  return (
    <span className={cn(badgeVariants({ tone, size: size === "lg" ? "groot" : "standaard", outline }), pending && "chip--pending")} title={title} data-status={TONE_STATUS[tone]}>
      {Icon ? <Icon className={cn("ico", pending && "motion-safe:animate-spin")} aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

/**
 * ErnstLabel (T-20, COMPONENTEN §9): rechthoek met een linkerbalk van 6 px, alleen tekst — een eigen vorm, zodat ernst
 * nooit op een gezondheidspil lijkt. Kritiek · Hoog · Middel · Laag · Info.
 */
export type Ernst = "kritiek" | "hoog" | "middel" | "laag" | "info";
const ERNST_CLASS: Record<Ernst, string> = {
  kritiek: "border-l-down-ink bg-destructive text-destructive-foreground",
  hoog: "border-l-down bg-down-bg text-down-ink",
  middel: "border-l-foreground bg-muted text-foreground",
  laag: "border-l-unknown bg-unknown-bg text-unknown-ink",
  info: "border-l-info bg-info-bg text-info-ink",
};
export function ErnstLabel({ ernst }: { ernst: Ernst }) {
  const { t } = useI18n();
  return (
    <span className={cn("ernst inline-flex w-fit shrink-0 items-center rounded-sm border-l-[6px] px-2 py-0.5 text-sm font-semibold leading-tight", ERNST_CLASS[ernst])} data-ernst={ernst}>
      <span className="sr-only">{t("ernst.label")}: </span>{t(`ernst.${ernst}` as MessageKey)}
    </span>
  );
}

export function LockedChip() {
  const { t } = useI18n();
  return <StatusChip tone="neutral" icon={Lock}>{t("status.locked")}</StatusChip>;
}

/** Decorative dot next to a word; `healthy` is the only green one (measured health); unknown is a dashed ring. */
export function Dot({ tone }: { tone: "healthy" | "success" | "warning" | "danger" | "accent" | "neutral" | "unknown" }) {
  // Review stap 5: een gekleurde stip verschilde per status alleen in kleur. Een gezondheidsstatus krijgt nu het
  // Peil-icoon (circle-check / triangle-alert / octagon-x / circle-help) in de statuskleur; het woord staat ernaast.
  const Icon = tone === "healthy" ? CircleCheck : tone === "warning" ? TriangleAlert : tone === "danger" ? OctagonX : tone === "unknown" ? CircleHelp : null;
  if (Icon) {
    const kleur = tone === "healthy" ? "text-healthy" : tone === "warning" ? "text-degraded" : tone === "danger" ? "text-down" : "text-unknown";
    return <Icon className={cn("dot ico size-4 shrink-0", kleur)} data-tone={tone} aria-hidden="true" />;
  }
  return <span className="dot inline-block size-2.5 shrink-0 rounded-full bg-muted-foreground shadow-none" aria-hidden="true" />;
}

/** Card (C-02): 1 px border instead of shadows; the title is an h2 that labels the section. */
export function Card({ title, icon: Icon, sub, actions, children, className, bodyClass, footer, flush, id, tone }: {
  title?: ReactNode; icon?: LucideIcon; sub?: ReactNode; actions?: ReactNode; children?: ReactNode; className?: string;
  bodyClass?: string; footer?: ReactNode; flush?: boolean; id?: string; tone?: "danger" | "warning";
}) {
  const autoId = useId();
  const headingId = id ?? `card-${autoId}`;
  return (
    <section
      className={cn("card @container min-w-0 rounded-lg border bg-card text-card-foreground shadow-none",
        tone === "danger" ? "card--danger border-2 border-down" : tone === "warning" ? "card--warning border-2 border-degraded" : "border-border", className)}
      aria-labelledby={title ? headingId : undefined}>
      {title || actions ? (
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4 pt-4 pb-2">
          {title ? (
            <h2 className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-lg font-semibold leading-snug" id={headingId}>
              {Icon ? <Icon className="ico size-5 shrink-0 text-muted-foreground" aria-hidden="true" /> : null}{title}
              {sub ? <span className="text-sm font-normal text-muted-foreground">{sub}</span> : null}
            </h2>
          ) : <span />}
          {actions ? <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      {flush ? children : <div className={cn("card-body px-4 pt-1 pb-4", bodyClass)}>{children}</div>}
      {footer ? <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-3 text-sm text-muted-foreground">{footer}</div> : null}
    </section>
  );
}

/** PageHeader: breadcrumb (chevron-right, 44 px links), one h1 (text-2xl), description at most 65ch, actions. */
export function PageHeader({ title, sub, actions, crumbs }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; crumbs?: Array<{ label: string; href?: string }> }) {
  return (
    <div className="page-header mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0 max-w-full">
        {crumbs?.length ? (
          <nav className="mb-1 flex flex-wrap items-center gap-1 text-sm text-muted-foreground" aria-label="breadcrumb">
            {crumbs.map((c, i) => (
              <span key={`${c.label}-${i}`} className="inline-flex items-center gap-1">
                {i > 0 ? <ChevronRight className="size-4" aria-hidden="true" /> : null}
                {c.href ? <Link href={c.href} className="inline-flex min-h-touch items-center text-link">{c.label}</Link> : <span>{c.label}</span>}
              </span>
            ))}
          </nav>
        ) : null}
        <h1 className="page-title text-2xl font-bold leading-tight text-balance">{title}</h1>
        {sub ? <p className="mt-1 max-w-prose text-base text-muted-foreground">{sub}</p> : null}
      </div>
      {actions ? <div className="flex max-w-full flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

type BtnVariant = "primary" | "secondary" | "ghost" | "danger" | "danger-solid";
const VARIANT: Record<BtnVariant, NonNullable<ButtonVariantProps["variant"]>> = {
  primary: "primair", secondary: "secundair", ghost: "stil", danger: "gevaar", "danger-solid": "gevaar-vol",
};

/** Button (shadcn, Peil codemod): ≥ 44 px; `pending` → aria-busy + spinner; label unchanged. */
export function Button({ variant = "secondary", size, icon: Icon, pending, children, className, iconOnly, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: BtnVariant; size?: "sm"; icon?: LucideIcon; pending?: boolean; iconOnly?: boolean;
}) {
  return (
    <button
      type="button"
      {...rest}
      aria-busy={pending || undefined}
      disabled={rest.disabled || pending}
      className={cn(buttonVariants({ variant: VARIANT[variant], size: iconOnly ? "icoon" : size === "sm" ? "klein" : "standaard" }), className)}
    >
      {pending ? <Loader className="ico motion-safe:animate-spin" aria-hidden="true" /> : Icon ? <Icon className="ico" aria-hidden="true" /> : null}
      {iconOnly ? <span className="sr-only">{children}</span> : children}
    </button>
  );
}

export function LinkButton({ href, variant = "secondary", size, icon: Icon, children }: { href: string; variant?: BtnVariant; size?: "sm"; icon?: LucideIcon; children: ReactNode }) {
  return (
    <Link href={href} className={buttonVariants({ variant: VARIANT[variant], size: size === "sm" ? "klein" : "standaard" })}>
      {Icon ? <Icon className="ico" aria-hidden="true" /> : null}{children}
    </Link>
  );
}

/** Switch (role=switch): 44 px hit area around a 2.75 × 1.5 rem track; off = outline, on = primary. */
export function Switch({ checked, onChange, label, disabled, id }: { checked: boolean; onChange?: (v: boolean) => void; label: string; disabled?: boolean; id?: string }) {
  return (
    <button id={id} type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange?.(!checked)}
      className="group focus-ring inline-flex min-h-touch min-w-touch shrink-0 cursor-pointer items-center justify-center rounded-full disabled:cursor-not-allowed">
      <span aria-hidden="true" className={cn(
        "relative inline-flex h-6 w-11 items-center rounded-full border-2 transition-colors",
        checked ? "border-primary bg-primary" : "border-input bg-card",
        "group-disabled:border-dashed group-disabled:border-border-strong group-disabled:bg-muted",
      )}>
        <span className={cn("absolute size-4 rounded-full transition-[left] motion-reduce:transition-none",
          checked ? "left-[1.375rem] bg-primary-foreground" : "left-0.5 bg-foreground", "group-disabled:bg-disabled-foreground")} />
      </span>
    </button>
  );
}

/** ToggleGroup look (shadcn) with aria-pressed buttons inside role=group; wraps on narrow screens, 44 px each. */
export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: Array<{ value: T; label: ReactNode; count?: number | string; disabled?: boolean; title?: string }>; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="segmented inline-flex max-w-full flex-wrap gap-1 rounded-lg border border-border bg-muted p-1" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} disabled={o.disabled} title={o.title} onClick={() => onChange(o.value)}
          className={cn("focus-ring inline-flex min-h-touch cursor-pointer items-center gap-1.5 rounded-md border-2 border-transparent px-3 text-sm font-semibold text-muted-foreground",
            "hover:text-foreground aria-pressed:border-border-strong aria-pressed:bg-card aria-pressed:text-foreground",
            "disabled:cursor-not-allowed disabled:border-dashed disabled:border-border-strong disabled:text-disabled-foreground",
            "[&_svg]:size-[1.125rem]")}>
          {o.label}{o.count !== undefined ? <span className="cijfers font-normal">{o.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

const TAB_CLASS = cn(
  "tab focus-ring relative inline-flex min-h-touch cursor-pointer items-center gap-1.5 border-0 bg-transparent px-3 text-base font-semibold text-muted-foreground no-underline",
  "hover:text-foreground aria-selected:text-foreground aria-[current=page]:text-foreground",
  "after:absolute after:inset-x-2 after:-bottom-px after:h-1 after:rounded-full after:bg-transparent aria-selected:after:bg-primary aria-[current=page]:after:bg-primary",
);

/**
 * Tabs (COMPONENTEN §21): route tabs (links with aria-current) or state tabs (role=tablist, roving tabindex,
 * ←/→ and Home/End). The row wraps instead of hiding tabs behind a horizontal scroll.
 */
export function Tabs<T extends string>({ items, value, onSelect, label }: {
  items: Array<{ value: T; label: ReactNode; count?: number | string; href?: string }>; value: T; onSelect?: (v: T) => void; label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const onKey = (e: KeyboardEvent) => {
    if (!onSelect || !["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const idx = items.findIndex((i) => i.value === value);
    const nextIdx = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : (idx + (e.key === "ArrowRight" ? 1 : items.length - 1)) % items.length;
    onSelect(items[nextIdx].value);
    window.setTimeout(() => ref.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus(), 0);
  };
  const count = (c?: number | string) => (c !== undefined ? <span className="cijfers rounded-full bg-muted px-2 text-sm font-normal text-foreground">{c}</span> : null);
  return (
    <div className="tabs flex flex-wrap gap-1 border-b border-border" role={onSelect ? "tablist" : undefined} aria-label={label} ref={ref} onKeyDown={onKey}>
      {items.map((item) =>
        item.href ? (
          <Link key={item.value} href={item.href} className={TAB_CLASS} aria-current={item.value === value ? "page" : undefined}>
            {item.label}{count(item.count)}
          </Link>
        ) : (
          <button key={item.value} type="button" role="tab" className={TAB_CLASS} aria-selected={item.value === value} tabIndex={item.value === value ? 0 : -1} onClick={() => onSelect?.(item.value)}>
            {item.label}{count(item.count)}
          </button>
        ),
      )}
    </div>
  );
}

/**
 * Field: label above the control, hint via aria-describedby, field error with octagon-x + aria-invalid.
 * The ids are wired into a single child control (cloneElement) unless it sets them itself.
 */
export function Field({ label, hint, error, htmlFor, children }: { label: ReactNode; hint?: ReactNode; error?: ReactNode; htmlFor: string; children: ReactNode }) {
  const describedBy = error ? `${htmlFor}-error` : hint ? `${htmlFor}-hint` : undefined;
  let control = children;
  const only = Children.count(children) === 1 ? Children.toArray(children)[0] : null;
  if (describedBy && isValidElement(only) && typeof only.type === "string") {
    const el = only as ReactElement<Record<string, unknown>>;
    const extra: Record<string, unknown> = {};
    if (el.props["aria-describedby"] === undefined) extra["aria-describedby"] = describedBy;
    if (error && el.props["aria-invalid"] === undefined) extra["aria-invalid"] = true;
    control = cloneElement(el, extra);
  }
  return (
    <div>
      <label className="label mb-1.5 block text-base font-semibold text-foreground" htmlFor={htmlFor}>{label}</label>
      {control}
      {error ? <div className="field-error mt-1.5 flex items-center gap-1.5 text-sm font-semibold text-down-ink" id={`${htmlFor}-error`} role="alert"><OctagonX className="ico size-4" aria-hidden="true" />{error}</div>
        : hint ? <p className="hint mt-1.5 text-sm text-muted-foreground" id={`${htmlFor}-hint`}>{hint}</p> : null}
    </div>
  );
}

/** Inline notice (Peil melding). `success` = something went as asked (neutral with a check, not "healthy"). */
/**
 * Inline melding. Amber (`warning`) alleen voor gemeten gezondheid (Peil §4.1 regel 3); onbekend = stippel + circle-help,
 * verouderd = stippel + clock-alert (nooit amber), een voorwaarde/rol = `neutral` met slot.
 */
export function InlineState({ tone = "info", icon: Icon, children, role }: { tone?: "info" | "warning" | "danger" | "success" | "neutral" | "unknown" | "stale"; icon?: LucideIcon; children: ReactNode; role?: "status" | "alert" }) {
  const I = Icon ?? (tone === "danger" ? OctagonX : tone === "warning" ? TriangleAlert : tone === "success" ? Check : tone === "unknown" ? CircleHelp : tone === "stale" ? ClockAlert : Info);
  const cls = {
    info: "border-info bg-info-bg text-info-ink [&_strong]:text-info-ink",
    warning: "border-degraded bg-degraded-bg text-degraded-ink [&_strong]:text-degraded-ink",
    danger: "border-down bg-down-bg text-down-ink [&_strong]:text-down-ink",
    success: "border-border bg-card text-foreground",
    neutral: "border-border bg-muted text-foreground",
    unknown: "border-dashed border-unknown bg-unknown-bg text-unknown-ink [&_strong]:text-unknown-ink",
    stale: "border-dashed border-stale bg-stale-bg text-foreground [&_svg]:text-stale",
  }[tone];
  return (
    <div className={cn("inline-state flex items-start gap-2.5 rounded-lg border-2 px-3 py-2.5 text-sm", cls)} role={role} data-tone={tone}>
      <I className="ico mt-0.5 size-[1.125rem] shrink-0" aria-hidden="true" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/**
 * Progress (role=progressbar): one neutral fill (chart-1) on the meter track — 100 % is not "green" and a status
 * never lives in the colour (the value and any limit are always shown as text next to it). `tone` is accepted for
 * API compatibility only.
 */
export function Progress({ value, thin, label, indeterminate }: { value: number | null | undefined; tone?: "success" | "warning" | "danger" | "cpu"; thin?: boolean; label: string; indeterminate?: boolean }) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div className={cn("progress relative overflow-hidden rounded-full bg-chart-meter-track", thin ? "h-1.5" : "h-2")} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={indeterminate ? undefined : Math.round(v)}>
      <span className={cn("absolute inset-y-0 left-0 rounded-full bg-chart-1", indeterminate && "arcering-geschat w-1/3 text-chart-1")} style={indeterminate ? undefined : { width: `${v}%` }} />
    </div>
  );
}

/** Skeleton: only on the first load; no animation with reduced motion. The `.skeleton` hook stays (e2e). */
export function Skeleton({ lines = 3, height }: { lines?: number; height?: number }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton skeleton-line my-2 h-3.5 rounded-md bg-muted after:hidden motion-safe:animate-pulse" style={{ width: `${90 - i * 12}%`, height }} />
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
    <span className="copyfield inline-flex max-w-full flex-wrap items-center justify-center gap-x-2 gap-y-1.5">
      <span className="text-sm text-muted-foreground">{label}</span>
      <code ref={ref} className="rounded-sm border border-border bg-muted px-1.5 py-0.5 font-mono text-sm text-foreground select-all [overflow-wrap:anywhere]">{value}</code>
      <Button size="sm" icon={Copy} onClick={() => void copy()}>{t("copy.button")}<span className="sr-only"> {label}</span></Button>
      <span className="text-sm text-muted-foreground empty:hidden" aria-live="polite">{msg}</span>
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd inline-grid h-6 min-w-6 place-items-center rounded-sm border border-b-2 border-border-strong px-1.5 font-mono text-sm font-semibold text-muted-foreground">{children}</kbd>;
}

/**
 * Dialog / AlertDialog (shadcn on Radix Dialog): portal, focus trap, Escape and outside click close, focus returns to
 * the element that opened it. Initial focus goes to `[data-autofocus]` when present (never a destructive button),
 * otherwise Radix focuses the first focusable element; re-renders never move focus. `role="alertdialog"` is kept for
 * confirmations (e2e). Phone: full-width sheet from the bottom; desktop: centred, max 100dvh − 2 rem, body scrolls.
 */
export function Modal({ open, onClose, title, children, footer, danger, icon: Icon, labelledBy, role = "dialog", description, wide }: {
  open: boolean; onClose: () => void; title: ReactNode; children?: ReactNode; footer?: ReactNode; danger?: boolean; icon?: LucideIcon;
  labelledBy?: string; role?: "dialog" | "alertdialog"; description?: ReactNode; wide?: boolean;
}) {
  // Radix returns focus to a Dialog.Trigger; MSO opens dialogs from ordinary buttons, so remember the opener ourselves.
  const opener = useRef<HTMLElement | null>(null);
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="overlay fixed inset-0 z-overlay bg-black/50" />
        <DialogPrimitive.Content
          role={role}
          {...(labelledBy ? { "aria-labelledby": labelledBy } : {})}
          {...(description ? {} : { "aria-describedby": undefined })}
          onOpenAutoFocus={(e) => {
            opener.current = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null;
            const target = (e.currentTarget as HTMLElement | null)?.querySelector<HTMLElement>("[data-autofocus]");
            if (target) { e.preventDefault(); target.focus(); }
          }}
          onCloseAutoFocus={(e) => {
            if (opener.current?.isConnected) { e.preventDefault(); opener.current.focus(); }
            opener.current = null;
          }}
          className={cn(
            "dialog fixed z-overlay flex flex-col overflow-hidden border-2 bg-popover text-popover-foreground shadow-lg outline-none",
            "inset-x-0 bottom-0 max-h-[calc(100dvh-1rem)] rounded-t-xl",
            "sm:inset-auto sm:top-1/2 sm:left-1/2 sm:max-h-[calc(100dvh-2rem)] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-xl",
            wide ? "sm:w-[min(55rem,calc(100vw-2rem))]" : "sm:w-[min(35rem,calc(100vw-2rem))]",
            danger ? "dialog--danger border-destructive" : "border-border",
          )}
        >
          <div className="flex flex-none items-start gap-3 px-5 pt-5 pb-2">
            {Icon ? (
              <span className={cn("inline-grid size-10 shrink-0 place-items-center rounded-lg", danger ? "bg-down-bg text-down-ink" : "bg-muted text-foreground")}>
                <Icon className="ico size-5" aria-hidden="true" />
              </span>
            ) : null}
            <div className="min-w-0">
              <DialogPrimitive.Title className="dialog-title text-xl font-bold leading-snug">{title}</DialogPrimitive.Title>
              {description ? <DialogPrimitive.Description className="mt-1 text-base text-muted-foreground">{description}</DialogPrimitive.Description> : null}
            </div>
          </div>
          {children ? <div className="dialog-body min-h-0 flex-auto overflow-y-auto overscroll-contain px-5 pt-1.5 pb-4 text-base text-foreground">{children}</div> : null}
          {footer ? <div className="dialog-foot flex flex-none flex-wrap justify-end gap-2 border-t border-border px-5 pt-3 pb-4">{footer}</div> : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
