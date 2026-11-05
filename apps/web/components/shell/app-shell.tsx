// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/components/shell/app-shell.tsx — internally versioned copy, see PROVENANCE.md
// (MSO: sidebar health from /system/health, topbar mode chip, "Automatisering pauzeren" instead of
// the emergency stop, theme stored in localStorage `mso.theme`, no notifications/storage pill/SSE.)
"use client";

import {
  Bot, CircleCheck, Eye, LogOut, Menu, Monitor, Moon, PauseCircle, PlayCircle, Search, ShieldCheck, Sun, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Button, Dot, Kbd, Modal } from "@/components/mg/primitives";
import { StateView } from "@/components/mg/states";
import { ToastProvider, useToast } from "@/components/mg/toast";
import { BrandMark } from "@/components/shell/auth-frame";
import { ALL_NAV_ITEMS, BOTTOM_NAV_KEYS, isActive, NAV } from "@/components/shell/nav";
import { errorMessage } from "@/lib/api/client";
import type { SystemHealth, SystemInfo } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { ModeProvider, useMode } from "@/lib/mode";
import { SessionProvider, useSession } from "@/lib/session";
import { healthLabel, healthTone } from "@/lib/status";
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
  const [choice, setChoice] = useState<ThemeChoice>("dark");
  useEffect(() => {
    const read = () => setChoice((document.documentElement.dataset.themeChoice as ThemeChoice | undefined) ?? (document.documentElement.dataset.theme === "light" ? "light" : "dark"));
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
    <button type="button" className="icon-btn" data-testid="theme-toggle" onClick={() => setUserTheme(next[choice])} aria-label={label} title={label}>
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
    return <div className="auth-main" style={{ minHeight: "100vh" }}><StateView kind="loading" /></div>;
  }
  if (status === "offline") {
    return (
      <div className="auth-main" style={{ minHeight: "100vh" }}>
        <StateView kind="offline" title={t("offline.pageTitle")} body={t("offline.pageBody")} action={<Button variant="primary" onClick={() => void refresh()}>{t("action.retry")}</Button>} />
      </div>
    );
  }
  return (
    <ModeProvider>
      <ToastProvider>
        <ShellFrame>{children}</ShellFrame>
      </ToastProvider>
    </ModeProvider>
  );
}

function ShellFrame({ children }: { children: ReactNode }) {
  const healthRes = useResource<SystemHealth>("/system/health", undefined, 30_000);
  const infoRes = useResource<SystemInfo>("/system/info");
  const [drawer, setDrawer] = useState(false);
  const pathname = usePathname();
  useEffect(() => setDrawer(false), [pathname]);

  useEffect(() => {
    let local: ThemeChoice | null = null;
    try { local = window.localStorage.getItem(THEME_KEY) as ThemeChoice | null; } catch { /* ignore */ }
    applyTheme(local ?? "dark");
  }, []);

  const shell = useMemo<ShellData>(() => ({
    health: healthRes.data, healthError: Boolean(healthRes.error), info: infoRes.data, reloadHealth: healthRes.reload,
  }), [healthRes.data, healthRes.error, healthRes.reload, infoRes.data]);
  const { t } = useI18n();
  return (
    <ShellContext.Provider value={shell}>
      <a className="skip-link" href="#main">{t("app.skipToContent")}</a>
      <div className="app">
        <aside className="sidebar" aria-label={t("nav.main")}><SidebarContent /></aside>
        <div className="main">
          <Topbar onMenu={() => setDrawer(true)} />
          <PauseBanner />
          <main id="main" className="content" tabIndex={-1}>{children}</main>
        </div>
      </div>
      <BottomNav onMore={() => setDrawer(true)} moreOpen={drawer} />
      {drawer ? <NavDrawer onClose={() => setDrawer(false)} /> : null}
      <KeyboardShortcuts />
    </ShellContext.Provider>
  );
}

