"use client";

import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import { api, type ApiError } from "@/lib/api/client";
import type { OperatingMode, Role, SystemMode } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export interface ModeValue {
  /** null while loading or when the backend is unreachable */
  mode: OperatingMode | null;
  readOnly: boolean;
  paused: boolean;
  loading: boolean;
  error: ApiError | null;
  reload: () => Promise<void>;
  /** POST /system/pause · POST /system/resume, then reload. Throws ApiError on failure. */
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  /** PUT /system/mode (admin), then reload so every `useMode()` consumer updates. Throws ApiError on failure. */
  setMode: (mode: OperatingMode) => Promise<void>;
}

const ModeContext = createContext<ModeValue | null>(null);

/** Mounted once by the AppShell; polls `GET /api/v1/system/mode` every 30 s. */
export function ModeProvider({ children }: { children: ReactNode }) {
  const res = useResource<SystemMode>("/system/mode", undefined, 30_000);
  const { reload } = res;
  const pause = useCallback(async () => { await api.post("/system/pause"); await reload(); }, [reload]);
  const resume = useCallback(async () => { await api.post("/system/resume"); await reload(); }, [reload]);
  const setMode = useCallback(async (mode: OperatingMode) => { await api.put("/system/mode", { mode }); await reload(); }, [reload]);
  const value = useMemo<ModeValue>(() => ({
    mode: res.data?.mode ?? null,
    // unknown mode is treated as read-only: never enable a mutation we cannot vouch for
    readOnly: res.data?.mode !== "manage",
    paused: Boolean(res.data?.paused),
    loading: res.loading,
    error: res.error,
    reload,
    pause,
    resume,
    setMode,
  }), [pause, reload, res.data, res.error, res.loading, resume, setMode]);
  return <ModeContext.Provider value={value}>{children}</ModeContext.Provider>;
}

/** Operating mode (`read_only` | `manage`) + automation pause state. Must be used inside the AppShell. */
export function useMode(): ModeValue {
  const value = useContext(ModeContext);
  if (!value) throw new Error("useMode outside ModeProvider (AppShell)");
  return value;
}

export interface WriteGate { allowed: boolean; reason: string | undefined }

/**
 * Can the current user perform a mutation that changes external systems?
 * `requireManage` (default true): also requires operating mode `manage`.
 * Use `reason` as the disabled button's `title` so a disabled button always explains itself.
 *
 *   const gate = useWriteGate("operator");
 *   <Button disabled={!gate.allowed} title={gate.reason}>Toepassen</Button>
 */
export function useWriteGate(minRole: Role = "operator", requireManage = true): WriteGate {
  const { can, role } = useSession();
  const { mode, readOnly, loading } = useMode();
  const { t } = useI18n();
  if (!can(minRole)) return { allowed: false, reason: t("mode.roleReason", { role: role ? t(`role.${role}`) : "—" }) };
  if (!requireManage) return { allowed: true, reason: undefined };
  if (loading && mode === null) return { allowed: false, reason: t("mode.loadingReason") };
  if (mode === null) return { allowed: false, reason: t("mode.unknownReason") };
  if (readOnly) return { allowed: false, reason: t("mode.readOnlyReason") };
  return { allowed: true, reason: undefined };
}
