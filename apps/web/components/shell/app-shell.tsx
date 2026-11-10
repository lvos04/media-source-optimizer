// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/components/shell/app-shell.tsx — internally versioned copy, see PROVENANCE.md
// (MSO: sidebar health from /system/health, topbar mode chip, "Automatisering pauzeren" instead of
// the emergency stop, theme stored in localStorage `mso.theme`, no notifications/storage pill/SSE;
// 1.6.0: "Wacht op jou" counter in the topbar, badge on Automatisering, "(N) " tab title — see ./waiting.tsx.)
// Peil fase 2a (AppSchil C-01, zonder Hub-items): sidebar ≥ 1024 px (lg), bottom navigation below; "Meer" is a sheet
// from the bottom; "Terug naar de Hub" (O-11) under the logo and as the first item of the sheet; testids and
// accessible names unchanged (sidebar-health, waiting-pill(-mobile), mode-chip, theme-toggle, pause-button).
"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  ArrowLeft, Bot, CirclePause, CirclePlay, Eye, Keyboard, LogOut, Menu, Monitor, Moon, Search, ShieldCheck, Sun, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Button, Dot, Kbd, Modal, Segmented } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { ToastProvider, useToast } from "@/components/mg/toast";
import { AutopilotHoldBanner } from "@/components/mso/automation/hold-banner";
import { BrandMark } from "@/components/shell/auth-frame";
import { ALL_NAV_ITEMS, BOTTOM_NAV_KEYS, isActive, NAV } from "@/components/shell/nav";
import { WaitingNavBadge, WaitingPill, WaitingProvider } from "@/components/shell/waiting";
import { errorMessage } from "@/lib/api/client";
import type { SystemHealth, SystemInfo } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { ModeProvider, useMode } from "@/lib/mode";
import { SessionProvider, useSession } from "@/lib/session";
import { BARE_STATUS_WORDS, healthLabel, healthTone, isHealthProblem } from "@/lib/status";
import { buttonVariants } from "@/components/ui/button";
import { HUB_URL_SETTING, hubUrl } from "@/lib/hub";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------- shell data (health/info)
interface ShellData { health: SystemHealth | null; healthError: boolean; info: SystemInfo | null; reloadHealth: () => Promise<void> }
const ShellContext = createContext<ShellData>({ health: null, healthError: false, info: null, reloadHealth: async () => undefined });
/** System health (`GET /system/health`, polled every 30 s) and info (`GET /system/info`) for any page. */
export const useShell = () => useContext(ShellContext);

// ---------------------------------------------------------------- theme
type ThemeChoice = "dark" | "light" | "system";
const THEME_KEY = "mso.theme";
let systemListener: ((e: MediaQueryListEvent) => void) | null = null;
function applyTheme(choice: ThemeChoice) {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const dark = choice === "dark" || (choice === "system" && mq.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.dataset.themeChoice = choice;
  if (systemListener) mq.removeEventListener("change", systemListener);
  systemListener = null;
  if (choice === "system") {
    systemListener = (e) => { document.documentElement.dataset.theme = e.matches ? "dark" : "light"; };
    mq.addEventListener("change", systemListener);
  }
  window.dispatchEvent(new CustomEvent("mso:theme", { detail: choice }));
}
function setUserTheme(choice: ThemeChoice) {
  try { window.localStorage.setItem(THEME_KEY, choice); } catch { /* storage may be blocked */ }
  applyTheme(choice);
}
function useThemeChoice(): ThemeChoice {
  const [choice, setChoice] = useState<ThemeChoice>("system");
  useEffect(() => {
    const read = () => setChoice((document.documentElement.dataset.themeChoice as ThemeChoice | undefined) ?? "system");
    read();
    window.addEventListener("mso:theme", read);
    return () => window.removeEventListener("mso:theme", read);
  }, []);
  return choice;
}

function ThemeToggle() {
  const { t } = useI18n();
  const choice = useThemeChoice();
  const next: Record<ThemeChoice, ThemeChoice> = { dark: "light", light: "system", system: "dark" };
  const Icon = choice === "dark" ? Moon : choice === "light" ? Sun : Monitor;
  const label = t("topbar.themeToggle", { current: t(`theme.${choice}`), next: t(`theme.${next[choice]}`) });
  return (
    <button type="button" className={buttonVariants({ variant: "stil", size: "icoon" })} data-testid="theme-toggle" onClick={() => setUserTheme(next[choice])} aria-label={label} title={label}>
      <Icon className="ico" aria-hidden="true" />
    </button>
  );
}

// ---------------------------------------------------------------- shell
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <ShellGate>{children}</ShellGate>
    </SessionProvider>
  );
}

