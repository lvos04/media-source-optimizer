// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/lib/session.tsx — internally versioned copy, see PROVENANCE.md
// (MSO: /setup/status instead of /auth/setup-status, `{user, csrf_token}` shape, no per-user locale.)
"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, ApiError, onUnauthorized, setCsrfToken } from "@/lib/api/client";
import type { Me, Role, SetupStatus, User } from "@/lib/api/contracts";
import { I18nProvider } from "@/lib/i18n";

type SessionStatus = "loading" | "authenticated" | "offline";

interface SessionValue {
  me: Me | null;
  user: User | null;
  status: SessionStatus;
  role: Role | null;
  /** Role check: viewer < operator < admin. */
  can: (min: Role) => boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

export const ROLE_RANK: Record<Role, number> = { viewer: 1, operator: 2, admin: 3 };
const SessionContext = createContext<SessionValue | null>(null);

/** Where an unauthenticated visitor should go: /setup on first run, otherwise /login?next=… */
export async function entryPath(next: string): Promise<string> {
  try {
    const setup = await api.get<SetupStatus>("/setup/status");
    if (setup.setup_required && !setup.admin_exists) return "/setup";
  } catch (error) {
    if (!(error instanceof ApiError) || error.kind === "offline") throw error;
  }
  return `/login?next=${encodeURIComponent(next || "/")}`;
}

/** Guards the authenticated app: resolves setup status and /auth/me, redirects to /setup or /login, keeps CSRF. */
export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<SessionStatus>("loading");

  const redirectToEntry = useCallback(async () => {
    try {
      router.replace(await entryPath(pathname || "/"));
    } catch {
      setStatus("offline");
    }
  }, [pathname, router]);

  const refresh = useCallback(async () => {
    try {
      // first run (or an unfinished setup wizard) always goes to /setup
      const setup = await api.get<SetupStatus>("/setup/status").catch((error: unknown) => {
        if (error instanceof ApiError && error.kind !== "offline") return null; // endpoint missing → ignore
        throw error;
      });
      if (setup?.setup_required) {
        router.replace("/setup");
        return;
      }
      const value = await api.get<Me>("/auth/me");
      setCsrfToken(value.csrf_token);
      setMe(value);
      setStatus("authenticated");
    } catch (error) {
      if (error instanceof ApiError && error.kind === "unauthorized") {
        await redirectToEntry();
      } else {
        setStatus("offline");
      }
    }
  }, [redirectToEntry, router]);

  useEffect(() => {
    void refresh();
    // only on mount: navigation inside the app keeps the session
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => onUnauthorized(() => {
    setMe(null);
    setCsrfToken(null);
    void redirectToEntry();
  }), [redirectToEntry]);

  const logout = useCallback(async () => {
    try { await api.post("/auth/logout"); } catch { /* session may already be gone */ }
    setCsrfToken(null);
    setMe(null);
    router.replace("/login");
  }, [router]);

  const value = useMemo<SessionValue>(() => ({
    me,
    user: me?.user ?? null,
    status,
    role: me?.user.role ?? null,
    can: (min: Role) => (me ? (ROLE_RANK[me.user.role] ?? 0) >= ROLE_RANK[min] : false),
    refresh,
    logout,
  }), [logout, me, refresh, status]);

  return (
    <SessionContext.Provider value={value}>
      <I18nProvider locale="nl-NL">{children}</I18nProvider>
    </SessionContext.Provider>
  );
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession outside SessionProvider");
  return value;
}
