// 1.6.0 WP-G1 (C06): one global, in-app signal that something waits for you — topbar counter (also ≤ 860 px), badge
// on Automatisering and a "(N) " prefix in the browser tab title. One source: GET /api/v1/overview/waiting (polled
// every 60 s and on every page change), shared with the "Wacht op jou" card on Overzicht so all numbers agree.
"use client";

import { Hand } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";

import type { WaitingInbox } from "@/lib/api/calm";
import { useResource, type Resource } from "@/lib/api/hooks";
import { HOLD_CHANGED_EVENT } from "@/lib/api/safety";
import { useI18n } from "@/lib/i18n";

const WaitingContext = createContext<Resource<WaitingInbox> | null>(null);
const TITLE_PREFIX = /^\(\d+\)\s+/;
export const WAITING_HREF = "/#wacht-op-jou";
export const WAITING_CHANGED_EVENT = "mso:waiting-changed";

/** Tell the shared inbox (topbar counter, nav badge, tab title, "Wacht op jou") to reload now. */
export function notifyWaitingChanged() {
  window.dispatchEvent(new Event(WAITING_CHANGED_EVENT));
}

/** Mounted once by the AppShell (inside the session). */
export function WaitingProvider({ children }: { children: ReactNode }) {
  const res = useResource<WaitingInbox>("/overview/waiting", undefined, 60_000);
  const pathname = usePathname();
  const first = useRef(true);
  const { reload } = res;
  // an approval or apply on another page changes the inbox: refresh on every page change (not on the first render)
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    void reload();
  }, [pathname, reload]);
  // "Hervatten" (noodrem) or an inline rollback changes the inbox right away: no waiting for the 60 s poll
  useEffect(() => {
    const on = () => void reload();
    window.addEventListener(HOLD_CHANGED_EVENT, on);
    window.addEventListener(WAITING_CHANGED_EVENT, on);
    return () => {
      window.removeEventListener(HOLD_CHANGED_EVENT, on);
      window.removeEventListener(WAITING_CHANGED_EVENT, on);
    };
  }, [reload]);
  useTitlePrefix(res.data?.decisions ?? 0);
  return <WaitingContext.Provider value={res}>{children}</WaitingContext.Provider>;
}

/** The shared inbox resource (null outside the AppShell). */
export function useWaiting(): Resource<WaitingInbox> | null {
  return useContext(WaitingContext);
}

/** Browser tab title "(N) Overzicht · MSO" while N decisions wait; Next.js replaces the title on navigation, so the
 *  prefix is re-applied whenever the <title> changes. */
function useTitlePrefix(n: number) {
  useEffect(() => {
    const apply = () => {
      const base = document.title.replace(TITLE_PREFIX, "");
      const next = n > 0 ? `(${n}) ${base}` : base;
      if (document.title !== next) document.title = next;
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(document.head, { subtree: true, childList: true, characterData: true });
    return () => {
      observer.disconnect();
      const base = document.title.replace(TITLE_PREFIX, "");
      if (document.title !== base) document.title = base;
    };
  }, [n]);
}

/** Topbar: a pill "3 wacht op jou" on wide screens, an icon with a counter on phones. Nothing when 0. */
export function WaitingPill() {
  const { t, num } = useI18n();
  const res = useWaiting();
  const n = res?.data?.decisions ?? 0;
  if (!n) return null;
  const label = t("calm.pillLabel", { count: num(n) });
  const title = t("calm.pillTitle", { count: num(n) });
  return (
    <>
      {/* Peil: a counter is an ink pill (never amber/red); the name is part of the accessible text. ≥ 1024 px a pill,
          below an icon button (44 px) with the count. */}
      <Link className="focus-ring hidden min-h-touch items-center gap-1.5 rounded-full border-2 border-border-strong bg-card px-3 text-sm text-foreground no-underline hover:bg-accent lg:inline-flex" href={WAITING_HREF} title={title} data-testid="waiting-pill">
        <Hand className="ico size-[1.125rem]" aria-hidden="true" /><span><strong className="cijfers">{num(n)}</strong> {t("autopilot.waitTitle").toLowerCase()}</span>
      </Link>
      <Link className="focus-ring relative inline-grid size-touch place-items-center rounded-lg text-foreground hover:bg-accent lg:hidden" href={WAITING_HREF} title={title} aria-label={label} data-testid="waiting-pill-mobile">
        <Hand className="ico size-5" aria-hidden="true" />
        <span className="absolute top-0.5 right-0 inline-grid h-5 min-w-5 place-items-center rounded-full bg-foreground px-1 text-sm leading-none font-bold text-background" aria-hidden="true">{n > 99 ? "99+" : n}</span>
      </Link>
    </>
  );
}

/** Sidebar/drawer badge for Automatisering. */
export function WaitingNavBadge() {
  const { t, num } = useI18n();
  const res = useWaiting();
  const n = res?.data?.decisions ?? 0;
  if (!n) return null;
  // the count is read with the link text ("Automatisering 3 wacht op jou"); no aria-label on a plain span (axe)
  return (
    <span className="cijfers inline-grid h-6 min-w-6 place-items-center rounded-full bg-foreground px-1.5 text-sm font-bold text-background" title={t("calm.navBadge", { count: num(n) })} data-testid="nav-waiting-badge">
      {n > 99 ? "99+" : num(n)}<span className="sr-only"> {t("autopilot.waitTitle").toLowerCase()}</span>
    </span>
  );
}