function ShellGate({ children }: { children: ReactNode }) {
  const { status, refresh } = useSession();
  const { t } = useI18n();
  if (status === "loading") {
    return <div className="flex min-h-screen items-center justify-center px-4 py-8"><StateView kind="loading" /></div>;
  }
  if (status === "offline") {
    return (
      <main className="flex min-h-screen items-center justify-center px-4 py-8">
        <StateView kind="offline" pageLevel title={t("offline.pageTitle")} body={t("offline.pageBody")} action={<Button variant="primary" onClick={() => void refresh()}>{t("action.retry")}</Button>} />
      </main>
    );
  }
  return (
    <ModeProvider>
      <ToastProvider>
        <WaitingProvider><ShellFrame>{children}</ShellFrame></WaitingProvider>
      </ToastProvider>
    </ModeProvider>
  );
}

function ShellFrame({ children }: { children: ReactNode }) {
  const healthRes = useResource<SystemHealth>("/system/health", undefined, 30_000);
  const infoRes = useResource<SystemInfo>("/system/info");
  const [more, setMore] = useState(false);
  const pathname = usePathname();
  useEffect(() => setMore(false), [pathname]);

  useEffect(() => {
    let local: ThemeChoice | null = null;
    try { local = window.localStorage.getItem(THEME_KEY) as ThemeChoice | null; } catch { /* ignore */ }
    applyTheme(local === "dark" || local === "light" ? local : "system");
  }, []);

  const shell = useMemo<ShellData>(() => ({
    health: healthRes.data, healthError: Boolean(healthRes.error), info: infoRes.data, reloadHealth: healthRes.reload,
  }), [healthRes.data, healthRes.error, healthRes.reload, infoRes.data]);
  const { t } = useI18n();
  return (
    <ShellContext.Provider value={shell}>
      <a className="skip-link focus-ring fixed top-[-4rem] left-3 z-toast rounded-lg bg-primary px-3 py-2.5 font-semibold text-primary-foreground no-underline focus:top-3" href="#main">{t("app.skipToContent")}</a>
      <div className="app min-h-screen lg:grid lg:grid-cols-[16rem_minmax(0,1fr)]">
        <aside className="sidebar sticky top-0 hidden h-screen flex-col gap-3 overflow-y-auto overscroll-contain border-r border-border bg-sidebar px-3 pt-4 pb-3 lg:flex" aria-label={t("nav.main")}>
          <SidebarContent />
        </aside>
        <div className="main flex min-w-0 flex-col">
          <Topbar onMenu={() => setMore(true)} />
          <PauseBanner />
          <AutopilotHoldBanner />
          <main id="main" className="content mx-auto w-full max-w-[95rem] px-4 pt-4 pb-[calc(var(--bottomnav-h)+1.75rem)] outline-none lg:px-6 lg:pt-6 lg:pb-12" tabIndex={-1}>{children}</main>
        </div>
      </div>
      <BottomNav onMore={() => setMore(true)} moreOpen={more} />
      <MoreSheet open={more} onClose={() => setMore(false)} />
      <KeyboardShortcuts />
    </ShellContext.Provider>
  );
}

// ---------------------------------------------------------------- sidebar
function Brand() {
  const { t } = useI18n();
  return (
    <Link href="/" className="brand focus-ring flex min-h-touch items-center gap-2.5 rounded-lg px-2 text-foreground no-underline">
      <BrandMark />
      <span className="min-w-0">
        <span className="block text-base leading-tight font-bold">{t("app.name")}</span>
        <span className="block text-sm leading-tight text-muted-foreground">{t("app.tagline")}</span>
      </span>
    </Link>
  );
}