// ---------------------------------------------------------------- sidebar
function Brand() {
  const { t } = useI18n();
  return (
    <Link href="/" className="brand">
      <BrandMark />
      <span><div className="brand-name">{t("app.name")}</div><div className="brand-sub">{t("app.tagline")}</div></span>
    </Link>
  );
}

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { t } = useI18n();
  return (
    <nav className="nav" aria-label={t("nav.main")}>
      {NAV.map((group) => (
        <div key={group.label ?? "root"} style={{ display: "contents" }}>
          {group.label ? <div className="nav-group">{t(group.label)}</div> : null}
          {group.items.map((item) => {
            const active = isActive(item.href, pathname);
            return (
              <Link key={item.key} href={item.href} className="nav-item" aria-current={active ? "page" : undefined} onClick={onNavigate}>
                <item.icon className="ico" aria-hidden="true" />
                <span className="nav-label">{t(item.label)}</span>
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
  const tone = healthError ? "danger" : health ? healthTone(health.status) : "neutral";
  const dot = tone === "success" || tone === "warning" || tone === "danger" ? tone : "neutral";
  const label = healthError ? t("health.offline") : !health ? t("health.unknown")
    : health.status === "healthy" ? t("health.healthy") : health.status === "degraded" ? t("health.degraded") : t("health.offline");
  const problems = (health?.components ?? []).filter((c) => healthTone(c.status) !== "success");
  return (
    <>
      <Brand />
      <NavList onNavigate={onNavigate} />
      <div className="sidebar-footer" data-testid="sidebar-health">
        <Link href="/systeem" className="health-line" title={problems.map((p) => `${p.label ?? p.name}: ${p.message && p.message !== p.status ? p.message : t(healthLabel(p.status))}`).join("\n") || undefined}>
          <Dot tone={dot} /><strong>{label}</strong>
        </Link>
        {problems.slice(0, 3).map((p) => (
          <div key={p.name} className="health-line"><Dot tone={healthTone(p.status) === "danger" ? "danger" : "warning"} /><span title={p.message ?? undefined}>{p.label ?? p.name.replace(/^connector:/, "")} — {p.message && p.message !== p.status ? p.message : t(healthLabel(p.status))}</span></div>
        ))}
        {problems.length > 3 ? <div className="health-line"><span>{t("health.issues", { count: problems.length })}</span></div> : null}
        <div className="version-line"><span>{info ? t("health.version", { version: info.version }) : "—"}</span><span>{t("app.short")}</span></div>
      </div>
    </>
  );
}

function NavDrawer({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);
  // focus once when the drawer opens; shell re-renders (health polling) must not move focus again
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("button, a")?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCloseRef.current(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return (
    <>
      <div className="drawer-overlay" onClick={onClose} aria-hidden="true" />
      <div className="drawer" ref={ref} role="dialog" aria-modal="true" aria-label={t("nav.main")} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div className="row-between"><Brand /><button type="button" className="icon-btn" onClick={onClose} aria-label={t("nav.closeMenu")}><X className="ico" aria-hidden="true" /></button></div>
        <NavList onNavigate={onClose} />
      </div>
    </>
  );
}

function BottomNav({ onMore, moreOpen }: { onMore: () => void; moreOpen: boolean }) {
  const pathname = usePathname();
  const { t } = useI18n();
  const items = BOTTOM_NAV_KEYS.map((key) => ALL_NAV_ITEMS.find((i) => i.key === key)!).filter(Boolean);
  const short: Record<string, string> = { missing: t("nav.missingShort") };
  const inBottom = items.some((i) => isActive(i.href, pathname));
  return (
    <nav className="bottomnav" aria-label={t("nav.mobile")}>
      {items.map((item) => (
        <Link key={item.key} href={item.href} aria-current={isActive(item.href, pathname) ? "page" : undefined}>
          <item.icon className="ico" aria-hidden="true" /><span>{short[item.key] ?? t(item.label)}</span>
        </Link>
      ))}
      <button type="button" onClick={onMore} aria-expanded={moreOpen} aria-current={!inBottom ? "page" : undefined}>
        <Menu className="ico" aria-hidden="true" /><span>{t("nav.more")}</span>
      </button>
    </nav>
  );
}

// ---------------------------------------------------------------- topbar
function Topbar({ onMenu }: { onMenu: () => void }) {
  const { t } = useI18n();
  return (
    <header className="topbar">
      <button type="button" className="icon-btn mobile-only" onClick={onMenu} aria-label={t("nav.openMenu")}><Menu className="ico" aria-hidden="true" /></button>
      <GlobalSearch />
      <div className="topbar-spacer" />
      <div className="topbar-cluster">
        <ModeChip />
        <ThemeToggle />
        <PauseButton />
        <ProfileMenu />
      </div>
    </header>
  );
}

/** Operating mode chip: "Alleen-lezen" / "Beheermodus" / "Autopilot" (accent tone) from GET /system/mode. */
function ModeChip() {
  const { t } = useI18n();
  const { mode, paused, error } = useMode();
  const label = mode ? t(`mode.${mode}`) : error ? t("mode.unknown") : "—";
  const title = mode === "autopilot" ? (paused ? t("mode.chipTitleAutopilotPaused") : t("mode.chipTitleAutopilot"))
    : mode === "manage" ? t("mode.chipTitleManage") : mode === "read_only" ? t("mode.chipTitleReadOnly") : t("mode.unknownReason");
  const auto = mode === "autopilot";
  return (
    <Link className={cn("top-pill mode-pill", mode === "manage" && "top-pill--warning")} href="/instellingen?tab=systeem#modus" title={`${title} ${t("mode.chipChange")}`} aria-label={`${label} — ${t("mode.chipChange")}`} data-testid="mode-chip" data-mode={mode ?? "unknown"}
      style={auto ? { borderColor: "var(--mg-accent)", background: "var(--mg-accent-tint)" } : undefined}>
      {auto ? <Bot className="ico ico-sm" aria-hidden="true" style={{ color: "var(--mg-accent)" }} />
        : mode === "manage" ? <ShieldCheck className="ico ico-sm" aria-hidden="true" /> : <Eye className="ico ico-sm" aria-hidden="true" />}
      <strong>{label}</strong>
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
    <div style={{ position: "relative", flex: "0 1 440px", minWidth: 0, display: "flex" }}>
      <label className="topbar-search" style={{ flex: 1 }}>
        <Search className="ico" aria-hidden="true" />
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
        />
        <Kbd>/</Kbd>
      </label>
      {open && q.trim() ? (
        <div className="popover" style={{ top: 42, left: 0, right: 0 }} role="listbox">
          <div className="list">
            <button type="button" role="option" aria-selected="true" className="list-row list-row--hover" style={{ border: 0, background: "none", width: "100%", textAlign: "left" }} onMouseDown={go}>
              <span className="list-icon"><Search className="ico" aria-hidden="true" /></span><span className="list-title">{t("topbar.searchFor", { q: q.trim() })}</span>
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** "Automatisering pauzeren" — POST /system/pause · /system/resume (replaces MediaGuard's emergency stop). */
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
      <button type="button" className="estop estop--pause" aria-pressed={paused} onClick={() => { setError(null); setOpen(true); }} disabled={Boolean(disabledReason)} title={disabledReason ?? (paused ? t("pause.resumeTitle") : t("pause.button"))} data-testid="pause-button">
        {paused ? <PlayCircle className="ico" aria-hidden="true" /> : <PauseCircle className="ico" aria-hidden="true" />}
        <span className="estop-label">{paused ? t("pause.active") : t("pause.button")}</span>
      </button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        role="alertdialog"
        icon={paused ? PlayCircle : PauseCircle}
        title={paused ? t("pause.resumeTitle") : t("pause.confirmTitle")}
        description={paused ? t("pause.resumeBody") : t("pause.confirmBody")}
        footer={<><Button onClick={() => setOpen(false)} data-autofocus>{t("action.cancel")}</Button><Button variant="primary" pending={pending} onClick={() => void submit()} data-testid="pause-confirm">{paused ? t("pause.resume") : t("pause.confirm")}</Button></>}
      >
        {error ? <div className="field-error" role="alert">{error}</div> : null}
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
    <div className="banner banner--warning" role="status" data-testid="pause-banner">
      <PauseCircle className="ico" aria-hidden="true" />
      <span className="grow"><strong>{t("pause.bannerTitle")}</strong> {t("pause.bannerBody")}</span>
      {can("operator") ? <span className="banner-actions"><Button size="sm" icon={PlayCircle} pending={pending} onClick={() => void release()}>{t("pause.resume")}</Button></span> : null}
    </div>
  );
}

function ProfileMenu() {
  const { t } = useI18n();
  const { user, logout } = useSession();
  const [open, setOpen] = useState(false);
  const theme = useThemeChoice();
  const initials = (user?.username ?? "?").slice(0, 2).toUpperCase();
  return (
    <div style={{ position: "relative" }}>
      <button type="button" className="avatar" aria-label={t("topbar.profile")} aria-expanded={open} onClick={() => setOpen((v) => !v)} style={{ cursor: "pointer" }}>{initials}</button>
      {open ? (
        <div className="popover" style={{ right: 0, top: 42, width: 260 }} role="dialog" aria-label={t("topbar.profile")}>
          <div style={{ padding: 14 }} className="stack-sm">
            <div><strong>{user?.username}</strong><div className="t-3" style={{ fontSize: 12 }}>{t("topbar.role", { role: user ? t(`role.${user.role}`) : "—" })}</div></div>
            <hr className="divider" />
            <span className="eyebrow">{t("topbar.theme")}</span>
            <div className="segmented" role="group" aria-label={t("topbar.theme")}>
              <button type="button" aria-pressed={theme === "dark"} onClick={() => setUserTheme("dark")}><Moon className="ico ico-sm" aria-hidden="true" />{t("theme.dark")}</button>
              <button type="button" aria-pressed={theme === "light"} onClick={() => setUserTheme("light")}><Sun className="ico ico-sm" aria-hidden="true" />{t("theme.light")}</button>
              <button type="button" aria-pressed={theme === "system"} onClick={() => setUserTheme("system")}><Monitor className="ico ico-sm" aria-hidden="true" />{t("theme.system")}</button>
            </div>
            <p className="hint" style={{ margin: 0 }}>{t("topbar.themeHint")}</p>
            <hr className="divider" />
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
    <Modal open={help} onClose={() => setHelp(false)} title={t("keyboard.title")} icon={CircleCheck}>
      <dl className="kv" style={{ gridTemplateColumns: "80px 1fr", rowGap: 10 }}>
        {rows.map(([k, label]) => <div key={k} style={{ display: "contents" }}><dt><Kbd>{k}</Kbd></dt><dd>{label}</dd></div>)}
      </dl>
    </Modal>
  );
}
