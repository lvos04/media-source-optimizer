// Derived from MediaGuard Ultimate (lvos04/media-guard-ultimate@e92c2acc) apps/web/lib/api/actions.ts — internally versioned copy, see PROVENANCE.md
"use client";

import { useCallback, useState } from "react";

import { useToast } from "@/components/mg/toast";
import { api, ApiError } from "@/lib/api/client";
import { useI18n } from "@/lib/i18n";

/**
 * Fire a backend action with consistent feedback: pending state per key, success toast,
 * error toast that distinguishes "endpoint not available yet" from real failures.
 */
export function useAction() {
  const toast = useToast();
  const { t } = useI18n();
  const [pending, setPending] = useState<string | null>(null);
  const run = useCallback(async <T,>(key: string, method: "post" | "patch" | "put" | "del", path: string, body?: unknown, success?: string): Promise<T | undefined> => {
    setPending(key);
    try {
      const result = method === "del" ? await api.del<T>(path, body) : await api[method]<T>(path, body);
      if (success) toast.push("success", success);
      return result;
    } catch (error) {
      if (error instanceof ApiError && error.kind === "unavailable") {
        toast.push("info", t("state.unavailableTitle"), t("state.backendRequired", { endpoint: `${method.toUpperCase()} /api/v1${path}` }));
      } else {
        toast.push("danger", t("toast.failed", { message: error instanceof Error ? error.message : String(error) }));
      }
      return undefined;
    } finally {
      setPending(null);
    }
  }, [t, toast]);
  return { run, pending };
}