/**
 * "Terug naar de Hub" (Peil O-11): only when `systeem.hub_url` holds a valid http(s) address (same rules as the
 * core validator). Unknown, empty, invalid or not readable → nothing in the DOM (no empty spot, no disabled button).
 * Same tab: it is the way back, not a deep link.
 */
export function HubLink({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useI18n();
  const res = useResource<{ values: Record<string, unknown> }>("/settings");
  const href = hubUrl(res.data?.values?.[HUB_URL_SETTING]);
  if (!href) return null;
  return (
    <a href={href} rel="noopener" onClick={onNavigate} data-testid="hub-link"
      className={cn(buttonVariants({ variant: "secundair" }), "w-full justify-start")}>
      <ArrowLeft className="ico" aria-hidden="true" />{t("nav.backToHub")}
    </a>
  );
}

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { t } = useI18n();
  return (
    <nav className="nav flex flex-col gap-0.5" aria-label={t("nav.main")}>
      {NAV.map((group) => (
        <div key={group.label ?? "root"} className="contents">
          {group.label ? <div className="mt-3 mb-1 px-3 text-sm font-semibold text-muted-foreground">{t(group.label)}</div> : null}
          {group.items.map((item) => {
            const active = isActive(item.href, pathname);
            return (
              <Link key={item.key} href={item.href} aria-current={active ? "page" : undefined} onClick={onNavigate}
                className={cn("nav-item focus-ring relative flex min-h-touch items-center gap-3 rounded-lg px-3 text-base text-foreground no-underline hover:bg-accent",
                  "aria-[current=page]:bg-accent aria-[current=page]:font-semibold",
                  "aria-[current=page]:before:absolute aria-[current=page]:before:inset-y-2 aria-[current=page]:before:-left-3 aria-[current=page]:before:w-1 aria-[current=page]:before:rounded-r aria-[current=page]:before:bg-primary")}>
                <item.icon className="ico size-5 shrink-0" aria-hidden="true" />
                <span className="min-w-0 flex-1 break-words">{t(item.label)}</span>
                {item.key === "automation" ? <WaitingNavBadge /> : null}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useI18n();
  const { health, healthError, info } = useShell();
  // Peil total status (T-01a): worst component; a failed fetch is "Onbekend — MSO-core reageert niet", never "Storing"
  // without a measurement.
  const tone = healthError ? "unknown" : health ? healthTone(health.status) : "unknown";
  const dot = tone === "healthy" || tone === "warning" || tone === "danger" ? tone : "unknown";
  const label = healthError ? t("health.offline") : !health ? t("health.unknown")
    : health.status === "healthy" ? t("health.healthy") : health.status === "degraded" ? t("health.degraded")
      : health.status === "offline" ? t("health.down") : t("health.unknown");
  const problems = (health?.components ?? []).filter((c) => isHealthProblem(c.status));
  const reason = (p: { status: string; message?: string | null }) => (p.message && !BARE_STATUS_WORDS.has(p.message.trim().toLowerCase()) && p.message !== p.status ? p.message : t(healthLabel(p.status)));
  return (
    <>
      <Brand />
      <HubLink onNavigate={onNavigate} />
      <NavList onNavigate={onNavigate} />
      <div className="sidebar-footer mt-auto flex flex-col gap-1 border-t border-border px-2 pt-2.5 text-sm" data-testid="sidebar-health">
        <Link href="/systeem" className="health-line focus-ring flex min-h-touch items-center gap-2 rounded-lg text-foreground no-underline" title={problems.map((p) => `${p.label ?? p.name}: ${reason(p)}`).join("\n") || undefined}>
          <Dot tone={dot} /><strong className="font-semibold">{label}</strong>
        </Link>
        {problems.slice(0, 3).map((p) => (
          <div key={p.name} className="health-line flex items-start gap-2 text-muted-foreground">
            <span className="mt-0.5"><Dot tone={healthTone(p.status) === "danger" ? "danger" : healthTone(p.status) === "warning" ? "warning" : "unknown"} /></span>
            <span title={p.message ?? undefined} className="min-w-0 break-words">{p.label ?? p.name.replace(/^connector:/, "")} — {reason(p)}</span>
          </div>
        ))}
        {problems.length > 3 ? <div className="text-muted-foreground">{t("health.issues", { count: problems.length })}</div> : null}
        <div className="flex justify-between pt-1 text-muted-foreground"><span>{info ? t("health.version", { version: info.version }) : "—"}</span><span>{t("app.short")}</span></div>
      </div>
    </>
  );
}

/** "Meer" (< 1024 px): a sheet from the bottom with the way back to the Hub first, then the full navigation. */
function MoreSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="drawer-overlay fixed inset-0 z-overlay bg-black/50 lg:hidden" />
        <DialogPrimitive.Content aria-describedby={undefined}
          className="drawer fixed inset-x-0 top-auto bottom-0 z-overlay w-auto animate-none flex max-h-[calc(100dvh-2rem)] flex-col gap-3 overflow-y-auto overscroll-contain rounded-t-xl border-2 border-border bg-sidebar px-3 pt-3 pb-[calc(env(safe-area-inset-bottom)+1rem)] shadow-lg outline-none lg:hidden">
          <div className="flex items-center justify-between gap-2">
            <DialogPrimitive.Title className="px-2 text-lg font-bold">{t("nav.more")}</DialogPrimitive.Title>
            <DialogPrimitive.Close className={buttonVariants({ variant: "stil", size: "icoon" })} aria-label={t("nav.closeMenu")}>
              <X className="ico" aria-hidden="true" />
            </DialogPrimitive.Close>
          </div>
          <HubLink onNavigate={onClose} />
          <NavList onNavigate={onClose} />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** Zachte afbreekstreepjes op Nederlandse lettergrepen voor de smalle ondernavigatie (alleen weergave). */
const LETTERGREPEN: Record<string, string> = { Overzicht: "Over\u00ADzicht", Downloads: "Down\u00ADloads", Ontbrekend: "Ont\u00ADbre\u00ADkend" };
function lettergrepen(label: string): string {
  return LETTERGREPEN[label] ?? label;
}

function BottomNav({ onMore, moreOpen }: { onMore: () => void; moreOpen: boolean }) {
  const pathname = usePathname();
  const { t } = useI18n();
  const items = BOTTOM_NAV_KEYS.map((key) => ALL_NAV_ITEMS.find((i) => i.key === key)!).filter(Boolean);
  const short: Record<string, string> = { missing: t("nav.missingShort") };
  const inBottom = items.some((i) => isActive(i.href, pathname));
  // Review stap 5: vijf gelijke kolommen van 64 px braken op 320 px midden in een woord ("Downlo-ads"). Nu krijgt elk
  // item zijn eigen tekstbreedte (flex-auto, min-width = min-content: de langste lettergreep, en ≥ 44 px) en breekt een label alleen op een
  // zacht afbreekstreepje (Nederlandse lettergrepen). Vanaf 360 px past alles op één regel.
  // Peil 1.1.1 (E16, COMPONENTEN C-01): actief = balk van 3 px in --primary bovenaan + inkt + aria-current, zonder extra
  // gewicht (dus niet alleen kleur, WCAG 1.4.1). De balk is een ::before, zodat de focusring (box-shadow) blijft staan;
  // in forced colors onderstreept de Peil-basislaag het actieve item.
  const item = cn("focus-ring relative flex min-h-bottomnav min-w-min flex-auto flex-col items-center justify-center gap-0.5 border-0 bg-transparent px-0.5 text-center text-sm leading-tight font-medium text-muted-foreground no-underline [hyphens:manual]",
    "aria-[current=page]:text-foreground aria-[current=page]:before:absolute aria-[current=page]:before:inset-x-0 aria-[current=page]:before:top-0 aria-[current=page]:before:h-[3px] aria-[current=page]:before:bg-primary [&_svg]:size-6");
  return (
    <nav className="bottomnav fixed inset-x-0 bottom-0 z-nav flex border-t border-border bg-card pb-[env(safe-area-inset-bottom)] lg:hidden" aria-label={t("nav.mobile")}>
      {items.map((it) => (
        <Link key={it.key} href={it.href} className={item} aria-current={isActive(it.href, pathname) ? "page" : undefined}>
          <it.icon className="ico" aria-hidden="true" /><span className="min-w-10">{lettergrepen(short[it.key] ?? t(it.label))}</span>
        </Link>
      ))}
      <button type="button" className={item} onClick={onMore} aria-expanded={moreOpen} aria-current={!inBottom ? "page" : undefined}>
        <Menu className="ico" aria-hidden="true" /><span className="min-w-10">{t("nav.more")}</span>
      </button>
    </nav>
  );
}

// ---------------------------------------------------------------- topbar
function Topbar({ onMenu }: { onMenu: () => void }) {
  const { t } = useI18n();
  return (
    // < 640 px the search field takes its own row under the icons (44 px targets for everything, no overflow at 320 px)
    <header className="topbar sticky top-0 z-nav flex min-h-header flex-wrap items-center gap-x-1 gap-y-1.5 border-b border-border bg-background px-3 py-1.5 sm:flex-nowrap sm:gap-2 lg:gap-3 lg:px-6 lg:py-0">
      <button type="button" className={cn(buttonVariants({ variant: "stil", size: "icoon" }), "lg:hidden")} onClick={onMenu} aria-label={t("nav.openMenu")}><Menu className="ico" aria-hidden="true" /></button>
      <GlobalSearch />
      <div className="hidden flex-1 lg:block" />
      <div className="ml-auto flex shrink-0 items-center gap-1 lg:gap-2">
        <WaitingPill />
        <ModeChip />
        <ThemeToggle />
        <PauseButton />
        <ProfileMenu />
      </div>
    </header>
  );
}

/** Operating mode: a neutral label (eye / shield-check / bot); "Modus onbekend" in the unknown shape. Icon only < lg. */
function ModeChip() {
  const { t } = useI18n();
  const { mode, paused, error } = useMode();
  const label = mode ? t(`mode.${mode}`) : error ? t("mode.unknown") : "—";
  const title = mode === "autopilot" ? (paused ? t("mode.chipTitleAutopilotPaused") : t("mode.chipTitleAutopilot"))
    : mode === "manage" ? t("mode.chipTitleManage") : mode === "read_only" ? t("mode.chipTitleReadOnly") : t("mode.unknownReason");
  const Icon = mode === "autopilot" ? Bot : mode === "manage" ? ShieldCheck : Eye;
  return (
    <Link href="/instellingen?tab=systeem#modus" title={`${title} ${t("mode.chipChange")}`} aria-label={`${label} — ${t("mode.chipChange")}`} data-testid="mode-chip" data-mode={mode ?? "unknown"}
      className={cn("mode-pill focus-ring inline-flex min-h-touch min-w-touch items-center justify-center gap-1.5 rounded-full border-2 px-2.5 text-sm font-semibold text-foreground no-underline hover:bg-accent lg:px-3",
        mode === null ? "border-dashed border-unknown bg-unknown-bg text-unknown-ink" : "border-border-strong bg-card")}>
      <Icon className="ico size-[1.125rem]" aria-hidden="true" />
      <span className="hidden lg:inline">{label}</span>
    </Link>
  );
}

function GlobalSearch() {
  const { t } = useI18n();
  const router = useRouter();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const go = () => {
    const term = q.trim();
    if (!term) return;
    setOpen(false);
    setQ("");
    router.push(`/zoeken?q=${encodeURIComponent(term)}`);
  };
  return (
    <div className="relative order-last flex min-w-0 basis-full sm:order-none sm:flex-1 sm:basis-auto lg:max-w-[28rem]">
      <label className="topbar-search flex min-h-touch min-w-0 flex-1 items-center gap-2 rounded-lg border-2 border-input bg-card px-2.5 text-muted-foreground focus-within:border-ring">
        <Search className="ico size-5 shrink-0" aria-hidden="true" />
        <span className="sr-only">{t("topbar.searchLabel")}</span>
        <input
          data-global-search
          value={q}
          placeholder={t("topbar.search")}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => window.setTimeout(() => setOpen(false), 150)}
          onKeyDown={(e) => { if (e.key === "Enter") go(); if (e.key === "Escape") (e.target as HTMLInputElement).blur(); }}
          aria-autocomplete="list"
          className="focus-ring min-h-touch min-w-0 flex-1 rounded-sm border-0 bg-transparent text-base text-foreground"
        />
        <span className="hidden lg:inline-grid"><Kbd>/</Kbd></span>
      </label>
      {open && q.trim() ? (
        <div className="absolute inset-x-0 top-full z-overlay mt-1 rounded-lg border-2 border-border bg-popover shadow-md" role="listbox">
          <button type="button" role="option" aria-selected="true" className="focus-ring flex min-h-touch w-full items-center gap-2.5 rounded-lg border-0 bg-transparent px-3 text-left text-base text-foreground hover:bg-accent" onMouseDown={go}>
            <Search className="ico size-5" aria-hidden="true" /><span className="min-w-0 break-words">{t("topbar.searchFor", { q: q.trim() })}</span>
          </button>
        </div>
      ) : null}
    </div>
  );
}

/** "Automatisering pauzeren" — POST /system/pause · /system/resume. Peil: Gepauzeerd = info (paused tokens), not amber. */
function PauseButton() {
  const { t } = useI18n();
  const { paused, mode, error: modeError, pause, resume } = useMode();
  const { can } = useSession();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const known = mode !== null;
  const disabledReason = !can("operator") ? t("state.readOnly") : !known ? (modeError ? t("mode.unknownReason") : t("mode.loadingReason")) : undefined;
  const submit = async () => {
    setPending(true);
    setError(null);
    try {
      if (paused) await resume(); else await pause();
      toast.push("success", paused ? t("pause.resumed") : t("pause.done"));
      setOpen(false);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  };
  return (
    <>
      <button type="button" aria-pressed={paused} onClick={() => { setError(null); setOpen(true); }} disabled={Boolean(disabledReason)} title={disabledReason ?? (paused ? t("pause.resumeTitle") : t("pause.button"))} data-testid="pause-button"
        className={cn(buttonVariants({ variant: "secundair" }), "estop min-w-touch px-2.5 lg:px-3",
          paused ? "border-paused bg-paused text-primary-foreground hover:bg-paused" : "border-paused bg-paused-bg text-paused-ink hover:bg-paused-bg")}>
        {paused ? <CirclePlay className="ico" aria-hidden="true" /> : <CirclePause className="ico" aria-hidden="true" />}
        <span className="estop-label sr-only lg:not-sr-only lg:text-sm">{paused ? t("pause.active") : t("pause.button")}</span>
      </button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        role="alertdialog"
        icon={paused ? CirclePlay : CirclePause}
        title={paused ? t("pause.resumeTitle") : t("pause.confirmTitle")}
        description={paused ? t("pause.resumeBody") : t("pause.confirmBody")}
        footer={<><Button onClick={() => setOpen(false)} data-autofocus>{t("action.cancel")}</Button><Button variant="primary" pending={pending} onClick={() => void submit()} data-testid="pause-confirm">{paused ? t("pause.resume") : t("pause.confirm")}</Button></>}
      >
        {error ? <div className="field-error flex items-center gap-1.5 text-sm font-semibold text-down-ink" role="alert">{error}</div> : null}
      </Modal>
    </>
  );
}

function PauseBanner() {
  const { t } = useI18n();
  const { paused, resume } = useMode();
  const { can } = useSession();
  const toast = useToast();
  const [pending, setPending] = useState(false);
  if (!paused) return null;
  const release = async () => {
    setPending(true);
    try { await resume(); toast.push("success", t("pause.resumed")); } catch (e) { toast.push("danger", t("toast.failed", { message: errorMessage(e) })); } finally { setPending(false); }
  };
  return (
    <div className="banner flex flex-wrap items-center gap-2.5 border-b-2 border-paused bg-paused-bg px-4 py-2 text-base text-paused-ink" role="status" data-testid="pause-banner">
      <CirclePause className="ico size-5 shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1"><strong>{t("pause.bannerTitle")}</strong> {t("pause.bannerBody")}</span>
      {can("operator") ? <span className="flex gap-2"><Button size="sm" icon={CirclePlay} pending={pending} onClick={() => void release()}>{t("pause.resume")}</Button></span> : null}
    </div>
  );
}

/** Profile: initials in the accessible name (WCAG 2.5.3) + ThemaKeuze Systeem / Licht / Donker (Peil, standard Systeem). */
function ProfileMenu() {
  const { t } = useI18n();
  const { user, logout } = useSession();
  const [open, setOpen] = useState(false);
  const theme = useThemeChoice();
  const initials = (user?.username ?? "?").slice(0, 2).toUpperCase();
  return (
    <div className="relative">
      <button type="button" className="avatar focus-ring inline-grid size-touch cursor-pointer place-items-center rounded-full border-2 border-border-strong bg-muted text-sm font-bold text-foreground" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {initials}<span className="sr-only"> {t("topbar.profileSr")}</span>
      </button>
      {open ? (
        <div className="absolute top-full right-0 z-overlay mt-1 w-[min(18rem,calc(100vw-1.5rem))] rounded-lg border-2 border-border bg-popover p-3.5 shadow-md" role="dialog" aria-label={t("topbar.profile")}>
          <div className="flex flex-col gap-2.5">
            <div><strong>{user?.username}</strong><div className="text-sm text-muted-foreground">{t("topbar.role", { role: user ? t(`role.${user.role}`) : "—" })}</div></div>
            <hr className="border-border" />
            <span className="text-sm font-semibold">{t("topbar.theme")}</span>
            <Segmented label={t("topbar.theme")} value={theme} onChange={setUserTheme} options={[
              { value: "system", label: <><Monitor className="size-[1.125rem]" aria-hidden="true" />{t("theme.system")}</> },
              { value: "light", label: <><Sun className="size-[1.125rem]" aria-hidden="true" />{t("theme.light")}</> },
              { value: "dark", label: <><Moon className="size-[1.125rem]" aria-hidden="true" />{t("theme.dark")}</> },
            ]} />
            <p className="hint m-0 text-sm text-muted-foreground">{t("topbar.themeHint")}</p>
            <hr className="border-border" />
            <Button variant="ghost" icon={LogOut} onClick={() => void logout()}>{t("topbar.logout")}</Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- keyboard
function KeyboardShortcuts() {
  const { t } = useI18n();
  const router = useRouter();
  const [help, setHelp] = useState(false);
  const pendingG = useRef(0);
  const goMap = useMemo(() => Object.fromEntries(ALL_NAV_ITEMS.filter((i) => i.shortcut).map((i) => [i.shortcut!.split(" ")[1], i.href])), []);
  const onKey = useCallback((event: KeyboardEvent) => {
    const target = event.target as HTMLElement | null;
    if (target?.closest("input, textarea, select, [contenteditable=true]") || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === "/") { event.preventDefault(); document.querySelector<HTMLInputElement>("[data-global-search]")?.focus(); return; }
    if (event.key === "?") { setHelp((v) => !v); return; }
    if (event.key === "g") { pendingG.current = Date.now(); return; }
    if (Date.now() - pendingG.current < 1200 && goMap[event.key]) { pendingG.current = 0; router.push(goMap[event.key]); }
  }, [goMap, router]);
  useEffect(() => {
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onKey]);
  const rows: Array<[string, string]> = [
    ["/", t("keyboard.search")], ["?", t("keyboard.help")], ["Esc", t("keyboard.close")],
    ...ALL_NAV_ITEMS.filter((i) => i.shortcut).map((i): [string, string] => [i.shortcut!, t("keyboard.go", { page: t(i.label) })]),
  ];
  return (
    <Modal open={help} onClose={() => setHelp(false)} title={t("keyboard.title")} icon={Keyboard}>
      <dl className="inline-grid w-full grid-cols-[5rem_minmax(0,1fr)] gap-x-4 gap-y-2.5 text-base">
        {rows.map(([k, label]) => <div key={k} className="contents"><dt><Kbd>{k}</Kbd></dt><dd className="m-0">{label}</dd></div>)}
      </dl>
    </Modal>
  );
}
